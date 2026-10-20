"""Домен исходника: прежние BLOB и неизменяемый источник retained native-захвата.

Запросы строит SQLAlchemy; CAS и запись остаются у репозитория. Файловый источник
читает только допущенный CAD-архив этого проекта с проверкой размера и SHA.
"""

from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

from sqlalchemy import and_, delete, func, literal_column, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.schema import CreateTable

from app.desktop.tickets import direct_path, read_regular
from app.dxf_import.contracts import ImportMode
from app.native_query.capture.capture_contracts import SessionTransfer
from app.native_query.capture.capture_store import NativeCaptureStore
from app.projects.contracts import Project
from app.projects.source_contracts import SourceContentInfo
from app.shared.failures import ProjectNotFound
from app.shared.sqlite_sql import sqlite_sql
from app.shared.storage_schema import PROJECT_SOURCES, PROJECTS

SOURCE_WRITE_CHUNK_BYTES = 1024 * 1024


class SourceStorage:
    def __init__(
        self, connection: sqlite3.Connection, archive_root: Path | None = None
    ) -> None:
        self.connection = connection
        self.archives = NativeCaptureStore(archive_root) if archive_root else None

    def verify_retained(self, project: Project, root: Path) -> None:
        """Проверить сохранённый источник до CAS, включая ещё не привязанный сеанс."""
        source = project.source_file
        if (
            self.archives is None
            or project.import_status.mode != ImportMode.AUTOCAD_LIVE
            or source is None
            or source.content_sha256 is None
        ):
            raise ValueError("Сохранённый источник требует native-проект")
        expected = self.archives.directory(project.id, source.content_sha256)
        if root.absolute() != expected:
            raise ValueError("Источник должен находиться в CAD-архиве этого проекта")
        path = self._retained_path(project.id, source.content_sha256, source.size)
        self._read_retained(path, source.size, source.content_sha256, prefix_bytes=0)

    def _retained_path(self, project_id: str, digest: str, size: int) -> Path:
        """Источник имеет тот же адрес и SHA, что полностью допущенный CAD-комплект."""
        if self.archives is None:
            raise ValueError("Каталог CAD-архива отсутствует")
        root = direct_path(
            self.archives.directory(project_id, digest), self.archives.root
        )
        with read_regular(root / "binding.json") as stream:
            content = stream.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("Привязка CAD-архива превышает допустимый размер")
        binding = json.loads(content)
        if (
            not isinstance(binding, dict)
            or binding.get("project_id") != project_id
            or binding.get("display_sha256") != digest
        ):
            raise ValueError("CAD-архив относится к другому проекту")
        manifest = SessionTransfer.model_validate(binding["transfer"])
        display = next(
            (entry for entry in manifest.files if entry.name == "Drawing.autocad.json"),
            None,
        )
        if display is None or (display.sha256, display.bytes) != (digest, size):
            raise ValueError("Источник не соответствует привязке CAD-архива")
        return direct_path(root / display.name, self.archives.root)

    def _retained_identity(self, project_id: str) -> tuple[Path, int, str] | None:
        projection = func.coalesce(PROJECTS.c.projection, PROJECTS.c.payload)
        row = self.connection.execute(
            *sqlite_sql(
                select(
                    func.json_extract(projection, "$.import_status.mode").label("mode"),
                    # Сохранённые байты независимы от редактируемых метаданных.
                    # Provenance уже опубликован до привязки native-сеанса.
                    func.coalesce(
                        func.json_extract(
                            projection,
                            "$.source_file.cad_snapshot_provenance.source_sha256",
                        ),
                        func.json_extract(
                            projection, "$.source_file.native_session.snapshot_sha256"
                        ),
                        func.json_extract(projection, "$.source_file.content_sha256"),
                    ).label("sha"),
                    func.json_extract(projection, "$.source_file.size").label("size"),
                ).where(PROJECTS.c.id == project_id)
            )
        ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        if row["mode"] != ImportMode.AUTOCAD_LIVE or row["sha"] is None:
            return None
        return (
            self._retained_path(project_id, row["sha"], row["size"]),
            row["size"],
            row["sha"],
        )

    @staticmethod
    def _read_retained(
        path: Path, size: int, digest: str, *, prefix_bytes: int | None
    ) -> bytes:
        """SHA проверяется на том же открытом файле; заголовок не загружает весь JSON."""
        actual, count, content = sha256(), 0, bytearray()
        with read_regular(path) as stream:
            while chunk := stream.read(SOURCE_WRITE_CHUNK_BYTES):
                count += len(chunk)
                if count > size:
                    raise ValueError("Нарушена целостность сохранённого исходника")
                actual.update(chunk)
                if prefix_bytes is None:
                    content.extend(chunk)
                elif len(content) < prefix_bytes:
                    content.extend(chunk[: prefix_bytes - len(content)])
        if count != size or actual.hexdigest() != digest:
            raise ValueError("Нарушена целостность сохранённого исходника")
        return bytes(content)

    def needs_migration(self) -> bool:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(project_sources)")
        }
        if columns and not {"project_id", "content"} <= columns:
            raise ValueError("Неподдерживаемая схема сохранённого исходника")
        return "content_sha256" not in columns

    def initialize(self) -> None:
        self.connection.execute(
            *sqlite_sql(CreateTable(PROJECT_SOURCES, if_not_exists=True))
        )
        if self.needs_migration():
            self.connection.execute(
                "ALTER TABLE project_sources ADD COLUMN content_sha256 TEXT"
            )

    def write(self, project_id: str, content: bytes | bytearray) -> None:
        """Репозиторий владеет блокировкой, проверкой существования, CAS и фиксацией."""
        frozen = bytes(content)
        statement = insert(PROJECT_SOURCES).values(
            project_id=project_id,
            content=func.zeroblob(len(frozen)),
            content_sha256=sha256(frozen).hexdigest(),
        )
        self.connection.execute(
            *sqlite_sql(
                statement.on_conflict_do_update(
                    index_elements=[PROJECT_SOURCES.c.project_id],
                    set_={
                        "content": statement.excluded.content,
                        "content_sha256": statement.excluded.content_sha256,
                    },
                )
            )
        )
        row = self.connection.execute(
            *sqlite_sql(
                select(literal_column("rowid"))
                .select_from(PROJECT_SOURCES)
                .where(PROJECT_SOURCES.c.project_id == project_id)
            )
        ).fetchone()
        with self.connection.blobopen("project_sources", "content", row[0]) as target:
            view = memoryview(frozen)
            for offset in range(0, len(view), SOURCE_WRITE_CHUNK_BYTES):
                target.write(view[offset : offset + SOURCE_WRITE_CHUNK_BYTES])

    def delete(self, project_id: str) -> None:
        self.connection.execute(
            *sqlite_sql(
                delete(PROJECT_SOURCES).where(
                    PROJECT_SOURCES.c.project_id == project_id
                )
            )
        )

    def read(self, project_id: str) -> bytes | None:
        row = self.connection.execute(
            *sqlite_sql(
                select(
                    PROJECTS.c.source.label("inline_source"),
                    PROJECT_SOURCES.c.content,
                    PROJECT_SOURCES.c.content_sha256,
                )
                .select_from(
                    PROJECTS.outerjoin(
                        PROJECT_SOURCES, PROJECT_SOURCES.c.project_id == PROJECTS.c.id
                    )
                )
                .where(PROJECTS.c.id == project_id)
            )
        ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        inline, separate = row["inline_source"], row["content"]
        if inline is not None and separate is not None and inline != separate:
            raise ValueError("Сохранённые версии исходника не совпадают")
        if separate is not None and row["content_sha256"] is not None:
            if sha256(separate).hexdigest() != row["content_sha256"]:
                raise ValueError("Нарушена целостность сохранённого исходника")
        content = inline if inline is not None else separate
        if content is not None:
            return bytes(content)
        retained = self._retained_identity(project_id)
        return self._read_retained(*retained, prefix_bytes=None) if retained else None

    def info(self, project_id: str, prefix_bytes: int) -> SourceContentInfo | None:
        if prefix_bytes < 0:
            raise ValueError("Размер заголовка должен быть неотрицательным")
        # Просмотр заголовка не должен загружать полный BLOB исходника в Python.
        content = func.coalesce(PROJECTS.c.source, PROJECT_SOURCES.c.content)
        row = self.connection.execute(
            *sqlite_sql(
                select(
                    func.length(content).label("size"),
                    func.substr(content, 1, prefix_bytes).label("prefix"),
                    and_(
                        PROJECTS.c.source.is_not(None),
                        PROJECT_SOURCES.c.content.is_not(None),
                        PROJECTS.c.source != PROJECT_SOURCES.c.content,
                    ).label("conflict"),
                )
                .select_from(
                    PROJECTS.outerjoin(
                        PROJECT_SOURCES, PROJECT_SOURCES.c.project_id == PROJECTS.c.id
                    )
                )
                .where(PROJECTS.c.id == project_id)
            )
        ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        if row["conflict"]:
            raise ValueError("Сохранённые версии исходника не совпадают")
        if row["size"] is not None:
            return SourceContentInfo(row["size"], bytes(row["prefix"] or b""))
        retained = self._retained_identity(project_id)
        if retained is None:
            return None
        return SourceContentInfo(
            retained[1], self._read_retained(*retained, prefix_bytes=prefix_bytes)
        )
