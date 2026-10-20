"""Установка только собственного bundle, с проверяемым staging и обратимым переключением.

Workspace, Transfers и WebView2 не входят в границу записи этого модуля.
Win32 оболочка отдельно владеет ярлыком и записью удаления текущего пользователя.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep

from app.distribution.contracts import PackageManifest
from app.distribution.preflight import MANIFEST_NAME, check_package, manifest_identity

INSTALLED_NAME = "GreenAtlas.bundle"


class BundleAccessError(ValueError):
    """Ожидаемый отказ Windows, без частного пути пользователя в сообщении."""

    def __init__(self, winerror: int) -> None:
        self.winerror = winerror
        super().__init__(
            f"Windows не разрешила изменить файлы установки Green Atlas (Win32 {winerror}). "
            "Файлы могут быть открыты другой программой или антивирусом либо доступ запрещён. "
            "Закройте Green Atlas и AutoCAD, дождитесь завершения проверки файлов и повторите. "
            "Установка или удаление не завершены; проекты хранятся отдельно."
        )


_REPLACE_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8)
_BUNDLE_ACCESS_ERRORS = frozenset({5, 32, 33})


@dataclass(frozen=True)
class InstallReceipt:
    action: str
    installed: str | None
    backup: str | None
    package_sha256: str
    workspace_preserved: bool = True
    cleanup_pending: tuple[str, ...] = ()
    removed_program_copies: tuple[str, ...] = ()


def plain_directory(path: Path, *, create: bool = False) -> Path:
    """Запретить ссылки, reparse points и файлы на каждом уровне пути."""
    path = path.absolute()
    chain = list(reversed((path, *path.parents)))
    for part in chain:
        try:
            info = part.lstat()
        except FileNotFoundError:
            if not create:
                raise
            part.mkdir(mode=0o700)
            info = part.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Каталог установки содержит ссылку или чужой тип: " + str(part))
    return path


def _present(path: Path) -> bool:
    """Существующий dangling reparse point также остаётся чужим занятым именем."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _directory_identity(path: Path) -> dict[str, int]:
    plain_directory(path)
    info = path.stat()
    if not info.st_ino:
        raise ValueError("Файловая система не сообщает идентичность каталога установки")
    birth = getattr(info, "st_birthtime_ns", int(getattr(info, "st_birthtime", 0) * 1_000_000_000))
    return {"device": info.st_dev, "inode": info.st_ino, "birth_ns": birth}


def _replace_bundle_directory(source: Path, destination: Path) -> None:
    """Повторить только отказ атомарного переноса собственного соседнего каталога."""
    if source.parent != destination.parent:
        raise ValueError("Перенос комплекта допускает только соседние каталоги")
    root_identity = _directory_identity(source.parent)
    source_identity = _directory_identity(source)
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        if (
            _directory_identity(source.parent) != root_identity
            or _directory_identity(source) != source_identity
            or _present(destination)
        ):
            raise ValueError("Каталоги установки изменились перед переносом; файлы сохранены")
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            code = getattr(error, "winerror", None)
            if code not in _BUNDLE_ACCESS_ERRORS:
                raise
            if attempt == len(_REPLACE_RETRY_DELAYS):
                raise BundleAccessError(code) from error
            sleep(_REPLACE_RETRY_DELAYS[attempt])


def load_owned_bundle(path: Path) -> PackageManifest:
    plain_directory(path)
    manifest_file = path / MANIFEST_NAME
    try:
        info = manifest_file.lstat()
    except FileNotFoundError as error:
        raise ValueError(
            "В GreenAtlas.bundle отсутствует distribution-manifest.json. "
            "Если это неполная прежняя установка, её файлы нужно сохранить перед заменой."
        ) from error
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Манифест установленного комплекта содержит ссылку")
    manifest = PackageManifest.model_validate_json(manifest_file.read_bytes())
    if manifest.layout.platform != "windows":
        raise ValueError("Установщик принимает только Windows комплект")
    check_package(path, manifest)
    return manifest


