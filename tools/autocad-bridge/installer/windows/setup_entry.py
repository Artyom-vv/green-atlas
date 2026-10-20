"""Вход автономного Setup.exe; Python и PowerShell на ПК пользователя не требуются."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from app.distribution.preflight import manifest_identity
from app.distribution.windows_install import (
    BundleInstaller,
    load_owned_bundle,
    plain_directory,
    write_install_receipt,
)
from app.distribution.windows_prerequisites import ensure_webview_runtime
from app.distribution.windows_setup_guard import assert_applications_closed
from app.distribution.windows_shell import (
    WindowsShell,
    installation_mutex,
    message,
    native_uninstaller,
    require_windows_user,
    write_diagnostic,
)


def entry_present(path: Path) -> bool:
    """Dangling ссылка или reparse point также занимают окончательное имя."""
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def regular_file(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400


def executable_sha256(path: Path) -> str:
    """Читать большой onefile setup ограниченными блоками."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def publish_new_file(temporary: Path, target: Path) -> None:
    """Опубликовать целый файл без замены занятого имени, включая гонку создания."""
    if os.name == "nt":
        # Windows rename атомарно отвергает существующий dst; replace здесь недопустим.
        os.rename(temporary, target)
    else:
        # Переносимая проверка той же политики не использует Unix rename с перезаписью.
        os.link(temporary, target)
        temporary.unlink()


