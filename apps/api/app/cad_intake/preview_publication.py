"""Atomic publication of a bounded editable view and its full DXF sources."""

from pathlib import Path

from app.cad_intake.prepare_contracts import PreparedSourceProvenance
from app.cad_intake.preview_contracts import CadPreviewResult
from app.cad_intake.source_publication import (
    SourcePublicationContent,
    SqliteSourcePublication,
    publication_sha256,
    publication_size,
)
from app.dxf_import.contracts import ImportEditability, ImportMode
from app.operations.contracts import OperationKind, ProjectOperation
from app.projects.contracts import Project


def _validate_publication(
    project: Project,
    source: SourcePublicationContent,
    result: CadPreviewResult,
    source_components: dict[str, SourcePublicationContent],
) -> PreparedSourceProvenance:
    metadata = project.source_file
    provenance = metadata.prepared_provenance if metadata else None
    drawings = provenance.drawings if provenance else []
    expected = {
        item.path: item
        for item in drawings
        if provenance is not None and item.path != provenance.entry
    }
    aoi_drawings = {item.path: item for item in provenance.aoi.drawings} if provenance and provenance.aoi else {}
    if (
        metadata is None
        or provenance is None
        or provenance.aoi is None
        or metadata.preview_provenance is not None
        or metadata.name != result.source_name
        or metadata.content_sha256 != publication_sha256(source)
        or metadata.content_sha256 != result.source_sha256
        or provenance.entry != result.source_name
        or provenance.source_sha256 != result.source_sha256
        or metadata.size != publication_size(source)
        or result.source_bytes != publication_size(source)
        or result.source_count != len(drawings)
        or result.source_bytes_total != sum(item.source_bytes for item in drawings)
        or len({item.path for item in drawings}) != len(drawings)
        or set(aoi_drawings) != {item.path for item in drawings}
        or aoi_drawings[provenance.entry].original_sha256
        != result.source_original_sha256
        or set(source_components) != set(expected)
        or any(
            publication_sha256(source_components[path]) != item.source_sha256
            or publication_size(source_components[path]) != item.source_bytes
            for path, item in expected.items()
        )
        or project.import_status.mode != ImportMode.SOURCE_DXF
        or project.import_status.editability != ImportEditability.EDITABLE
        or project.source_review is None
        or project.plan is not None
        or project.source_geometry is not None
        or project.geometry is None
        or not project.map_ready
        or project.planting_zones
        or result.feature_count
        != len(project.geometry.feature_collection.get("features", []))
    ):
        raise ValueError("Рабочая территория не соответствует контракту публикации")
    return provenance


def _validate_operation(
    operation: ProjectOperation,
    project: Project,
    provenance: PreparedSourceProvenance,
    result: CadPreviewResult,
) -> None:
    if (
        operation.kind != OperationKind.PREPARE_CAD_PREVIEW
        or operation.project_id != project.id
        or operation.cad_preview is None
        or provenance.aoi is None
    ):
        raise ValueError("Операция не соответствует рабочей территории")
    request = operation.cad_preview.request
    aoi = provenance.aoi
    selected = next(
        (item for item in aoi.drawings if item.path == request.source.path), None
    )
    if (
        provenance.intake_operation_id != request.intake_operation_id
        or provenance.manifest_sha256 != request.manifest_sha256
        or aoi.boundary_path != request.boundary.path
        or aoi.boundary_original_sha256 != request.boundary.source_sha256
        or aoi.boundary_converted_sha256 != request.boundary.normalized_sha256
        or aoi.boundary_handle.upper() != request.boundary.handle.upper()
        or selected is None
        or selected.original_sha256 != request.source.source_sha256
        or selected.converted_sha256 != request.source.normalized_sha256
        or selected.fragment_sha256 != result.output_sha256
        or selected.fragment_bytes != result.output_bytes
        or selected.manifest_sha256 != result.manifest_sha256
    ):
        raise ValueError("Происхождение территории не соответствует операции")


class SqliteCadPreviewPublication:
    def __init__(self, database_path: str | Path) -> None:
        self.publication = SqliteSourcePublication(database_path)

    def publish(
        self,
        project: Project,
        source: SourcePublicationContent,
        operation_id: str,
        result: CadPreviewResult,
        source_components: dict[str, SourcePublicationContent] | None = None,
    ) -> ProjectOperation:
        components = source_components or {}
        provenance = _validate_publication(project, source, result, components)
        return self.publication.publish(
            project,
            source,
            operation_id,
            result,
            record_field="cad_preview",
            stage="Рабочая территория открыта; назначения слоёв требуют проверки",
            validate_operation=lambda operation: _validate_operation(
                operation, project, provenance, result
            ),
            source_components=components,
        )
