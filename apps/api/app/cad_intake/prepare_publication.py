from hashlib import sha256
from pathlib import Path

from app.cad_intake.prepare_contracts import CadPrepareResult, PreparedSourceProvenance
from app.cad_intake.source_publication import SqliteSourcePublication
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
    if (
        request.intake_operation_id != provenance.intake_operation_id
        or request.manifest_sha256 != provenance.manifest_sha256
        or request.profile_version != provenance.profile_version
    ):
        raise ValueError("Источник не соответствует проверенному паспорту")


class SqliteCadProjectPublication:
    def __init__(self, database_path: str | Path) -> None:
        self.publication = SqliteSourcePublication(database_path)

    def publish(
        self,
        project: Project,
        source: bytes,
        operation_id: str,
        result: CadPrepareResult,
    ) -> ProjectOperation:
        metadata = project.source_file
        if (
            metadata is None
            or metadata.prepared_provenance is None
            or metadata.content_sha256 != sha256(source).hexdigest()
            or metadata.content_sha256 != result.source_sha256
            or metadata.prepared_provenance.source_sha256 != result.source_sha256
            or metadata.size != len(source)
            or result.source_bytes != len(source)
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
        ):
            raise ValueError("Полный источник не соответствует контракту публикации")
        provenance = metadata.prepared_provenance
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
        )
