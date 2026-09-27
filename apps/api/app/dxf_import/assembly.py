"""Compose imported state without reading files or publishing a project."""

from hashlib import sha256

from app.dxf_import.admission import cad_preview_status
from app.dxf_import.contracts import (
    DxfImportResult,
    ImportEditability,
    ImportMode,
    ImportStatus,
    SourceFile,
)
from app.projects.contracts import Project, ProjectStatus


def assemble_imported_project(
    project: Project,
    filename: str,
    content: bytes | bytearray,
    imported: DxfImportResult,
    imported_at: str,
) -> Project:
    plain_fallback = any(
        layer.source_name.upper() in {"GREEN_ATLAS_TREES", "GREEN_ATLAS_SHRUBS"}
        for layer in imported.layers
    )
    status = ImportStatus(
        mode=ImportMode.PLAIN_DXF_FALLBACK if plain_fallback else ImportMode.SOURCE_DXF,
        editability=ImportEditability.READ_ONLY
        if plain_fallback
        else ImportEditability.EDITABLE,
        message=(
            "Обычный DXF с проектными слоями открыт только для просмотра: "
            "для продолжения загрузите полный ZIP-пакет выпуска."
            if plain_fallback
            else "Исходный DXF доступен для подготовки редактируемого плана."
        ),
    )
    if imported.preview_provenance is not None:
        status = cad_preview_status()
    warnings = list(imported.warnings)
    if status.editability == ImportEditability.READ_ONLY:
        warnings.append(status.message)
    source = SourceFile(
        name=filename,
        content_sha256=sha256(content).hexdigest(),
        size=len(content),
        imported_at=imported_at,
        dxf_version=imported.dxf_version,
        units=imported.units,
        units_assumed=imported.units_assumed,
        entity_count=imported.entity_count,
        bounds=imported.bounds,
        warnings=warnings,
        preview_provenance=imported.preview_provenance,
        cad_snapshot_provenance=imported.cad_snapshot_provenance,
    )
    return project.model_copy(
        update={
            "source_file": source,
            "source_review": None,
            "import_status": status,
            "layers": imported.layers,
            "source_geometry": imported.geometry,
            "coordinate_reference": imported.coordinate_reference,
            "geometry": None,
            "map_ready": False,
            "geometry_version": project.geometry_version + 1,
            "planting_zones": [],
            "site_area_m2": None,
            "planning_area_m2": None,
            "allowed_area_m2": None,
            "plan": None,
            "status": ProjectStatus.IMPORTED,
        }
    )
