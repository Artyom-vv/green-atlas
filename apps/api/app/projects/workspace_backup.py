"""Публикация полной локальной точки восстановления перед изменением схемы хранилища."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from app.projects.archive_backup import backup_archives
from app.projects.backup_files import store_backup_file, sync_directory
from app.projects.geometry_storage import (
    GEOMETRY_STORAGE_KEY,
    GeometryStore,
    geometry_references,
)

BACKUP_SCHEMA = "green-atlas.workspace-backup/4"


def _sync_file(path: Path) -> None:
    # Windows requires a writable file descriptor for _commit/os.fsync.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def backup_workspace(
    database: str | Path,
    *,
    reason: str,
    connection: sqlite3.Connection | None = None,
    prepared_directory: Path | None = None,
) -> Path:
    """Сохраняет записи WAL и неизменяемые подготовленные файлы без производных доменов.

    Опубликованные записи v1 не хранят ссылку на подготовленный файл, поэтому для
    расчётных оснований исторических планов нужны все корневые снимки. Функция не
    разбирает CAD и не перезаписывает исходные файлы.
    """
    database = Path(database).absolute()
    if connection is None:
        source_connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        try:
            return backup_workspace(
                database,
                reason=reason,
                connection=source_connection,
                prepared_directory=prepared_directory,
            )
        finally:
            source_connection.close()
    if connection.in_transaction:
        raise ValueError("Перед резервным копированием завершите транзакцию проекта")
    root = database.with_name(database.name + ".backups")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root / (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex
    )
    prepared = prepared_directory or database.with_name(
        database.name + ".prepared-geometry"
    )
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
        staged = Path(temporary) / "complete"
        staged.mkdir(mode=0o700)
        db_copy = staged / database.name
        # INTO создаёт согласованную копию, включая WAL, без свободных страниц.
        # Исходная база не уплотняется; временный результат публикуется только
        # после прежних проверок. Неявные ROWID не являются ключами проектов.
        connection.execute("VACUUM main INTO ?", (str(db_copy),))
        target = sqlite3.connect(db_copy)
        try:
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Не удалось проверить резервную копию проекта")
            # В старой базе отдельной таблицы исходников ещё может не быть.
            source_table = target.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_sources'"
            ).fetchone()
            no_source = "source IS NULL"
            if source_table is not None:
                no_source += (
                    " AND NOT EXISTS(SELECT 1 FROM project_sources AS stored "
                    "WHERE stored.project_id=projects.id AND stored.content IS NOT NULL)"
                )
            archive_references = target.execute(
                "SELECT id, coalesce("
                "json_extract(payload, '$.source_file.native_session.snapshot_sha256'), "
                "CASE WHEN json_extract(payload, '$.import_status.mode') = 'autocad_live' "
                "THEN coalesce(json_extract(payload, '$.source_file.cad_snapshot_provenance.source_sha256'), "
                "json_extract(payload, '$.source_file.content_sha256')) END), "
                "CASE WHEN json_extract(payload, '$.import_status.mode') = 'autocad_live' "
                f"AND {no_source} THEN 1 ELSE 0 END FROM projects"
            ).fetchall()
            geometry_rows = target.execute(
                "SELECT id, payload FROM projects WHERE "
                "json_type(payload, ?) IS NOT NULL",
                (f"$.{GEOMETRY_STORAGE_KEY}",),
            ).fetchall()
        finally:
            target.close()
        _sync_file(db_copy)
        database_file = store_backup_file(db_copy, staged, Path(database.name))
        if database_file["encoding"] == "gzip":
            db_copy.unlink()
        files = []
        if prepared.is_symlink():
            raise ValueError("Каталог подготовленной геометрии является ссылкой")
        if prepared.exists():
            copy_root = staged / prepared.name
            copy_root.mkdir(mode=0o700)
            for source in sorted(prepared.glob("*.json")):
                if source.is_symlink() or not source.is_file():
                    raise ValueError("Подготовленная геометрия не является файлом")
                files.append(
                    store_backup_file(source, staged, Path(prepared.name) / source.name)
                )
            sync_directory(copy_root)
        archives = backup_archives(database, staged, archive_references)
        geometry = GeometryStore(Path(str(database) + ".geometry"))
        geometry_files = [
            record
            for project_id, payload in geometry_rows
            for record in geometry.backup(
                project_id, geometry_references(json.loads(payload)), staged
            )
        ]
        manifest = {
            "schema": BACKUP_SCHEMA,
            "reason": reason,
            "created_at": datetime.now(UTC).isoformat(),
            "database": database_file,
            "prepared_directory_present": prepared.exists(),
            "prepared_files": files,
            "archive_files": archives,
            "geometry_files": geometry_files,
        }
        manifest_path = staged / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        _sync_file(manifest_path)
        sync_directory(staged)
        staged.replace(destination)
        sync_directory(root)
    return destination
