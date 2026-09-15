"""Atomic publication on the existing project and operation journal tables."""

import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from app.cad_intake.preview_contracts import CadPreviewResult
from app.dxf_import.contracts import ImportEditability, ImportMode
from app.dxf_import.preview_contracts import CadPreviewProvenance
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.operations.progress import OperationCancelled
from app.projects.adapters import project_projection
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project


def _validate_publication(
    project: Project, source: bytes, result: CadPreviewResult
) -> CadPreviewProvenance:
    source_file = project.source_file
    if (
        source_file is None
        or source_file.preview_provenance is None
        or source_file.content_sha256 != sha256(source).hexdigest()
        or source_file.content_sha256 != result.output_sha256
        or source_file.size != len(source)
        or result.output_bytes != len(source)
        or project.import_status.mode != ImportMode.CAD_PREVIEW
        or project.import_status.editability != ImportEditability.READ_ONLY
        or project.plan is not None
        or project.geometry is not None
        or project.map_ready
        or project.planting_zones
        or any(
            area is not None
            for area in (
                project.site_area_m2,
                project.planning_area_m2,
                project.allowed_area_m2,
            )
        )
    ):
        raise ValueError("Предварительная карта не соответствует контракту публикации")
    assert source_file is not None and source_file.preview_provenance is not None
    return source_file.preview_provenance


def _validate_operation(
    operation: ProjectOperation,
    project: Project,
    provenance: CadPreviewProvenance,
) -> None:
    if (
        operation.kind != OperationKind.PREPARE_CAD_PREVIEW
        or operation.project_id != project.id
        or operation.cad_preview is None
    ):
        raise ValueError("Операция не соответствует предварительной карте")
    request = operation.cad_preview.request
    if (
        provenance.original_sha256 != request.source.source_sha256
        or provenance.converted_sha256 != request.source.normalized_sha256
        or provenance.boundary_original_sha256 != request.boundary.source_sha256
        or provenance.boundary_handle.upper() != request.boundary.handle.upper()
    ):
        raise ValueError("Происхождение карты не соответствует выбранным источникам")


class SqliteCadPreviewPublication:
    """No new registry: this operation's terminal record is its durable receipt."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)

    def publish(
        self,
        project: Project,
        source: bytes,
        operation_id: str,
        result: CadPreviewResult,
    ) -> ProjectOperation:
        provenance = _validate_publication(project, source, result)
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
        payload = published.model_dump_json()
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
            _validate_operation(operation, project, provenance)
            assert operation.cad_preview is not None
            if operation.status == OperationStatus.COMPLETED:
                if operation.cad_preview.result != result:
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
                "SELECT projection, state_version FROM projects WHERE id=?",
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
                    "Для предварительной карты нужен новый проект без исходника"
                )
            connection.execute(
                "UPDATE projects SET payload=?, projection=?, source=?, updated_at=?, state_version=? WHERE id=? AND state_version=?",
                (
                    payload,
                    projection,
                    source,
                    now,
                    published.state_version,
                    project.id,
                    expected,
                ),
            )
            operation.status = OperationStatus.COMPLETED
            operation.progress = 100
            operation.progress_mode = "determinate"
            operation.stage = "Предварительная карта открыта только для просмотра"
            operation.updated_at = operation.completed_at = now
            operation.cad_preview.result = result
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
