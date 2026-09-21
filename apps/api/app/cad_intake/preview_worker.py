"""Bounded CAD → editable AOI graph + immutable full-source publication."""

import gc
import json
import sys
from pathlib import Path
from threading import RLock

from app.cad_import.aoi import prepare_aoi
from app.cad_import.aoi_contracts import AoiRequest, AoiSource
from app.cad_import.cache import file_sha256
from app.cad_intake.asset_sources import resolve_asset_source
from app.cad_intake.composition import ImportedDrawing, compose_dxf_imports
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.prepare_contracts import (
    PreparedAoiDrawingProvenance,
    PreparedAoiProvenance,
    PreparedDrawingProvenance,
    PreparedSourceProvenance,
)
from app.cad_intake.prepare_policy import PREPARE_POLICY
from app.cad_intake.preview_admission import require_empty_preview_project
from app.cad_intake.preview_contracts import CadPreviewResult
from app.cad_intake.preview_publication import SqliteCadPreviewPublication
from app.cad_intake.preview_sources import resolve_preview_sources
from app.cad_intake.preview_work import PreviewWork
from app.cad_intake.source_publication import SourcePublicationAsset
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.assembly import assemble_imported_project
from app.dxf_import.capacity import SourceCapacityExceeded
from app.dxf_import.contracts import ImportStatus
from app.dxf_import.editor_source import open_source_editor
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES
from app.dxf_import.preview_contracts import CAD_PREVIEW_MESSAGE
from app.operations.adapters import SqliteOperationRepository
from app.operations.contracts import OperationKind, OperationStatus
from app.operations.lifecycle import OperationLifecycle
from app.operations.progress import OperationCancelled, WorkProgress
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.shared.identity import random_id, utc_now


