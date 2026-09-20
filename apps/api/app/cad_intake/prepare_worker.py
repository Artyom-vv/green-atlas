"""A full, resource-bounded DXF read; no AOI, sampling, or SDK rewrite."""

import gc
import json
import sys
from hashlib import sha256
from pathlib import Path
from threading import RLock

from app.cad_bridge.provider import build_dxf_import_from_snapshot_path
from app.cad_import.cache import file_sha256
from app.cad_intake.asset_sources import resolve_asset_source
from app.cad_intake.composition import ImportedDrawing, compose_dxf_imports
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadPackagePassport
from app.cad_intake.prepare_admission import (
    require_empty_source_project,
    validate_prepared_intake,
)
from app.cad_intake.prepare_contracts import (
    CadPrepareRequest,
    CadPrepareResult,
    CadSnapshotSelection,
    PreparedDrawingProvenance,
    PreparedSourceProvenance,
)
from app.cad_intake.prepare_policy import PREPARE_POLICY
from app.cad_intake.prepare_publication import SqliteCadProjectPublication
from app.cad_intake.snapshot_source import (
    resolve_cad_snapshot,
    verify_cad_snapshot_dependency_records,
    verify_cad_snapshot_fingerprint,
)
from app.cad_intake.source_publication import SourcePublicationAsset
from app.cad_intake.work import CadWork
from app.cad_intake.worker import SnapshotInventory, _read_snapshot
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


def _expected_xref_dependencies(
    passport: CadPackagePassport, root_entry: str
) -> dict[str, str]:
    drawing_hashes = {
        item.path: item.source_sha256
        for item in passport.drawings
        if item.source_sha256 is not None
    }
    references_by_owner: dict[str, list] = {}
    for reference in passport.references:
        references_by_owner.setdefault(reference.owner, []).append(reference)
    expected: dict[str, str] = {}
    pending = [root_entry]
    visited: set[str] = set()
    while pending:
        owner = pending.pop()
        if owner in visited:
            continue
        visited.add(owner)
        references = references_by_owner.get(owner, [])
        for reference in references:
            if reference.status != "resolved" or reference.target is None:
                raise ValueError("DXF-комплект содержит неразрешённый XREF")
            digest = reference.expected_sha256 or drawing_hashes.get(reference.target)
            if digest is None:
                raise ValueError("Для XREF не зафиксирован SHA-256 исходного файла")
            previous = expected.setdefault(reference.target, digest)
            if previous != digest:
                raise ValueError("Паспорт содержит противоречивые SHA-256 одного XREF")
            pending.append(reference.target)
    return expected


def _snapshot_selections(
    passport: CadPackagePassport,
    request: CadPrepareRequest,
) -> dict[str, CadSnapshotSelection]:
    selections = {
        item.drawing_path: item.snapshot for item in request.additional_snapshots
    }
    if request.cad_snapshot is not None:
        selections[passport.entry] = request.cad_snapshot
    return selections


def _read_source_bytes(path: Path, expected_sha256: str) -> tuple[bytes, str]:
    with path.open("rb") as stream:
        content = stream.read(PREPARE_POLICY.max_source_bytes + 1)
    digest = sha256(content).hexdigest()
    if len(content) > PREPARE_POLICY.max_source_bytes or digest != expected_sha256:
        raise ValueError(
            "Исходник изменился после проверки либо превысил бюджет чтения"
        )
    return content, digest


