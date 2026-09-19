"""Shared atomic source/project/operation publication; validation stays in scenarios."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from app.cad_intake.prepare_contracts import CadPrepareResult
from app.cad_intake.preview_contracts import CadPreviewResult
from app.operations.contracts import OperationStatus, ProjectOperation
from app.operations.progress import OperationCancelled
from app.projects.adapters import project_projection
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project

_PROJECT_JSON = TypeAdapter(Project)
_PUBLICATION_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class SourcePublicationAsset:
    path: Path
    sha256: str
    size: int

    @classmethod
    def checked(cls, path: Path) -> "SourcePublicationAsset":
        digest = sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(_PUBLICATION_CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
        if size <= 0:
            raise ValueError("Пустой DXF нельзя опубликовать")
        return cls(path.resolve(strict=True), digest.hexdigest(), size)


SourcePublicationContent = bytes | bytearray | SourcePublicationAsset


def publication_size(content: SourcePublicationContent) -> int:
    return content.size if isinstance(content, SourcePublicationAsset) else len(content)


def publication_sha256(content: SourcePublicationContent) -> str:
    return (
        content.sha256
        if isinstance(content, SourcePublicationAsset)
        else sha256(content).hexdigest()
    )


def _write_asset(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    rowid: int,
    asset: SourcePublicationAsset,
) -> None:
    digest = sha256()
    written = 0
    with connection.blobopen(table, column, rowid) as target, asset.path.open(
        "rb"
    ) as source:
        while chunk := source.read(_PUBLICATION_CHUNK_BYTES):
            target.write(chunk)
            digest.update(chunk)
            written += len(chunk)
    if written != asset.size or digest.hexdigest() != asset.sha256:
        raise ValueError("Исходный DXF изменился во время публикации")


class SqliteSourcePublication:
    """No new registry: this operation's terminal record is its durable receipt."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)

    def publish(
        self,
        project: Project,
        source: SourcePublicationContent,
        operation_id: str,
        result: CadPreviewResult | CadPrepareResult,
        *,
        record_field: Literal["cad_preview", "cad_prepare"],
        stage: str,
        validate_operation: Callable[[ProjectOperation], None],
        source_components: dict[str, SourcePublicationContent] | None = None,
    ) -> ProjectOperation:
        expected = project.state_version
        now = datetime.now(UTC).isoformat()
        published = project.model_copy(
            update={"state_version": expected + 1, "updated_at": now}
        )
        if (
            result.published_state_version != published.state_version
            or result.geometry_version != project.geometry_version
        ):
            raise ValueError("Версия результата не соответствует публикации")
        # Serialize the bounded worker's graph before holding a SQLite write lock.
        # Emit UTF-8 directly. A large Unicode string followed by SQLite's UTF-8
        # conversion retains two extra representations of the whole CAD graph.
        # CAST below keeps the existing TEXT storage contract unchanged.
        payload = _PROJECT_JSON.dump_json(published)
        projection = project_projection(published).model_dump_json()
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload FROM project_operations WHERE id=?", (operation_id,)
            ).fetchone()
            if row is None:
                raise KeyError("Операция не найдена")
            operation = ProjectOperation.model_validate_json(row["payload"])
            validate_operation(operation)
            record = getattr(operation, record_field)
            if record is None:
                raise ValueError("Operation publication record is missing")
            if operation.status == OperationStatus.COMPLETED:
                if record.result != result:
                    raise ValueError("Операция уже опубликовала другой результат")
                connection.rollback()
                return operation
            if (
                operation.status != OperationStatus.RUNNING
                or operation.cancel_requested_at is not None
            ):
                raise OperationCancelled("Публикация отменена до фиксации проекта")
            if operation.project_state_version != expected:
                raise ProjectVersionConflict(
                    project.id, operation.project_state_version, expected
                )
            current = connection.execute(
                "SELECT rowid AS storage_rowid, projection, state_version FROM projects WHERE id=?",
                (project.id,),
            ).fetchone()
            if current is None:
                raise KeyError("Проект не найден")
            if current["state_version"] != expected:
                raise ProjectVersionConflict(
                    project.id, expected, current["state_version"]
                )
            previous = Project.model_validate_json(current["projection"])
            if previous.source_file is not None or previous.plan is not None:
                raise ValueError(
                    "Для подготовки источника нужен новый проект без исходника"
                )
            if isinstance(source, SourcePublicationAsset):
                connection.execute(
                    "UPDATE projects SET payload=zeroblob(?), projection=?, source=zeroblob(?), updated_at=?, state_version=? WHERE id=? AND state_version=?",
                    (
                        len(payload),
                        projection,
                        source.size,
                        now,
                        published.state_version,
                        project.id,
                        expected,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE projects SET payload=zeroblob(?), projection=?, source=?, updated_at=?, state_version=? WHERE id=? AND state_version=?",
                    (
                        len(payload),
                        projection,
                        source,
                        now,
                        published.state_version,
                        project.id,
                        expected,
                    ),
                )
            component_assets: list[tuple[int, SourcePublicationAsset]] = []
            if source_components is not None:
                connection.execute(
                    "DELETE FROM project_source_components WHERE project_id=?",
                    (project.id,),
                )
                for path, component in sorted(source_components.items()):
                    if isinstance(component, SourcePublicationAsset):
                        cursor = connection.execute(
                            "INSERT INTO project_source_components "
                            "(project_id, path, sha256, content) "
                            "VALUES (?, ?, ?, zeroblob(?))",
                            (project.id, path, component.sha256, component.size),
                        )
                        component_assets.append((cursor.lastrowid, component))
                    else:
                        connection.execute(
                            "INSERT INTO project_source_components "
                            "(project_id, path, sha256, content) VALUES (?, ?, ?, ?)",
                            (
                                project.id,
                                path,
                                sha256(component).hexdigest(),
                                component,
                            ),
                        )
            # SQLite's incremental I/O avoids binding another complete copy of
            # the large JSON. The temporary BLOB is private to this transaction;
            # the existing TEXT representation is restored before publication.
            with connection.blobopen(
                "projects", "payload", current["storage_rowid"]
            ) as blob:
                view = memoryview(payload)
                for offset in range(0, len(view), _PUBLICATION_CHUNK_BYTES):
                    blob.write(view[offset : offset + _PUBLICATION_CHUNK_BYTES])
                del view
            del payload
            if isinstance(source, SourcePublicationAsset):
                _write_asset(
                    connection,
                    "projects",
                    "source",
                    current["storage_rowid"],
                    source,
                )
            for rowid, component in component_assets:
                _write_asset(
                    connection,
                    "project_source_components",
                    "content",
                    rowid,
                    component,
                )
            connection.execute(
                "UPDATE projects SET payload=CAST(payload AS TEXT) WHERE id=?",
                (project.id,),
            )
            operation.status = OperationStatus.COMPLETED
            operation.progress = 100
            operation.progress_mode = "determinate"
            operation.stage = stage
            operation.updated_at = operation.completed_at = now
            operation = operation.model_copy(
                update={record_field: record.model_copy(update={"result": result})}
            )
            connection.execute(
                "UPDATE project_operations SET status=?, payload=?, updated_at=? WHERE id=?",
                (
                    operation.status.value,
                    operation.model_dump_json(),
                    now,
                    operation.id,
                ),
            )
            connection.commit()
            return operation
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
