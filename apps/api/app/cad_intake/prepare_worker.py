"""A full, resource-bounded DXF read; no AOI, sampling, or SDK rewrite."""

import gc
import json
import sys
from hashlib import sha256
from pathlib import Path
from threading import RLock

from app.cad_import.cache import file_sha256
from app.cad_intake.asset_sources import resolve_asset_source
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.prepare_admission import (
    require_empty_source_project,
    validate_prepared_intake,
)
from app.cad_intake.prepare_contracts import CadPrepareResult, PreparedSourceProvenance
from app.cad_intake.prepare_policy import PREPARE_POLICY
from app.cad_intake.prepare_publication import SqliteCadProjectPublication
from app.cad_intake.work import CadWork
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.assembly import assemble_imported_project
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.editor_source import open_source_editor
from app.operations.adapters import SqliteOperationRepository
from app.operations.contracts import OperationKind, OperationStatus
from app.operations.lifecycle import OperationLifecycle
from app.operations.progress import OperationCancelled, WorkProgress
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.shared.identity import random_id, utc_now


def _execute(work: CadWork, lifecycle: OperationLifecycle) -> None:
    operation = lifecycle.operations.get(work.operation_id)
    if (
        operation.kind != OperationKind.PREPARE_CAD_PROJECT
        or operation.status != OperationStatus.RUNNING
        or operation.cad_prepare is None
    ):
        raise ValueError("Операция не готова к подготовке исходника")
    lifecycle.check_cancelled(operation.id)
    project = lifecycle.repository.get(operation.project_id, lightweight=True)
    if project.state_version != operation.project_state_version:
        raise ProjectVersionConflict(
            project.id, operation.project_state_version, project.state_version
        )
    require_empty_source_project(project)
    request = operation.cad_prepare.request
    intake = lifecycle.get(project.id, request.intake_operation_id)
    passport = validate_prepared_intake(intake, project.id, request)
    config = CadIntakeConfig(
        (AllowedCadRoot(passport.root_id, passport.root_id, work.root),),
        work.storage,
        None,
        PREPARE_POLICY,
    )
    source = resolve_asset_source(config, intake, project.id)
    with source.asset.open("rb") as stream:
        content = stream.read(PREPARE_POLICY.max_source_bytes + 1)
    digest = sha256(content).hexdigest()
    if (
        len(content) > PREPARE_POLICY.max_source_bytes
        or digest != source.drawing.source_sha256
    ):
        raise ValueError(
            "Исходник изменился после проверки либо превысил бюджет чтения"
        )
    lifecycle.report(
        operation.id,
        WorkProgress(stage="Читаем всю геометрию DXF", fraction=None),
        0,
        99,
    )
    imported = EzdxfReader(capacity=SourceGeometryCapacity.process_bounded()).read(
        source.asset.name, content
    )
    # ezdxf's private Drawing and virtual entities contain reference cycles.
    # Their lifetime ends here. Reclaim those unreachable SDK objects before
    # allocating the publication JSON, while retaining every normalized feature.
    # This runs only at the phase boundary of the isolated preparation worker.
    gc.collect()
    project = assemble_imported_project(
        project, source.asset.name, content, imported, utc_now().isoformat()
    )
    del imported
    assert project.source_file is not None
    project.source_file.prepared_provenance = PreparedSourceProvenance(
        intake_operation_id=intake.id,
        manifest_sha256=request.manifest_sha256,
        profile_version=request.profile_version,
        entry=passport.entry,
        source_sha256=digest,
    )
    project = open_source_editor(project)
    assert project.geometry is not None
    result = CadPrepareResult(
        published_state_version=project.state_version + 1,
        geometry_version=project.geometry_version,
        source_sha256=digest,
        source_bytes=len(content),
        feature_count=len(project.geometry.feature_collection["features"]),
    )
    lifecycle.check_cancelled(operation.id)
    # Re-resolve the signed manifest and re-hash the source just before publication.
    current = resolve_asset_source(
        config, lifecycle.get(project.id, intake.id), project.id
    )
    if file_sha256(current.asset) != digest:
        raise ValueError("Исходник изменился во время подготовки")
    lifecycle.report(
        operation.id,
        WorkProgress(stage="Сохраняем полный редактируемый источник", fraction=None),
        0,
        99,
    )
    receipt = SqliteCadProjectPublication(work.database).publish(
        project, content, operation.id, result
    )
    work.receipt.write_text(
        json.dumps({"operation_id": receipt.id, "status": receipt.status.value}),
        encoding="utf-8",
    )


def execute(work: CadWork) -> None:
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
                work.operation_id, "Проект изменился", "PROJECT_VERSION_CONFLICT", error
            )
            raise
    finally:
        operations.close()
        projects.close()


if __name__ == "__main__":
    execute(CadWork.model_validate_json(Path(sys.argv[1]).read_text(encoding="utf-8")))
