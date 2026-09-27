from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Event

import ezdxf
import pytest
from cad_preview_fixtures import fixture_preview

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import DrawingInspection
from app.cad_import.package_contracts import PackageDrawing, SourcePackage
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.contracts import CadDrawingEntry
from app.cad_intake.passport import make_passport
from app.cad_intake.preview_adapter import ProcessCadPreviewPreparation
from app.cad_intake.preview_application import CadPreviewApplication
from app.cad_intake.preview_publication import SqliteCadPreviewPublication
from app.cad_intake.preview_worker import execute
from app.dxf_import.capacity import SourceCapacityExceeded
from app.dxf_import.contracts import ImportEditability, ImportMode
from app.operations.adapters import SqliteOperationRepository
from app.operations.contracts import OperationKind, OperationStatus
from app.operations.progress import OperationCancelled
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)


@pytest.fixture
def fixture(tmp_path):
    result = fixture_preview(tmp_path)
    try:
        yield result
    finally:
        result.runtime.close()


def test_real_bounded_worker_publishes_editable_aoi_and_full_source_once(fixture):
    application = fixture.application(
        ProcessCadPreviewPreparation(fixture.config, fixture.runtime.database_path)
    )
    operation = application.start(fixture.project.id, fixture.request)
    application.run(operation.id)
    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.COMPLETED, record.error
    project = fixture.runtime.project_repository.get(fixture.project.id)
    assert project.state_version == fixture.project.state_version + 1
    assert project.import_status.mode == ImportMode.SOURCE_DXF
    assert project.import_status.editability == ImportEditability.EDITABLE
    assert project.source_geometry is None
    assert project.geometry is not None and project.plan is None and project.map_ready
    assert project.source_review is not None
    assert not project.planting_zones
    assert (
        project.site_area_m2
        is project.allowed_area_m2
        is project.planning_area_m2
        is None
    )
    result = record.cad_preview.result
    assert result.boundary_mask_area_m2 == 100 and not result.calculation_ready
    assert project.source_file.content_sha256 == result.source_sha256
    assert project.source_file.prepared_provenance is not None
    assert project.source_file.prepared_provenance.aoi is not None
    assert fixture.runtime.project_repository.get_source(project.id) == (
        fixture.source.read_bytes()
    )
    assert file_sha256(fixture.source) == fixture.request.source.source_sha256
    assert str(fixture.source.parent) not in record.model_dump_json()
    application.run(operation.id)
    assert (
        fixture.runtime.project_repository.get(project.id).state_version
        == project.state_version
    )
    reopened = SqliteOperationRepository(fixture.runtime.database_path)
    try:
        assert reopened.get(operation.id).status == OperationStatus.COMPLETED
        assert (
            fixture.lifecycle.cancel(project.id, operation.id).status
            == OperationStatus.COMPLETED
        )
    finally:
        reopened.close()


def test_bounded_worker_composes_every_dxf_in_the_checked_package(fixture):
    child = fixture.source.parent / "networks.dxf"
    document = ezdxf.new("R2018")
    document.units = 6
    document.layers.add("NETWORKS")
    document.modelspace().add_line(
        (1, 1), (9, 9), dxfattribs={"layer": "NETWORKS"}
    )
    document.saveas(child)
    digest = sha256(child.read_bytes()).hexdigest()
    intake = fixture.lifecycle.operations.get(fixture.request.intake_operation_id)
    assert intake.cad_intake is not None
    manifest = (
        fixture.config.storage / "operations" / intake.id / "source-package.json"
    )
    package = SourcePackage.model_validate_json(manifest.read_bytes())
    package.entries = [package.entry, child.name]
    package.drawings.append(
        PackageDrawing(
            path=child.name,
            source_sha256=digest,
            source_bytes=child.stat().st_size,
            normalized_path=str(child),
            status="readable",
            inspection=DrawingInspection(
                dxf_version=document.dxfversion,
                units=6,
                modelspace_entities={"LINE": 1},
                layer_names=["0", "NETWORKS"],
                xrefs={},
            ),
        )
    )
    manifest.write_text(package.model_dump_json(), encoding="utf-8")
    manifest_digest = sha256(manifest.read_bytes()).hexdigest()
    intake.cad_intake.request.additional_entries = [
        CadDrawingEntry(path=child.name, sha256=digest)
    ]
    intake.cad_intake.passport = make_passport(
        package,
        "test",
        manifest_digest,
        {"main.dxf": file_sha256(fixture.source), child.name: digest},
    )
    fixture.lifecycle.operations.save(intake)
    fixture.request = fixture.request.model_copy(
        update={"manifest_sha256": manifest_digest}
    )
    application = fixture.application(
        ProcessCadPreviewPreparation(fixture.config, fixture.runtime.database_path)
    )

    operation = application.start(fixture.project.id, fixture.request)
    application.run(operation.id)

    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.COMPLETED, record.error
    project = fixture.runtime.project_repository.get(fixture.project.id)
    assert any(
        layer.source_name == "[networks.dxf] NETWORKS" for layer in project.layers
    )
    assert any(
        feature["properties"].get("source_drawing_path") == "networks.dxf"
            for feature in project.geometry.feature_collection["features"]
    )


