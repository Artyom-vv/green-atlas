"""Atomic publication on the existing project and operation journal tables."""

from hashlib import sha256
from pathlib import Path

from app.cad_intake.preview_contracts import CadPreviewResult
from app.cad_intake.source_publication import SqliteSourcePublication
from app.dxf_import.contracts import ImportEditability, ImportMode
from app.dxf_import.preview_contracts import CadPreviewProvenance
from app.operations.contracts import OperationKind, ProjectOperation
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
    def __init__(self, database_path: str | Path) -> None:
        self.publication = SqliteSourcePublication(database_path)

    def publish(
        self,
        project: Project,
        source: bytes,
        operation_id: str,
        result: CadPreviewResult,
    ) -> ProjectOperation:
        provenance = _validate_publication(project, source, result)
        return self.publication.publish(
            project,
            source,
            operation_id,
            result,
            record_field="cad_preview",
            stage="Предварительная карта открыта только для просмотра",
            validate_operation=lambda operation: _validate_operation(
                operation, project, provenance
            ),
        )