def incomplete_legacy_bundle(path: Path) -> bool:
    """Recognise an older Green Atlas install without treating arbitrary data as ours.

    The old installer could leave PackageContents.xml and the ARX in place before
    writing distribution-manifest.json. Keep its bytes in a separate directory;
    never try to validate or delete that incomplete installation as a package.
    """
    plain_directory(path)
    if _present(path / MANIFEST_NAME):
        return False
    registration = path / "PackageContents.xml"
    binary = path / "Contents/Windows/GreenAtlasBridge.arx"
    if not _present(registration) or not _present(binary):
        return False
    plain_directory(binary.parent)
    for candidate in (registration, binary):
        info = candidate.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return False
    if registration.stat().st_size > 64 * 1024:
        return False
    from xml.etree import ElementTree

    try:
        package = ElementTree.fromstring(registration.read_bytes())
    except ElementTree.ParseError:
        return False
    return (
        package.tag == "ApplicationPackage"
        and package.get("Name") == "Green Atlas Windows"
        and package.get("Author") == "Green Atlas"
        and package.get("ProductCode") == "{AD229F45-E79B-4CB2-A250-63A3ABFD279B}"
        and any(entry.get("ModuleName") == "./Contents/Windows/GreenAtlasBridge.arx"
                for entry in package.findall("./Components/ComponentEntry"))
    )