def test_preview_accepts_an_uploaded_root_without_static_server_roots(fixture):
    root_id = "upload-0123456789abcdef0123456789abcdef"
    uploaded = fixture.config.storage / "uploads" / root_id
    uploaded.mkdir(parents=True)
    (uploaded / "main.dxf").write_bytes(fixture.source.read_bytes())
    (uploaded / "upload.json").write_text("{}", encoding="utf-8")
    intake = fixture.lifecycle.operations.get(fixture.request.intake_operation_id)
    assert intake.cad_intake is not None and intake.cad_intake.passport is not None
    intake.cad_intake.request.root_id = root_id
    intake.cad_intake.passport.root_id = root_id
    fixture.lifecycle.operations.save(intake)
    config = CadIntakeConfig((), fixture.config.storage, None)
    application = CadPreviewApplication(
        config,
        fixture.lifecycle,
        ProcessCadPreviewPreparation(config, fixture.runtime.database_path),
        lambda _: None,
    )

    operation = application.start(fixture.project.id, fixture.request)

    assert operation.status == OperationStatus.QUEUED


@pytest.mark.parametrize("change", ["manifest", "source", "selection"])
def test_changed_evidence_fails_before_prepare_and_preserves_project(
    fixture, monkeypatch, change
):
    work = fixture.start_work()
    if change == "source":
        fixture.source.write_bytes(b"changed")
    elif change == "manifest":
        manifest = (
            fixture.config.storage
            / "operations"
            / fixture.request.intake_operation_id
            / "source-package.json"
        )
        manifest.write_bytes(b"changed")
    else:
        intake = fixture.lifecycle.operations.get(fixture.request.intake_operation_id)
        intake.cad_intake.passport.drawings[0].normalized_sha256 = "b" * 64
        fixture.lifecycle.operations.save(intake)

    def forbidden(*_):
        pytest.fail("changed evidence must not enter AOI")

    monkeypatch.setattr("app.cad_intake.preview_worker.prepare_aoi", forbidden)
    with pytest.raises(ValueError):
        execute(work)
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 1


@pytest.mark.parametrize("reason", ["cancel", "version", "reader"])
def test_cancel_or_conflict_before_publication_leaves_project_empty(
    fixture, monkeypatch, reason
):
    work = fixture.start_work()
    original = SqliteCadPreviewPublication.publish

    def intercept(self, project, content, operation_id, result, **kwargs):
        if reason == "cancel":
            fixture.lifecycle.cancel(project.id, operation_id)
        else:
            fixture.runtime.project_repository.save(fixture.project)
        return original(self, project, content, operation_id, result, **kwargs)

    if reason == "reader":

        def reject(*_):
            raise SourceCapacityExceeded("source capacity fixture")

        monkeypatch.setattr(
            "app.cad_intake.preview_worker.EzdxfReader.read_prepared_file", reject
        )
    else:
        monkeypatch.setattr(SqliteCadPreviewPublication, "publish", intercept)
    with pytest.raises((OperationCancelled, ProjectVersionConflict, ValueError)):
        execute(work)
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert (
        fixture.lifecycle.operations.get(work.operation_id).status
        != OperationStatus.COMPLETED
    )
    if reason == "reader":
        assert (
            fixture.lifecycle.operations.get(work.operation_id).error.code
            == "SOURCE_CAPACITY_EXCEEDED"
        )


def test_commit_wins_lost_worker_response_and_late_progress(fixture, monkeypatch):
    original = SqliteCadPreviewPublication.publish

    def publish_then_crash(self, *args, **kwargs):
        committed = original(self, *args, **kwargs)
        fixture.lifecycle.cancel(committed.project_id, committed.id)
        raise RuntimeError("worker crashed after commit")

    monkeypatch.setattr(SqliteCadPreviewPublication, "publish", publish_then_crash)

    class InlinePreparation:
        def prepare(self, operation_id, request, check_cancelled, report_progress):
            from app.cad_intake.preview_work import PreviewWork

            execute(
                PreviewWork(
                    operation_id=operation_id,
                    database=fixture.runtime.database_path,
                    root=fixture.source.parent,
                    storage=fixture.config.storage,
                    receipt=fixture.config.storage / "unused.json",
                )
            )

    app = fixture.application(InlinePreparation())
    operation = app.start(fixture.project.id, fixture.request)
    app.run(operation.id)
    assert (
        fixture.lifecycle.operations.get(operation.id).status
        == OperationStatus.COMPLETED
    )
    assert fixture.runtime.project_repository.get_source(fixture.project.id)
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 2


