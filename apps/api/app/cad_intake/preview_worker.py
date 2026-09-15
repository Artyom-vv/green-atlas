"""Bounded CAD → AOI → normalized source → atomic read-only publication."""

import gc
import json
import sys
from pathlib import Path
from threading import RLock

from app.cad_import.aoi import prepare_aoi
from app.cad_import.cache import file_sha256
from app.cad_intake.preview_admission import require_empty_preview_project
from app.cad_intake.preview_contracts import CadPreviewResult
from app.cad_intake.preview_publication import SqliteCadPreviewPublication
from app.cad_intake.preview_sources import resolve_preview_sources
from app.cad_intake.preview_work import PreviewWork
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.assembly import assemble_imported_project
from app.dxf_import.capacity import SourceCapacityExceeded
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES
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
    prepared = prepare_aoi(sources.request, work.storage / "previews")
    lifecycle.check_cancelled(operation.id)
    if prepared.manifest.request != sources.request:
        raise ValueError("Рабочая территория не совпадает с выбранными источниками")
    drawing = prepared.drawing_path
    if drawing.stat().st_size > MAX_DXF_CONTENT_BYTES:
        raise ValueError("Подготовленная территория превышает бюджет исходника")
    warnings = list(
        dict.fromkeys([*sources.package_warnings, *prepared.manifest.warnings])
    )
    result = CadPreviewResult(
        published_state_version=project.state_version + 1,
        geometry_version=project.geometry_version + 1,
        source_name=f"{Path(request.source.path).stem}-aoi-{request.boundary.handle.upper()}.dxf",
        output_sha256=prepared.manifest.output_sha256,
        output_bytes=prepared.manifest.output_bytes,
        manifest_sha256=file_sha256(prepared.manifest_path),
        source_units=prepared.manifest.source_units,
        boundary_mask_area_m2=prepared.manifest.boundary_area_m2,
        selected_entities=prepared.manifest.selected_modelspace_entities,
        unknown_bounds=prepared.manifest.unknown_bounds,
        warnings=warnings[:50],
        warning_count=len(warnings),
    )
    # Large source-link manifests stay private and do not coexist with the
    # normalized geometry graph longer than necessary inside the bounded child.
    del prepared
    gc.collect()
    lifecycle.report(
        operation.id,
        WorkProgress(stage="Готовим геометрию предварительной карты", fraction=None),
        0,
        99,
    )
    with drawing.open("rb") as stream:
        content = stream.read(MAX_DXF_CONTENT_BYTES + 1)
    if len(content) > MAX_DXF_CONTENT_BYTES:
        raise ValueError("Рабочая территория превышает бюджет чтения")
    imported = EzdxfReader().read(result.source_name, content)
    project = assemble_imported_project(
        project, result.source_name, content, imported, utc_now().isoformat()
    )
    del imported
    lifecycle.check_cancelled(operation.id)
    sources.verify()
    lifecycle.report(
        operation.id,
        WorkProgress(stage="Открываем карту только для просмотра", fraction=None),
        0,
        99,
    )
    receipt = SqliteCadPreviewPublication(work.database).publish(
        project, content, operation.id, result
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
