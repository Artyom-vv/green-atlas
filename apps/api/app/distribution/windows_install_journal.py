"""Durable program-only update state; never names a user profile or CAD source."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any

from app.distribution.contracts import PackageManifest
from app.distribution.preflight import MANIFEST_NAME, manifest_identity

JOURNAL_NAME = ".green-atlas-install.json"
PREFIXES = ("GreenAtlas.backup-", "GreenAtlas.failed-", "GreenAtlas.uninstalled-")


def owned_name(name: str, prefixes: tuple[str, ...]) -> bool:
    return isinstance(name, str) and any(name.startswith(p) and len(name[len(p):]) == 32
               and all(c in "0123456789abcdef" for c in name[len(p):]) for p in prefixes)


def plain_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Журнал или файл программы содержит ссылку: " + str(path))


def read_journal(root: Path) -> dict[str, Any] | None:
    path = root / JOURNAL_NAME
    try:
        plain_file(path)
    except FileNotFoundError:
        return None
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Слишком большой журнал установки")
    value = json.loads(path.read_bytes())
    if (not isinstance(value, dict) or value.get("schema") != "green-atlas.windows-install/1"
            or value.get("phase") not in ("preparing", "switching", "committed")
            or not owned_name(value.get("stage", ""), ("GreenAtlas.staging-",))
            or not owned_name(value.get("backup", ""), ("GreenAtlas.backup-", "GreenAtlas.incomplete-"))
            or not owned_name(value.get("failed", ""), ("GreenAtlas.failed-",))
            or value.get("old_kind") not in ("absent", "owned", "incomplete")
            or not isinstance(value.get("cleanup"), list)
            or len(value["cleanup"]) > 1):
        raise ValueError("Неизвестный журнал установки; файлы сохранены")
    manifest = PackageManifest.model_validate(value["manifest"])
    if manifest.layout.platform != "windows" or manifest_identity(manifest) != value["new_identity"]:
        raise ValueError("Журнал не соответствует Windows комплекту")
    for row in value.get("cleanup", []):
        if not isinstance(row, dict) or not owned_name(row.get("name", ""), PREFIXES):
            raise ValueError("Журнал очистки содержит чужой путь")
        old = PackageManifest.model_validate(row["manifest"])
        if old.layout.platform != "windows":
            raise ValueError("Журнал очистки содержит другой тип комплекта")
    directory = value.get("stage_identity")
    if directory is not None and (not isinstance(directory, dict)
            or set(directory) != {"device", "inode", "birth_ns"}
            or not all(isinstance(n, int) for n in directory.values())):
        raise ValueError("Журнал содержит неизвестную идентичность staging")
    return value


def write_journal(root: Path, value: dict[str, Any]) -> None:
    path = root / JOURNAL_NAME
    if path.exists() or path.is_symlink():
        plain_file(path)
    temporary = root / (JOURNAL_NAME + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        # Windows does not expose a portable directory fsync. The journal file
        # itself is flushed before the atomic same-volume publication.
        if os.name != "nt":
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def remove_payload(path: Path, manifest: PackageManifest) -> None:
    """Resume deletion after a crash, accepting only a subset of sealed files."""
    from app.distribution.windows_install import plain_directory

    if not path.exists() and not path.is_symlink():
        return
    plain_directory(path)
    expected = {entry.path: entry for entry in manifest.entries}
    if any(entry.kind != "file" for entry in manifest.entries):
        raise ValueError("Очистка Windows не принимает ссылки")
    allowed_directories = {parent.as_posix() for name in expected for parent in Path(name).parents if str(parent) != "."}
    files: list[Path] = []
    for directory, dirs, names in os.walk(path, followlinks=False):
        for name in dirs:
            child = plain_directory(Path(directory) / name)
            if child.relative_to(path).as_posix() not in allowed_directories:
                raise ValueError("В программной копии найден чужой каталог")
        for name in names:
            target = Path(directory) / name
            plain_file(target)
            relative = target.relative_to(path).as_posix()
            if relative == MANIFEST_NAME:
                stored = PackageManifest.model_validate_json(target.read_bytes())
                if manifest_identity(stored) != manifest_identity(manifest):
                    raise ValueError("Манифест копии изменён перед очисткой")
            else:
                entry = expected.get(relative)
                if entry is None:
                    raise ValueError("В программной копии найден чужой файл")
                digest = hashlib.sha256()
                count = 0
                with target.open("rb") as source:
                    while block := source.read(1024 * 1024):
                        digest.update(block)
                        count += len(block)
                if (count, digest.hexdigest()) != (entry.size_bytes, entry.sha256):
                    raise ValueError("Программная копия изменена перед очисткой")
            files.append(target)
    # Validate the complete remaining set before deleting the first file.
    for target in files:
        plain_directory(target.parent)
        plain_file(target)
        target.unlink()
    for directory, _, _ in os.walk(path, topdown=False, followlinks=False):
        plain_directory(Path(directory)).rmdir()


def finish_committed(root: Path, value: dict[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Cleanup failure is pending maintenance, never a reason to undo a commit."""
    from app.distribution.windows_install import INSTALLED_NAME, load_owned_bundle

    # A damaged current install is not a cleanup warning and must not be
    # reported as successful maintenance. Keep the old payload and journal.
    if manifest_identity(load_owned_bundle(root / INSTALLED_NAME)) != value["new_identity"]:
        raise ValueError("Текущая установка изменилась; очистка остановлена")
    removed: list[str] = []
    try:
        # A single durable deletion inventory bounds journal size regardless
        # of the number of old builds. Resume it before selecting another.
        for row in value.get("cleanup", []):
            path = root / row["name"]
            remove_payload(path, PackageManifest.model_validate(row["manifest"]))
            removed.append(str(path))
        value["cleanup"] = []
        write_journal(root, value)
        for path in sorted(root.iterdir()):
            if owned_name(path.name, PREFIXES):
                manifest = load_owned_bundle(path)
                value["cleanup"] = [{"name": path.name, "manifest": manifest.model_dump(mode="json", by_alias=True)}]
                # Persist the exact inventory before unlinking any payload.
                write_journal(root, value)
                remove_payload(path, manifest)
                removed.append(str(path))
                value["cleanup"] = []
                write_journal(root, value)
        (root / JOURNAL_NAME).unlink()
        return tuple(removed), ()
    except (OSError, ValueError, KeyError) as error:
        return tuple(removed), (str(error),)
