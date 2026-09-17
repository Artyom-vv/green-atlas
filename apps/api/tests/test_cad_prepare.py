import sqlite3

import pytest
from cad_preview_fixtures import fixture_preview

from app.cad_import.contracts import CadConversionError
from app.cad_intake.prepare_adapter import ProcessCadProjectPreparation
from app.cad_intake.prepare_application import CadPrepareApplication
from app.cad_intake.prepare_contracts import CadPrepareRequest
from app.cad_intake.prepare_publication import SqliteCadProjectPublication
from app.cad_intake.prepare_worker import execute
from app.cad_intake.work import CadWork
from app.dxf_import.contracts import ImportEditability
from app.operations.contracts import OperationStatus
from app.operations.progress import OperationCancelled
from app.projects.concurrency import ProjectVersionConflict


@pytest.fixture
def fixture(tmp_path):
    value = fixture_preview(tmp_path)
    try:
        yield value
    finally:
        value.runtime.close()


def preparation(fixture):
    app = CadPrepareApplication(
        fixture.config,
        fixture.lifecycle,
        ProcessCadProjectPreparation(fixture.config, fixture.runtime.database_path),
        lambda _: None,
    )
    request = CadPrepareRequest(
        intake_operation_id=fixture.request.intake_operation_id,
        manifest_sha256=fixture.request.manifest_sha256,
    )
    return app, request


def start_work(fixture):
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)
    fixture.lifecycle.claim(operation.id, "test")
    return CadWork(
        operation_id=operation.id,
        database=fixture.runtime.database_path,
        root=fixture.source.parent,
        storage=fixture.config.storage,
        receipt=fixture.config.storage / "publication.json",
    )


def test_full_worker_retains_far_geometry_original_bytes_and_editability(fixture):
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)
    assert app.start(fixture.project.id, request).id == operation.id
    app.run(operation.id)
    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.COMPLETED, record.error
    project = fixture.runtime.project_repository.get(fixture.project.id)
    assert project.import_status.editability == ImportEditability.EDITABLE
    assert project.map_ready and project.source_review is not None
    assert project.geometry is not None and project.source_geometry is None
    assert len(project.geometry.feature_collection["features"]) == 3
    assert project.source_file.bounds == [-10.0, 0.0, 200.0, 200.0]
    assert (
        fixture.runtime.project_repository.get_source(project.id)
        == fixture.source.read_bytes()
    )
    assert (
        project.source_file.prepared_provenance.manifest_sha256
        == request.manifest_sha256
    )
    assert record.cad_prepare.result.feature_count == 3
    with sqlite3.connect(fixture.runtime.database_path) as connection:
        stored, storage_type = connection.execute(
            "SELECT payload, typeof(payload) FROM projects WHERE id=?", (project.id,)
        ).fetchone()
    assert storage_type == "text"
    assert stored == project.model_dump_json()
    app.run(operation.id)
    assert fixture.runtime.project_repository.get(project.id).state_version == 2
    assert (
        fixture.lifecycle.cancel(project.id, operation.id).status
        == OperationStatus.COMPLETED
    )
    with pytest.raises(ValueError):
        app.start(project.id, request)


@pytest.mark.parametrize("change", ["source", "manifest"])
def test_changed_evidence_does_not_publish_partial_project(fixture, change):
    work = start_work(fixture)
    path = (
        fixture.source
        if change == "source"
        else (
            fixture.config.storage
            / "operations"
            / fixture.request.intake_operation_id
            / "source-package.json"
        )
    )
    path.write_bytes(b"changed")
    with pytest.raises(ValueError):
        execute(work)
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 1


@pytest.mark.parametrize("reason", ["cancel", "version", "receipt"])
def test_atomic_publication_preserves_project_on_cancel_conflict_or_sql_failure(
    fixture, monkeypatch, reason
):
    work = start_work(fixture)
    original = SqliteCadProjectPublication.publish

    def intercept(self, project, source, operation_id, result):
        if reason == "cancel":
            fixture.lifecycle.cancel(project.id, operation_id)
        elif reason == "version":
            fixture.runtime.project_repository.save(fixture.project)
        return original(self, project, source, operation_id, result)

    monkeypatch.setattr(SqliteCadProjectPublication, "publish", intercept)
    if reason == "receipt":
        with sqlite3.connect(work.database) as connection:
            connection.execute("""CREATE TRIGGER fail_receipt BEFORE UPDATE ON project_operations
                WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    with pytest.raises(
        (OperationCancelled, ProjectVersionConflict, sqlite3.IntegrityError)
    ):
        execute(work)
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert (
        fixture.lifecycle.operations.get(work.operation_id).status
        != OperationStatus.COMPLETED
    )


def test_a_root_with_external_references_is_not_published_as_whole_package(fixture):
    app, request = preparation(fixture)
    intake = fixture.lifecycle.operations.get(request.intake_operation_id)
    intake.cad_intake.passport.drawings[0].inspection.xrefs = {"network": "network.dxf"}
    fixture.lifecycle.operations.save(intake)
    with pytest.raises(ValueError, match="внешние ссылки"):
        app.start(fixture.project.id, request)


def test_resource_failure_explains_why_without_publishing(fixture, monkeypatch):
    app, request = preparation(fixture)
    operation = app.start(fixture.project.id, request)

    def exceed_budget(*args, **kwargs):
        raise CadConversionError("Превышен бюджет памяти преобразования CAD")

    monkeypatch.setattr(app.preparation, "prepare", exceed_budget)
    app.run(operation.id)
    record = fixture.lifecycle.operations.get(operation.id)
    assert record.status == OperationStatus.FAILED
    assert record.error.code == "CAD_PREPARE_RESOURCE_LIMIT"
    assert "памяти" in record.error.message
    assert fixture.runtime.project_repository.get_source(fixture.project.id) is None
    assert fixture.runtime.project_repository.get(fixture.project.id).state_version == 1