class BundleInstaller:
    """Публикует соседние каталоги на одном томе; не удаляет неизвестные файлы."""

    def __init__(self, plugins: Path) -> None:
        self.plugins = plugins.absolute()
        self.installed = self.plugins / INSTALLED_NAME

    def install(
        self,
        source: Path,
        *,
        publish_shell: Callable[[Path, str], None] | None = None,
        before_publish: Callable[[], None] | None = None,
    ) -> InstallReceipt:
        self.recover(publish_shell=publish_shell, before_publish=before_publish)
        from app.distribution.windows_install_journal import read_journal

        if read_journal(self.plugins) is not None:
            raise ValueError("Очистка прежнего обновления ещё не завершена; текущая установка сохранена")
        manifest = load_owned_bundle(source)
        identity = manifest_identity(manifest)
        root = plain_directory(self.plugins, create=True)
        stage = root / ("GreenAtlas.staging-" + uuid.uuid4().hex)
        backup = root / ("GreenAtlas.backup-" + uuid.uuid4().hex)
        incomplete_old = False
        old_identity = None
        if _present(self.installed):
            incomplete_old = incomplete_legacy_bundle(self.installed)
            if not incomplete_old:
                old_identity = manifest_identity(load_owned_bundle(self.installed))
        if incomplete_old:
            backup = root / ("GreenAtlas.incomplete-" + uuid.uuid4().hex)
        from app.distribution.windows_install_journal import (
            finish_committed,
            write_journal,
        )

        journal = {
            "schema": "green-atlas.windows-install/1", "phase": "preparing",
            "stage": stage.name, "backup": backup.name,
            "failed": "GreenAtlas.failed-" + uuid.uuid4().hex,
            "old_kind": "incomplete" if incomplete_old else "owned" if old_identity else "absent",
            "old_identity": old_identity, "new_identity": identity,
            "stage_identity": None,
            "manifest": manifest.model_dump(mode="json", by_alias=True), "cleanup": [],
        }
        # Journal publication precedes the first program write/rename. An
        # interrupted copy therefore has an exact, bounded cleanup inventory.
        write_journal(root, journal)
        try:
            stage.mkdir(mode=0o700)
            journal["stage_identity"] = _directory_identity(stage)
            write_journal(root, journal)
            for entry in manifest.entries:
                if entry.kind != "file":
                    raise ValueError("Windows payload не должен содержать ссылки")
                origin = source / entry.path
                destination = stage / entry.path
                plain_directory(destination.parent, create=True)
                # Режим x не заменяет существующий файл staging.
                with origin.open("rb") as incoming, destination.open("xb") as outgoing:
                    digest = hashlib.sha256()
                    count = 0
                    while chunk := incoming.read(1024 * 1024):
                        outgoing.write(chunk)
                        digest.update(chunk)
                        count += len(chunk)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                if count != entry.size_bytes or digest.hexdigest() != entry.sha256:
                    raise ValueError("Payload изменился при копировании: " + entry.path)
            with (stage / MANIFEST_NAME).open("xb") as output:
                output.write((source / MANIFEST_NAME).read_bytes())
                output.flush()
                os.fsync(output.fileno())
            staged = load_owned_bundle(stage)
            if manifest_identity(staged) != identity:
                raise ValueError("Staging не соответствует выбранному комплекту")
            plain_directory(root)
            if before_publish is not None:
                before_publish()
            journal["phase"] = "switching"
            write_journal(root, journal)
            if _present(self.installed):
                if incomplete_old:
                    if not incomplete_legacy_bundle(self.installed):
                        raise ValueError("Неполная прежняя установка изменилась во время обновления")
                else:
                    if manifest_identity(load_owned_bundle(self.installed)) != old_identity:
                        raise ValueError("Прежняя установка изменилась во время обновления")
                _replace_bundle_directory(self.installed, backup)
            _replace_bundle_directory(stage, self.installed)
            if manifest_identity(load_owned_bundle(self.installed)) != identity:
                raise ValueError("Опубликованный комплект отличается от staging")
            if publish_shell is not None:
                publish_shell(self.installed, manifest.layout.version)
            journal["phase"] = "committed"
            write_journal(root, journal)
        except BaseException:
            # WindowsShell already rolls back ordinary callback failures. A
            # fresh setup repairs shell state during crash recovery below.
            self.recover()
            raise
        removed, pending = finish_committed(root, journal)
        return InstallReceipt(
            "install", str(self.installed), str(backup) if incomplete_old else None,
            identity, cleanup_pending=pending, removed_program_copies=removed,
        )

    def recover(
        self, *, publish_shell: Callable[[Path, str], None] | None = None,
        remove_shell: Callable[[], None] | None = None,
        before_publish: Callable[[], None] | None = None,
    ) -> bool:
        """Recover an interrupted update before accepting another operation.

        Before COMMITTED the old complete/incomplete install is restored. After
        COMMITTED only program cleanup is retried; valid new bytes stay current.
        """
        from app.distribution.windows_install_journal import (
            JOURNAL_NAME,
            finish_committed,
            read_journal,
        )

        root = plain_directory(self.plugins, create=True)
        value = read_journal(root)
        if value is None:
            return False
        if before_publish is not None:
            before_publish()
        if value["phase"] == "committed":
            _, pending = finish_committed(root, value)
            if pending:
                # A new update must not overwrite a pending cleanup receipt.
                # The caller may still report/use the already committed app.
                return True
            return True
        stage, backup, failed = (root / value[key] for key in ("stage", "backup", "failed"))
        manifest = PackageManifest.model_validate(value["manifest"])
        if _present(backup):
            if value["old_kind"] == "incomplete":
                if not incomplete_legacy_bundle(backup):
                    raise ValueError("Прежняя неполная установка изменилась; восстановление остановлено")
            elif manifest_identity(load_owned_bundle(backup)) != value["old_identity"]:
                raise ValueError("Прежний комплект отличается от журнала")
            if _present(self.installed):
                if _directory_identity(self.installed) != value.get("stage_identity"):
                    raise ValueError("Текущий комплект неизвестен журналу; файлы сохранены")
                if _present(failed):
                    raise ValueError("Имя неуспешного комплекта занято")
                _replace_bundle_directory(self.installed, failed)
            _replace_bundle_directory(backup, self.installed)
        elif value["old_kind"] == "owned":
            if manifest_identity(load_owned_bundle(self.installed)) != value["old_identity"]:
                raise ValueError("Не найден прежний комплект для восстановления")
        elif value["old_kind"] == "incomplete":
            if not incomplete_legacy_bundle(self.installed):
                raise ValueError("Не найдена прежняя неполная установка")
        elif _present(self.installed):
            if _directory_identity(self.installed) != value.get("stage_identity"):
                raise ValueError("Нельзя заменить чужой комплект при восстановлении")
            if _present(failed):
                raise ValueError("Имя неуспешного комплекта занято")
            _replace_bundle_directory(self.installed, failed)
        if _present(self.installed) and value["old_kind"] == "owned" and publish_shell:
            old = load_owned_bundle(self.installed)
            publish_shell(self.installed, old.layout.version)
        elif value["old_kind"] == "absent" and remove_shell:
            remove_shell()
        if _present(stage):
            if value.get("stage_identity") is None:
                if any(plain_directory(stage).iterdir()):
                    raise ValueError("Неизвестное содержимое staging сохранено")
            elif _directory_identity(stage) != value["stage_identity"]:
                raise ValueError("Каталог staging заменён; чужие файлы сохранены")
            self._remove_stage(stage, manifest)
            if _present(stage):
                raise ValueError("В staging остались неизвестные файлы; они сохранены")
        if _present(failed):
            if _directory_identity(failed) != value.get("stage_identity"):
                raise ValueError("Каталог неуспешного кандидата заменён; файлы сохранены")
            # This is exactly the newly created candidate, not an old install.
            # Even corrupt copied program bytes are regenerable. Delete only
            # its declared regular program files; unknown manual files stay.
            self._remove_stage(failed, manifest)
            if _present(failed):
                raise ValueError("В неуспешном кандидате остались неизвестные файлы; они сохранены")
        (root / JOURNAL_NAME).unlink()
        return True

    def rollback(
        self, backup: Path, *, publish_shell: Callable[[Path, str], None] | None = None,
        before_publish: Callable[[], None] | None = None,
    ) -> InstallReceipt:
        root = plain_directory(self.plugins)
        backup = backup.absolute()
        if backup.parent != root or not backup.name.startswith("GreenAtlas.backup-"):
            raise ValueError("Откат принимает только собственный соседний backup")
        suffix = backup.name.removeprefix("GreenAtlas.backup-")
        if len(suffix) != 32 or any(ch not in "0123456789abcdef" for ch in suffix):
            raise ValueError("Некорректный идентификатор backup")
        from dataclasses import replace

        # Legacy retained packages can still be selected explicitly. Successful
        # new updates intentionally keep no historical downgrade payload.
        return replace(self.install(backup, publish_shell=publish_shell, before_publish=before_publish), action="rollback")

    def uninstall(self, *, remove_shell: Callable[[], None] | None = None,
                  before_publish: Callable[[], None] | None = None) -> InstallReceipt:
        self.recover(remove_shell=remove_shell, before_publish=before_publish)
        from app.distribution.windows_install_journal import read_journal

        if read_journal(self.plugins) is not None:
            raise ValueError("Очистка обновления ещё не завершена; текущая установка сохранена")
        root = plain_directory(self.plugins)
        manifest = load_owned_bundle(self.installed)
        retained = root / ("GreenAtlas.uninstalled-" + uuid.uuid4().hex)
        if before_publish is not None:
            before_publish()
        _replace_bundle_directory(self.installed, retained)
        try:
            if remove_shell is not None:
                remove_shell()
        except BaseException:
            _replace_bundle_directory(retained, self.installed)
            raise
        # Оставляем собственные байты для восстановления, вне .bundle autoload.
        return InstallReceipt("uninstall", None, str(retained), manifest_identity(manifest))

    def purge_retained(self) -> tuple[str, ...]:
        """Явное удаление проверенных программных backups, без рабочего профиля."""
        root = plain_directory(self.plugins)
        selected: list[tuple[Path, PackageManifest]] = []
        for path in root.iterdir():
            prefix = next((p for p in ("GreenAtlas.backup-", "GreenAtlas.uninstalled-", "GreenAtlas.failed-") if path.name.startswith(p)), None)
            if prefix is None:
                continue
            suffix = path.name.removeprefix(prefix)
            if len(suffix) != 32 or any(ch not in "0123456789abcdef" for ch in suffix):
                continue
            selected.append((path, load_owned_bundle(path)))
        # Сначала принимаем весь набор, затем удаляем только известные файлы.
        for path, manifest in selected:
            load_owned_bundle(path)
            for entry in manifest.entries:
                target = path / entry.path
                plain_directory(target.parent)
                if hashlib.sha256(target.read_bytes()).hexdigest() != entry.sha256:
                    raise ValueError("Backup изменился перед очисткой")
                target.unlink()
            (path / MANIFEST_NAME).unlink()
            for directory, dirs, _ in os.walk(path, topdown=False, followlinks=False):
                for name in dirs:
                    plain_directory(Path(directory) / name).rmdir()
            path.rmdir()
        return tuple(str(path) for path, _ in selected)

    @staticmethod
    def _remove_stage(stage: Path, manifest: PackageManifest) -> None:
        plain_directory(stage)
        expected = {entry.path for entry in manifest.entries} | {MANIFEST_NAME}
        for directory, dirs, files in os.walk(stage, topdown=False, followlinks=False):
            for name in files:
                path = Path(directory) / name
                if path.relative_to(stage).as_posix() not in expected:
                    continue
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Staging содержит чужой тип файла")
                path.unlink()
            for name in dirs:
                child = plain_directory(Path(directory) / name)
                if not any(child.iterdir()):
                    child.rmdir()
        if not any(stage.iterdir()):
            stage.rmdir()


def write_install_receipt(path: Path, receipt: InstallReceipt) -> None:
    from dataclasses import asdict

    plain_directory(path.parent, create=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(asdict(receipt), output, ensure_ascii=False, indent=2)
        output.write("\n")