def _execute(work: PreviewWork, lifecycle: OperationLifecycle) -> None:
    operation = lifecycle.operations.get(work.operation_id)
    if (
        operation.kind != OperationKind.PREPARE_CAD_PREVIEW
        or operation.status != OperationStatus.RUNNING
        or operation.cad_preview is None
    ):
        raise ValueError("Операция не готова к подготовке территории")
    lifecycle.check_cancelled(operation.id)
    project = lifecycle.repository.get(operation.project_id, lightweight=True)
    if project.state_version != operation.project_state_version:
        raise ProjectVersionConflict(
            project.id, operation.project_state_version, project.state_version
        )
    require_empty_preview_project(project)
    request = operation.cad_preview.request
    intake = lifecycle.get(project.id, request.intake_operation_id)
    sources = resolve_preview_sources(work, operation, intake, request)
    lifecycle.report(
        operation.id,
        WorkProgress(stage="Выделяем территорию по авторскому контуру", fraction=None),
        0,
        99,
    )
    if intake.cad_intake is None or intake.cad_intake.passport is None:
        raise ValueError("Паспорт исходного комплекта отсутствует")
    passport = intake.cad_intake.passport
    config = CadIntakeConfig(
        (AllowedCadRoot(passport.root_id, passport.root_id, work.root),),
        work.storage,
        None,
        PREPARE_POLICY,
    )
    entries = passport.entries or [passport.entry]
    imported_drawings = []
    publication_assets = {}
    prepared_sources = []
    aoi_sources = []
    warnings = list(sources.package_warnings)
    primary_fragment = None
    primary_imported = None
    selected_entities = 0
    unknown_bounds = 0
    for index, drawing_path in enumerate(entries, start=1):
        lifecycle.check_cancelled(operation.id)
        lifecycle.report(
            operation.id,
            WorkProgress(
                stage=f"Выделяем территорию: {index} из {len(entries)}",
                fraction=None,
            ),
            0,
            99,
        )
        source = resolve_asset_source(config, intake, project.id, drawing_path)
        drawing = source.drawing
        if drawing.source_sha256 is None or drawing.normalized_sha256 is None:
            raise ValueError("Для DXF не зафиксированы контрольные суммы")
        publication_asset = SourcePublicationAsset.checked(source.asset)
        if publication_asset.sha256 != drawing.normalized_sha256:
            raise ValueError("DXF изменился после проверки комплекта")
        aoi_request = AoiRequest(
            source=AoiSource(
                original_path=source.original,
                original_sha256=drawing.source_sha256,
                converted_path=source.asset,
                converted_sha256=drawing.normalized_sha256,
            ),
            boundary_source=sources.request.boundary_source,
            boundary_handle=request.boundary.handle,
        )
        prepared = prepare_aoi(aoi_request, work.storage / "previews")
        if prepared.manifest.request != aoi_request:
            raise ValueError("Рабочая территория не совпадает с выбранными источниками")
        if prepared.drawing_path.stat().st_size > MAX_DXF_CONTENT_BYTES:
            raise ValueError("Подготовленная территория превышает бюджет исходника")
        imported = EzdxfReader().read_prepared_file(prepared.drawing_path)
        imported_drawings.append(
            ImportedDrawing(
                path=drawing_path,
                imported=imported,
                source_sha256=publication_asset.sha256,
                source_bytes=publication_asset.size,
            )
        )
        publication_assets[drawing_path] = publication_asset
        prepared_sources.append(
            PreparedDrawingProvenance(
                path=drawing_path,
                source_sha256=publication_asset.sha256,
                source_bytes=publication_asset.size,
            )
        )
        aoi_sources.append(
            PreparedAoiDrawingProvenance(
                path=drawing_path,
                original_sha256=drawing.source_sha256,
                converted_sha256=drawing.normalized_sha256,
                fragment_sha256=prepared.manifest.output_sha256,
                fragment_bytes=prepared.manifest.output_bytes,
                manifest_sha256=file_sha256(prepared.manifest_path),
                selected_entities=prepared.manifest.selected_modelspace_entities,
                unknown_bounds=prepared.manifest.unknown_bounds,
            )
        )
        selected_entities += prepared.manifest.selected_modelspace_entities
        unknown_bounds += prepared.manifest.unknown_bounds
        if drawing_path == request.source.path:
            primary_fragment = (
                drawing_path,
                prepared.drawing_path,
                prepared.manifest.output_sha256,
                prepared.manifest.output_bytes,
                file_sha256(prepared.manifest_path),
                prepared.manifest.source_units,
                prepared.manifest.boundary_area_m2,
            )
            primary_imported = imported
        warnings.extend(f"{drawing_path}: {item}" for item in prepared.manifest.warnings)
        del prepared
        gc.collect()
    lifecycle.check_cancelled(operation.id)
    if primary_fragment is None or primary_imported is None:
        raise ValueError("Выбранный основной DXF отсутствует в комплекте")
    if len(imported_drawings) == 1:
        imported = primary_imported
    else:
        imported = compose_dxf_imports(imported_drawings).imported.model_copy(
            update={"preview_provenance": primary_imported.preview_provenance}
        )
    warnings = list(dict.fromkeys(warnings))
    (
        primary_path,
        drawing,
        fragment_sha256,
        fragment_bytes,
        fragment_manifest_sha256,
        source_units,
        boundary_area_m2,
    ) = primary_fragment
    fragment_name = (
        f"{Path(primary_path).stem}-aoi-{request.boundary.handle.upper()}.dxf"
    )
    # Large source-link manifests stay private and do not coexist with the
    # normalized geometry graph longer than necessary inside the bounded child.
    del imported_drawings, primary_fragment, primary_imported
    gc.collect()
    lifecycle.report(
        operation.id,
        WorkProgress(stage="Готовим геометрию рабочей территории", fraction=None),
        0,
        99,
    )
    with drawing.open("rb") as stream:
        content = stream.read(MAX_DXF_CONTENT_BYTES + 1)
    if len(content) > MAX_DXF_CONTENT_BYTES:
        raise ValueError("Рабочая территория превышает бюджет чтения")
    project = assemble_imported_project(
        project, fragment_name, content, imported, utc_now().isoformat()
    )
    del imported
    primary_source = publication_assets[passport.entry]
    assert project.source_file is not None
    project.source_file = project.source_file.model_copy(
        update={
            "name": passport.entry,
            "content_sha256": primary_source.sha256,
            "size": primary_source.size,
            "warnings": [
                warning
                for warning in project.source_file.warnings
                if warning != CAD_PREVIEW_MESSAGE
            ],
            "preview_provenance": None,
            "prepared_provenance": PreparedSourceProvenance(
                intake_operation_id=intake.id,
                manifest_sha256=request.manifest_sha256,
                entry=passport.entry,
                source_sha256=primary_source.sha256,
                drawings=prepared_sources,
                aoi=PreparedAoiProvenance(
                    boundary_path=request.boundary.path,
                    boundary_original_sha256=request.boundary.source_sha256,
                    boundary_converted_sha256=request.boundary.normalized_sha256,
                    boundary_handle=request.boundary.handle,
                    drawings=aoi_sources,
                ),
            ),
        }
    )
    project.import_status = ImportStatus()
    project = open_source_editor(project)
    assert project.geometry is not None
    result = CadPreviewResult(
        published_state_version=project.state_version + 1,
        geometry_version=project.geometry_version,
        source_name=passport.entry,
        source_original_sha256=next(
            item.original_sha256
            for item in aoi_sources
            if item.path == passport.entry
        ),
        source_sha256=primary_source.sha256,
        source_bytes=primary_source.size,
        source_count=len(publication_assets),
        source_bytes_total=sum(item.size for item in publication_assets.values()),
        feature_count=len(project.geometry.feature_collection["features"]),
        output_sha256=fragment_sha256,
        output_bytes=fragment_bytes,
        manifest_sha256=fragment_manifest_sha256,
        source_units=source_units,
        boundary_mask_area_m2=boundary_area_m2,
        selected_entities=selected_entities,
        unknown_bounds=unknown_bounds,
        warnings=warnings[:50],
        warning_count=len(warnings),
    )
    lifecycle.check_cancelled(operation.id)
    sources.verify()
    lifecycle.report(
        operation.id,
        WorkProgress(stage="Открываем слои рабочей территории", fraction=None),
        0,
        99,
    )
    receipt = SqliteCadPreviewPublication(work.database).publish(
        project,
        primary_source,
        operation.id,
        result,
        source_components={
            path: asset
            for path, asset in publication_assets.items()
            if path != passport.entry
        },
    )
    work.receipt.write_text(
        json.dumps({"operation_id": receipt.id, "status": receipt.status.value}),
        encoding="utf-8",
    )


def execute(work: PreviewWork) -> None:
    # Never create_runtime here: its recovery would interrupt this running job.
    projects = SqliteProjectRepository(work.database)
    operations = SqliteOperationRepository(work.database, recover=False)
    try:
        lifecycle = OperationLifecycle(
            projects, operations, RLock(), utc_now, random_id
        )
        try:
            _execute(work, lifecycle)
        except OperationCancelled:
            lifecycle.complete_cancellation(work.operation_id)
            raise
        except ProjectVersionConflict as error:
            lifecycle.fail(
                work.operation_id,
                "Карта не открыта: проект изменился",
                "PROJECT_VERSION_CONFLICT",
                error,
            )
            raise
        except SourceCapacityExceeded:
            lifecycle.fail(
                work.operation_id,
                "Территория превышает бюджет карты",
                "SOURCE_CAPACITY_EXCEEDED",
                ValueError(
                    "Геометрия выбранной территории превышает бюджет подготовки. Исходник и проект не изменены; выберите меньший авторский контур."
                ),
            )
            raise
    finally:
        operations.close()
        projects.close()


if __name__ == "__main__":
    execute(
        PreviewWork.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8"))
    )
