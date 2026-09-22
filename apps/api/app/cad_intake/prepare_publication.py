from pathlib import Path

from app.cad_intake.prepare_contracts import CadPrepareResult, PreparedSourceProvenance
from app.cad_intake.source_publication import (
    SourcePublicationContent,
    SqliteSourcePublication,
    publication_sha256,
    publication_size,
)
from app.dxf_import.contracts import ImportEditability, ImportMode
from app.operations.contracts import OperationKind, ProjectOperation
from app.projects.contracts import Project


def _validate_operation(
    operation: ProjectOperation, project: Project, provenance: PreparedSourceProvenance
) -> None:
    if (
        operation.kind != OperationKind.PREPARE_CAD_PROJECT
        or operation.project_id != project.id
        or operation.cad_prepare is None
    ):
        raise ValueError("Операция не соответствует подготовленному источнику")
    request = operation.cad_prepare.request
    requested_snapshot_paths = {
        item.drawing_path for item in request.additional_snapshots
    }
    if request.cad_snapshot is not None:
        requested_snapshot_paths.add(provenance.entry)
    published_snapshot_paths = {
        item.path for item in provenance.drawings if item.cad_snapshot is not None
    }
    if (
        request.intake_operation_id != provenance.intake_operation_id
        or request.manifest_sha256 != provenance.manifest_sha256
        or request.profile_version != provenance.profile_version
        or request.opening_review != provenance.opening_review
        or requested_snapshot_paths != published_snapshot_paths
    ):
        raise ValueError("Источник не соответствует проверенному паспорту")
    snapshot = project.source_file.cad_snapshot_provenance if project.source_file else None
    if (request.cad_snapshot is None) != (snapshot is None):
        raise ValueError("CAD snapshot не соответствует операции подготовки")


class SqliteCadProjectPublication:
    def __init__(self, database_path: str | Path) -> None:
        self.publication = SqliteSourcePublication(database_path)

    def publish(
        self,
        project: Project,
        source: SourcePublicationContent,
        operation_id: str,
        result: CadPrepareResult,
        source_components: dict[str, SourcePublicationContent] | None = None,
    ) -> ProjectOperation:
        metadata = project.source_file
        provenance = metadata.prepared_provenance if metadata else None
        drawings = provenance.drawings if provenance else []
        components = source_components or {}
        drawing_by_path = {item.path: item for item in drawings}
        expected_components = {
            item.path: item
            for item in drawings
            if provenance is not None and item.path != provenance.entry
        }
        if (
            metadata is None
            or provenance is None
            or metadata.content_sha256 != publication_sha256(source)
            or metadata.content_sha256 != result.source_sha256
            or provenance.source_sha256 != result.source_sha256
            or metadata.size != publication_size(source)
            or result.source_bytes != publication_size(source)
            or result.source_count != (len(drawings) if drawings else 1)
            or result.source_bytes_total
            != (sum(item.source_bytes for item in drawings) if drawings else len(source))
            or len(drawing_by_path) != len(drawings)
            or set(components) != set(expected_components)
            or any(
                publication_sha256(components[path]) != item.source_sha256
                or publication_size(components[path]) != item.source_bytes
                for path, item in expected_components.items()
            )
            or project.import_status.editability != ImportEditability.EDITABLE
            or project.import_status.mode != ImportMode.SOURCE_DXF
            or project.source_review is None
            or project.plan is not None
            or project.planting_zones
            or project.geometry is None
            or not project.map_ready
            or project.source_geometry is not None
            or result.feature_count
            != len(project.geometry.feature_collection["features"])
            or (
                metadata.cad_snapshot_provenance is None
                and (
                    result.cad_snapshot_payload_sha256 is not None
                    or result.cad_snapshot_native_geometry is not None
                )
            )
            or (
                metadata.cad_snapshot_provenance is not None
                and (
                    result.cad_snapshot_payload_sha256
                    != metadata.cad_snapshot_provenance.payload_sha256
                    or result.cad_snapshot_native_geometry
                    != metadata.cad_snapshot_provenance.native_geometry
                )
            )
        ):
            raise ValueError("Полный источник не соответствует контракту публикации")
        return self.publication.publish(
            project,
            source,
            operation_id,
            result,
            record_field="cad_prepare",
            stage="Полный источник открыт для редактирования; ограничения требуют проверки",
            validate_operation=lambda operation: _validate_operation(
                operation, project, provenance
            ),
            source_components=components,
        )