def save_setup_copy(executable: Path, target: Path, identity: str) -> None:
    temporary = target.with_name(target.name + ".staging-" + uuid.uuid4().hex)
    try:
        digest = hashlib.sha256()
        with executable.open("rb") as incoming, temporary.open("xb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                outgoing.write(chunk)
                digest.update(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest.hexdigest() != identity:
            raise ValueError("Setup изменился во время сохранения")
        if entry_present(target):
            raise ValueError("Имя сохранённого setup занято другим объектом")
        publish_new_file(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def keep_setup(installer_root: Path) -> Path:
    executable = Path(sys.executable)
    if not getattr(sys, "frozen", False):
        raise ValueError("Для установки запустите собранный GreenAtlasSetup.exe")
    identity = executable_sha256(executable)
    directory = plain_directory(installer_root / "Versions" / identity, create=True)
    target = directory / "GreenAtlasSetup.exe"
    if entry_present(target):
        if not regular_file(target) or executable_sha256(target) != identity:
            raise ValueError("Сохранённый setup не соответствует текущему EXE")
    else:
        save_setup_copy(executable, target, identity)
    marker = directory / "setup-version.json"
    expected = {"schema": "green-atlas.saved-setup/1", "file": target.name, "sha256": identity}
    if entry_present(marker):
        if not regular_file(marker) or json.loads(marker.read_bytes()) != expected:
            raise ValueError("Маркер сохранённого setup отличается")
    else:
        temporary = marker.with_name(marker.name + ".staging-" + uuid.uuid4().hex)
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(expected, stream)
                stream.flush()
                os.fsync(stream.fileno())
            if entry_present(marker):
                raise ValueError("Имя маркера setup занято другим объектом")
            publish_new_file(temporary, marker)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def purge_setup_versions(installer_root: Path, *, keep: tuple[Path, ...] = ()) -> tuple[str, ...]:
    """Удалить только проверенные неисполняемые копии setup из своего хранилища."""
    root = plain_directory(installer_root / "Versions")
    current = Path(sys.executable).absolute()
    protected = {current, *(path.absolute() for path in keep)}
    removed = []
    pending = []
    for directory in root.iterdir():
        if len(directory.name) != 64 or any(ch not in "0123456789abcdef" for ch in directory.name):
            continue
        try:
            plain_directory(directory)
            if {path.name for path in directory.iterdir()} != {"GreenAtlasSetup.exe", "setup-version.json"}:
                continue
            executable = directory / "GreenAtlasSetup.exe"
            marker = directory / "setup-version.json"
            if executable.absolute() in protected:
                continue
            if not regular_file(executable) or not regular_file(marker):
                raise ValueError("Сохранённый setup содержит ссылку")
            expected = {"schema": "green-atlas.saved-setup/1", "file": executable.name, "sha256": directory.name}
            if json.loads(marker.read_bytes()) != expected or executable_sha256(executable) != directory.name:
                raise ValueError("Сохранённый setup изменён; очистка остановлена")
            executable.unlink()
            marker.unlink()
            directory.rmdir()
            removed.append(directory.name)
        except (OSError, ValueError) as error:
            # A single Windows-locked/changed old EXE must not make every
            # subsequent verified version accumulate behind it.
            pending.append(directory.name + ": " + str(error))
    if pending:
        raise ValueError("; ".join(pending)[:8192])
    return tuple(removed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Установка Green Atlas для текущего пользователя")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--uninstall", action="store_true")
    actions.add_argument("--rollback", action="store_true")
    actions.add_argument("--purge-app-backups", action="store_true")
    actions.add_argument("--status-json", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--native-uninstaller", type=Path)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--check-closed", action="store_true")
    parser.add_argument("--dependency-action", choices=("discover", "check", "save"))
    parser.add_argument("--dependency-file", type=Path)
    parser.add_argument("--dependency-result", type=Path)
    args = parser.parse_args()

    def progress(stage: str) -> None:
        if args.progress:
            write_diagnostic("GA_STAGE:" + stage + "\n")

    def wizard_consent() -> bool:
        return True

    try:
        require_windows_user()
        if args.dependency_action:
            from app.distribution.dependency_setup import wizard_action

            if args.dependency_result is None:
                raise ValueError("Не задан файл результата настройки")
            return 0 if wizard_action(args.dependency_action, args.dependency_file, args.dependency_result) else 2
        if args.check_closed:
            assert_applications_closed()
            write_diagnostic('{"ok":true}\n')
            return 0
        roaming = Path(os.environ["APPDATA"])
        local = Path(os.environ["LOCALAPPDATA"])
        maintenance = native_uninstaller(local, args.native_uninstaller) if args.native_uninstaller else None
        installer_root = local / "GreenAtlas/Installer"
        engine = BundleInstaller(roaming / "Autodesk/ApplicationPlugins")
        if args.status_json:
            current = load_owned_bundle(engine.installed) if engine.installed.exists() else None
            write_diagnostic(json.dumps({"installed": current is not None, "package_sha256": manifest_identity(current) if current else None}, ensure_ascii=False) + "\n")
            return 0
        action = "удалить" if args.uninstall else "восстановить предыдущую версию" if args.rollback else "очистить программные резервные копии" if args.purge_app_backups else "установить или обновить"
        if not args.quiet and not message(
            f"{action.capitalize()} Green Atlas?\n\nЗакройте AutoCAD и Green Atlas. Рабочий профиль, проекты и модели сохраняются.", confirm=True
        ):
            return 0
        with installation_mutex():
            progress("checking")
            assert_applications_closed()
            progress("saving")
            setup = keep_setup(installer_root)
            shell = WindowsShell(roaming, setup) if maintenance is None else WindowsShell(roaming, setup, uninstaller=maintenance)

            def publish(installed: Path, version: str) -> None:
                progress("shortcuts")
                shell.publish(installed, version)
            # Runs under the installation mutex with both applications closed.
            # An interrupted pre-commit switch is undone before a new action;
            # committed cleanup only retries removal of sealed program copies.
            engine.recover(publish_shell=publish, remove_shell=shell.remove)
            if args.purge_app_backups:
                removed = engine.purge_retained()
                purge_setup_versions(installer_root)
                if not args.quiet:
                    message(f"Удалено программных резервных комплектов: {len(removed)}. Рабочий профиль сохранён. Текущий запущенный setup остаётся в Installer/Versions для последующего обслуживания.")
                return 0
            if args.uninstall:
                progress("removing")
                receipt = engine.uninstall(remove_shell=shell.remove, before_publish=assert_applications_closed)
            elif args.rollback:
                candidates = []
                for path in plain_directory(engine.plugins).glob("GreenAtlas.backup-*"):
                    load_owned_bundle(path)
                    candidates.append(path)
                if not candidates:
                    raise ValueError("Предыдущая сборка не сохранена: после успешного обновления старые программные комплекты удаляются. Рабочие проекты и резервные копии данных сохранены.")
                backup = max(candidates, key=lambda path: path.stat().st_mtime_ns)
                receipt = engine.rollback(backup, publish_shell=publish, before_publish=assert_applications_closed)
            else:
                extracted = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
                source = extracted / "payload/GreenAtlas.bundle"
                load_owned_bundle(source)
                confirm_elevation = None if args.quiet else lambda: message(
                    "На компьютере установлена системная версия Microsoft WebView2, "
                    "которую нужно обновить. Запустить включённый установщик Microsoft "
                    "с запросом прав Windows? Green Atlas продолжит установку "
                    "для текущего пользователя.", confirm=True
                )
                if maintenance is not None:
                    # «Установить» в native-мастере подтверждает описанное на Ready
                    # стандартное повышение только для Microsoft WebView2.
                    confirm_elevation = wizard_consent
                progress("webview")
                ensure_webview_runtime(source, confirm_elevation=confirm_elevation)
                progress("installing")
                receipt = engine.install(source, publish_shell=publish, before_publish=assert_applications_closed)
            if receipt.action in ("install", "rollback"):
                # Keep this saved maintenance entry and an actually running
                # older Setup. An executable locked by Windows is reported as
                # pending cleanup, never as a failed/rolled-back installation.
                try:
                    purge_setup_versions(installer_root, keep=(setup,))
                except (OSError, ValueError) as error:
                    from dataclasses import replace

                    receipt = replace(receipt, cleanup_pending=receipt.cleanup_pending + ("Installer/Versions: " + str(error),))
            write_install_receipt(installer_root / "Receipts" / (uuid.uuid4().hex + ".json"), receipt)
            if args.quiet:
                progress("complete")
                write_diagnostic(json.dumps(asdict(receipt), ensure_ascii=False) + "\n")
            elif args.uninstall:
                message("Green Atlas удалён из AutoCAD и меню приложений. Программный комплект сохранён для восстановления. Workspace, Transfers, WebView2 и модели сохранены. Текущий setup в Installer/Versions сохраняется для обслуживания.")
                if message("Удалить проверенные программные резервные комплекты? Рабочий профиль сохранится.", confirm=True):
                    engine.purge_retained()
                    purge_setup_versions(installer_root)
            else:
                message("Green Atlas готов к запуску из меню Пуск или AutoCAD. Рабочий профиль сохранён.")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        if args.progress:
            write_diagnostic("GA_ERROR:" + str(error).removeprefix("needs_machine_prerequisite_upgrade: ").replace("\r", " ").replace("\n", " ")[:8192] + "\n")
        if args.quiet:
            write_diagnostic(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False) + "\n", error=True)
        elif os.name == "nt":
            message(str(error).removeprefix("needs_machine_prerequisite_upgrade: "), error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