def test_start_checks_version_selection_empty_project_and_reuses_active_request(
    fixture,
):
    app = fixture.application(None)
    token = set_expected_project_version("9")
    try:
        with pytest.raises(ProjectVersionConflict):
            app.start(fixture.project.id, fixture.request)
    finally:
        reset_expected_project_version(token)
    invalid = fixture.request.model_copy(update={"manifest_sha256": "a" * 64})
    with pytest.raises(ValueError):
        app.start(fixture.project.id, invalid)
    op = app.start(fixture.project.id, fixture.request)
    assert app.start(fixture.project.id, fixture.request).id == op.id
    fixture.lifecycle.cancel(fixture.project.id, op.id)
    retry = app.start(fixture.project.id, fixture.request)
    assert retry.retry_of_operation_id == op.id
    assert (
        fixture.lifecycle.latest(
            fixture.project.id, OperationKind.PREPARE_CAD_PREVIEW
        ).id
        == retry.id
    )


def test_duplicate_workers_claim_only_once_and_cancel_waiting_work(fixture):
    entered, release = Event(), Event()
    calls = []

    class Preparation:
        def prepare(self, operation_id, request, check_cancelled, report_progress):
            calls.append(operation_id)
            entered.set()
            assert release.wait(3)
            check_cancelled()

    app = fixture.application(Preparation())
    operation = app.start(fixture.project.id, fixture.request)
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(app.run, operation.id)
        assert entered.wait(2)
        pool.submit(app.run, operation.id).result(2)
        fixture.lifecycle.cancel(fixture.project.id, operation.id)
        release.set()
        running.result(2)
    assert calls == [operation.id]
    assert (
        fixture.lifecycle.operations.get(operation.id).status
        == OperationStatus.CANCELLED
    )
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None


def test_recovery_stale_read_cannot_overwrite_atomic_publication(fixture, monkeypatch):
    import app.operations.adapters as adapters

    work = fixture.start_work()
    original_publish = SqliteCadPreviewPublication.publish
    original_interrupted = adapters.interrupted
    recovered = []

    def publish_during_recovery(self, *args, **kwargs):
        journal = SqliteOperationRepository(work.database, recover=False)
        committed = []

        def interrupt_after_commit(operation):
            committed.append(original_publish(self, *args, **kwargs))
            return original_interrupted(operation)

        monkeypatch.setattr(adapters, "interrupted", interrupt_after_commit)
        try:
            recovered.extend(journal.recover_incomplete())
            return committed[0]
        finally:
            journal.close()

    monkeypatch.setattr(SqliteCadPreviewPublication, "publish", publish_during_recovery)
    execute(work)
    assert recovered == []
    assert (
        fixture.lifecycle.operations.get(work.operation_id).status
        == OperationStatus.COMPLETED
    )
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 2
    assert fixture.runtime.project_repository.get_source(fixture.project.id)


def test_receipt_write_failure_rolls_back_source_and_project(fixture):
    import sqlite3

    work = fixture.start_work()
    with sqlite3.connect(work.database) as connection:
        connection.execute("""CREATE TRIGGER reject_fixture_receipt BEFORE UPDATE ON project_operations
            WHEN NEW.kind = 'prepare_cad_preview' AND NEW.status = 'completed'
            BEGIN SELECT RAISE(ABORT, 'fixture receipt failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        execute(work)
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 1
    assert (
        fixture.lifecycle.operations.get(work.operation_id).status
        != OperationStatus.COMPLETED
    )


def test_subprocess_version_conflict_retains_typed_journal_reason(fixture):
    work = fixture.start_work()
    fixture.runtime.project_repository.save(fixture.project)
    preparation = ProcessCadPreviewPreparation(fixture.config, work.database)
    with pytest.raises(ValueError):
        preparation.prepare(
            work.operation_id, fixture.request, lambda: None, lambda _: None
        )
    failed = fixture.lifecycle.operations.get(work.operation_id)
    assert failed.status == OperationStatus.FAILED
    assert failed.error.code == "PROJECT_VERSION_CONFLICT"
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