def _execute(work: CadWork, lifecycle: OperationLifecycle) -> None:
    operation = lifecycle.operations.get(work.operation_id)
    if (
        operation.kind != OperationKind.PREPARE_CAD_PROJECT
        or operation.status != OperationStatus.RUNNING
        or operation.cad_prepare is None
    ):
        raise ValueError(
            "Операция не готова к подготовке исходника: "
            f"kind={operation.kind.value}, status={operation.status.value}, "
            f"request={'present' if operation.cad_prepare is not None else 'missing'}"
        )
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
    canonical_root = work.root.resolve(strict=True)
    config = CadIntakeConfig(
        (AllowedCadRoot(passport.root_id, passport.root_id, canonical_root),),
        work.storage,
        None,
        PREPARE_POLICY,
    )
    entries = passport.entries or [passport.entry]
    selections = _snapshot_selections(passport, request)
    lifecycle.report(
        operation.id,
        WorkProgress(
            stage=f"Проверяем геометрию AutoCAD: 0 из {len(entries)}",
            fraction=None,
        ),
        0,
        99,
    )
    contents: dict[str, bytes] = {}
    source_assets: dict[str, SourcePublicationAsset] = {}
    digests: dict[str, str] = {}
    imported_by_path = {}
    snapshot_sources = {}
    snapshots: dict[str, SnapshotInventory] = {}
    verified_by_path: dict[str, dict[str, str]] = {}
    for index, drawing_path in enumerate(entries, start=1):
        lifecycle.check_cancelled(operation.id)
        source = resolve_asset_source(config, intake, project.id, drawing_path)
        if source.drawing.source_sha256 is None:
            raise ValueError("В паспорте не зафиксирован SHA-256 исходного DXF")
        content, digest = _read_source_bytes(source.asset, source.drawing.source_sha256)
        expected_dependencies = _expected_xref_dependencies(passport, drawing_path)
        selection = selections.get(drawing_path)
        if selection is None:
            raise ValueError("Для каждого DXF нужен проверенный AutoCAD snapshot")
        snapshot_source = resolve_cad_snapshot(canonical_root, selection)
        snapshot = _read_snapshot(snapshot_source.path, work.storage / "cache")
        verified_dependencies = verify_cad_snapshot_dependency_records(
            canonical_root, snapshot.dependencies
        )
        if verified_dependencies != expected_dependencies:
            raise ValueError("CAD snapshot не покрывает точный набор XREF из паспорта")
        imported_drawing = build_dxf_import_from_snapshot_path(
            snapshot_source.path,
            source=snapshot.source,
            extraction=snapshot.extraction,
            dependencies=snapshot.dependencies,
            summary=snapshot.summary,
            source_sha256=digest,
            scratch_root=work.storage / "cache",
            capacity=SourceGeometryCapacity.process_bounded(),
            verified_dependencies=verified_dependencies,
        )
        snapshot_sources[drawing_path] = snapshot_source
        snapshots[drawing_path] = snapshot
        verified_by_path[drawing_path] = verified_dependencies
        contents[drawing_path] = content
        source_assets[drawing_path] = SourcePublicationAsset.checked(source.asset)
        digests[drawing_path] = digest
        imported_by_path[drawing_path] = imported_drawing
        lifecycle.report(
            operation.id,
            WorkProgress(
                stage=f"Проверяем геометрию AutoCAD: {index} из {len(entries)}",
                fraction=None,
            ),
            0,
            99,
        )
        gc.collect()

    if len(entries) == 1:
        imported = imported_by_path[passport.entry]
    else:
        imported = compose_dxf_imports(
            [
                ImportedDrawing(
                    path=path,
                    imported=imported_by_path[path],
                    source_sha256=digests[path],
                    source_bytes=len(contents[path]),
                )
                for path in entries
            ],
            capacity=SourceGeometryCapacity.process_bounded(),
        ).imported
    content = contents[passport.entry]
    digest = digests[passport.entry]
    primary_snapshot_provenance = imported_by_path[
        passport.entry
    ].cad_snapshot_provenance
    # Release the admitted snapshot models before allocating publication JSON.
    gc.collect()
    project = assemble_imported_project(
        project, passport.entry, content, imported, utc_now().isoformat()
    )
    del imported
    assert project.source_file is not None
    project.source_file.cad_snapshot_provenance = primary_snapshot_provenance
    project.source_file.prepared_provenance = PreparedSourceProvenance(
        intake_operation_id=intake.id,
        manifest_sha256=request.manifest_sha256,
        profile_version=request.profile_version,
        entry=passport.entry,
        source_sha256=digest,
        drawings=[
            PreparedDrawingProvenance(
                path=path,
                source_sha256=digests[path],
                source_bytes=len(contents[path]),
                cad_snapshot=imported_by_path[path].cad_snapshot_provenance,
            )
            for path in entries
        ],
    )
    project = open_source_editor(project)
    assert project.geometry is not None
    result = CadPrepareResult(
        published_state_version=project.state_version + 1,
        geometry_version=project.geometry_version,
        source_sha256=digest,
        source_bytes=len(content),
        source_count=len(entries),
        source_bytes_total=sum(len(item) for item in contents.values()),
        feature_count=len(project.geometry.feature_collection["features"]),
        cad_snapshot_payload_sha256=(
            project.source_file.cad_snapshot_provenance.payload_sha256
            if project.source_file.cad_snapshot_provenance is not None
            else None
        ),
        cad_snapshot_native_geometry=(
            project.source_file.cad_snapshot_provenance.native_geometry
            if project.source_file.cad_snapshot_provenance is not None
            else None
        ),
    )
    lifecycle.check_cancelled(operation.id)
    # Re-resolve the signed manifest and re-hash the source just before publication.
    current_intake = lifecycle.get(project.id, intake.id)
    for drawing_path in entries:
        current = resolve_asset_source(config, current_intake, project.id, drawing_path)
        if file_sha256(current.asset) != digests[drawing_path]:
            raise ValueError("Исходник изменился во время подготовки")
        selection = selections.get(drawing_path)
        if selection is None:
            continue
        current_snapshot_sha256 = verify_cad_snapshot_fingerprint(
            canonical_root, selection
        )
        snapshot_source = snapshot_sources.get(drawing_path)
        if snapshot_source is None or current_snapshot_sha256 != snapshot_source.sha256:
            raise ValueError("CAD snapshot изменился во время подготовки")
        current_dependencies = verify_cad_snapshot_dependency_records(
            canonical_root, snapshots[drawing_path].dependencies
        )
        if current_dependencies != verified_by_path[drawing_path]:
            raise ValueError("XREF-комплект изменился во время подготовки")
    lifecycle.report(
        operation.id,
        WorkProgress(stage="Сохраняем полный редактируемый источник", fraction=None),
        0,
        99,
    )
    receipt = SqliteCadProjectPublication(work.database).publish(
        project,
        source_assets[passport.entry],
        operation.id,
        result,
        source_components={
            path: source_assets[path] for path in entries if path != passport.entry
        },
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
