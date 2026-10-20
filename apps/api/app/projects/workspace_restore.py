"""Проверка и восстановление точки рабочей среды в новый пустой каталог."""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.projects.backup_files import (
    BACKUP_COPY_CHUNK_BYTES,
    file_digest,
    sync_directory,
)
from app.projects.geometry_storage import (
    GEOMETRY_STORAGE_KEY,
    GeometryStore,
    geometry_references,
)


class BackupFileRecord(BaseModel):
    path: str
    original_path: str | None = None
    encoding: Literal["identity", "gzip"] = "identity"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stored_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    bytes: int | None = Field(default=None, ge=0)


class WorkspaceBackupManifest(BaseModel):
    schema_version: Literal[
        "green-atlas.workspace-backup/1",
        "green-atlas.workspace-backup/2",
        "green-atlas.workspace-backup/3",
        "green-atlas.workspace-backup/4",
    ] = Field(alias="schema")
    database: BackupFileRecord
    prepared_files: list[BackupFileRecord]
    archive_files: list[BackupFileRecord] = Field(default_factory=list)
    geometry_files: list[BackupFileRecord] = Field(default_factory=list)


def _relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("Путь резервной копии выходит за её каталог")
    return path


def _restore_file(backup: Path, staged: Path, record: BackupFileRecord) -> Path:
    stored = _relative_path(record.path)
    original = _relative_path(record.original_path or record.path)
    if record.encoding == "gzip" and record.original_path is None:
        raise ValueError("Не указан исходный путь сжатого файла")
    source = backup / stored
    if any(
        (backup / Path(*stored.parts[:index])).is_symlink()
        for index in range(1, len(stored.parts) + 1)
    ):
        raise ValueError("Резервная копия содержит ссылку")
    if record.stored_sha256 and file_digest(source) != record.stored_sha256:
        raise ValueError("Нарушена целостность файла резервной копии")
    destination = staged / original
    if destination.exists():
        raise ValueError("Резервная копия повторяет путь назначения")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    size = 0
    reader = gzip.open(source, "rb") if record.encoding == "gzip" else source.open("rb")
    with reader, destination.open("xb") as writer:
        for chunk in iter(lambda: reader.read(BACKUP_COPY_CHUNK_BYTES), b""):
            writer.write(chunk)
            digest.update(chunk)
            size += len(chunk)
            if record.bytes is not None and size > record.bytes:
                raise ValueError("Нарушена целостность восстановленного файла")
        writer.flush()
        os.fsync(writer.fileno())
    if digest.hexdigest() != record.sha256 or (
        record.bytes is not None and size != record.bytes
    ):
        raise ValueError("Нарушена целостность восстановленного файла")
    return destination


def restore_workspace_backup(backup: Path, destination: Path) -> Path:
    """Восстанавливает оба формата резервной копии без перезаписи рабочего профиля."""
    backup = backup.absolute()
    destination = destination.absolute()
    if backup.is_symlink() or (backup / "manifest.json").is_symlink():
        raise ValueError("Резервная копия содержит ссылку")
    if destination.is_symlink() or (
        destination.exists() and any(destination.iterdir())
    ):
        raise ValueError("Каталог восстановления должен быть пустым")
    manifest = WorkspaceBackupManifest.model_validate(
        json.loads((backup / "manifest.json").read_text())
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".restore-", dir=destination.parent
    ) as temporary:
        staged = Path(temporary) / "complete"
        staged.mkdir(mode=0o700)
        database = _restore_file(backup, staged, manifest.database)
        for record in (
            *manifest.prepared_files,
            *manifest.archive_files,
            *manifest.geometry_files,
        ):
            _restore_file(backup, staged, record)
        with closing(
            sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        ) as restored:
            if restored.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Не удалось проверить восстановленную базу")
            geometry_store = GeometryStore(Path(str(database) + ".geometry"))
            rows = restored.execute(
                "SELECT id, payload FROM projects WHERE json_type(payload, ?) IS NOT NULL",
                (f"$.{GEOMETRY_STORAGE_KEY}",),
            )
            for project_id, payload in rows:
                for reference in geometry_references(json.loads(payload)).values():
                    with geometry_store.open(project_id, reference) as stream:
                        geometry_store.read(stream, reference, set())
        for directory in sorted(
            (path for path in staged.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            sync_directory(directory)
        sync_directory(staged)
        if destination.exists():
            destination.rmdir()
        staged.replace(destination)
        sync_directory(destination.parent)
    return destination / _relative_path(
        manifest.database.original_path or manifest.database.path
    )
