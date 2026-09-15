from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, RLock

import pytest

from app.cad_import.cache import file_sha256
from app.cad_intake.application import CadIntakeApplication
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadIntakeRequest, CadPackagePassport
from app.operations.adapters import (
    InMemoryOperationRepository,
    SqliteOperationRepository,
)
from app.operations.contracts import OperationKind, OperationStatus
from app.operations.lifecycle import OperationLifecycle
from app.planning.contracts import Plan
from app.projects.adapters import InMemoryProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project
from app.shared.identity import random_id, utc_now


class Inspector:
    def __init__(self, callback: Callable[[], None] = lambda: None) -> None:
        self.calls = 0
        self.callback = callback

    def inspect(self, operation_id, request, check_cancelled, report_progress):
        self.calls += 1
        self.callback()
        check_cancelled()
        return CadPackagePassport(
            root_id=request.root_id,
            entry=request.entry,
            manifest_sha256="a" * 64,
            drawings=[],
            references=[],
            status="blocked",
            blockers=["Внешняя ссылка отсутствует"],
        )


def setup_intake(tmp_path, inspector=None, journal=None):
    root = tmp_path / "originals"
    root.mkdir(exist_ok=True)
    source = root / "main.dxf"
    source.write_text("original drawing", encoding="utf-8")
    config = CadIntakeConfig(
        (AllowedCadRoot("official", "Официальный набор", root),),
        tmp_path / "data",
        tmp_path / "dwgread",
    )
    repository = InMemoryProjectRepository()
    project = repository.create(Project(name="CAD intake"))
    lifecycle = OperationLifecycle(
        repository,
        journal or InMemoryOperationRepository(),
        RLock(),
        utc_now,
        random_id,
    )
    application = CadIntakeApplication(config, lifecycle, inspector or Inspector())
    request = CadIntakeRequest(
        root_id="official", entry="main.dxf", entry_sha256=file_sha256(source)
    )
    return application, project, request


def test_intake_publishes_blocked_passport_without_changing_project_or_original(
    tmp_path,
):
    app, project, request = setup_intake(tmp_path)
    before = app.lifecycle.repository.get(project.id).model_dump_json()
    operation = app.start(project.id, request)
    app.run(operation.id)
    result = app.lifecycle.get(project.id, operation.id)
    assert result.status == OperationStatus.COMPLETED
    assert result.cad_intake.passport.status == "blocked"
    assert result.cad_intake.passport.calculation_ready is False
    assert app.lifecycle.repository.get(project.id).model_dump_json() == before
    assert (
        app.discovery.fingerprint("official", "main.dxf").sha256 == request.entry_sha256
    )


def test_intake_duplicate_start_and_workers_only_inspect_once(tmp_path):
    entered, release = Event(), Event()

    def pause():
        entered.set()
        assert release.wait(2)

    inspector = Inspector(pause)
    app, project, request = setup_intake(tmp_path, inspector)
    operation = app.start(project.id, request)
    assert app.start(project.id, request).id == operation.id
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(app.run, operation.id)
        assert entered.wait(2)
        pool.submit(app.run, operation.id).result(2)
        release.set()
        first.result(2)
    app.run(operation.id)
    assert inspector.calls == 1


def test_cancellation_discards_worker_result_and_retry_has_new_identity(tmp_path):
    app, project, request = setup_intake(tmp_path)
    operation = app.start(project.id, request)
    app.inspector = Inspector(lambda: app.lifecycle.cancel(project.id, operation.id))
    app.run(operation.id)
    result = app.lifecycle.get(project.id, operation.id)
    assert result.status == OperationStatus.CANCELLED
    assert result.cad_intake.passport is None
    retry = app.start(project.id, request)
    assert retry.id != operation.id and retry.retry_of_operation_id == operation.id


def test_queued_cancel_prevents_any_worker(tmp_path):
    inspector = Inspector()
    app, project, request = setup_intake(tmp_path, inspector)
    operation = app.start(project.id, request)
    app.lifecycle.cancel(project.id, operation.id)
    app.run(operation.id)
    assert inspector.calls == 0


def test_project_cas_rejects_start_and_changed_project_during_work(tmp_path):
    app, project, request = setup_intake(tmp_path)
    token = set_expected_project_version("9")
    try:
        with pytest.raises(ProjectVersionConflict):
            app.start(project.id, request)
    finally:
        reset_expected_project_version(token)
    operation = app.start(project.id, request)
    app.inspector = Inspector(lambda: app.lifecycle.repository.save(project))
    app.run(operation.id)
    result = app.lifecycle.get(project.id, operation.id)
    assert result.status == OperationStatus.FAILED
    assert result.error.code == "PROJECT_VERSION_CONFLICT"
    assert result.cad_intake.passport is None


def test_journal_recovery_preserves_request_and_prevents_stale_worker(tmp_path):
    database = tmp_path / "journal.sqlite3"
    first = SqliteOperationRepository(database)
    app, project, request = setup_intake(tmp_path, journal=first)
    operation = app.start(project.id, request)
    app.lifecycle.claim(operation.id, "Чтение")
    first.close()
    reopened = SqliteOperationRepository(database)
    try:
        restored = reopened.get(operation.id)
        assert restored.status == OperationStatus.INTERRUPTED
        assert restored.cad_intake.request == request
        app.lifecycle.operations = reopened
        app.run(operation.id)
        assert app.inspector.calls == 0
        retry = app.start(project.id, request)
        assert retry.retry_of_operation_id == operation.id
    finally:
        reopened.close()


def test_operation_cas_blocks_stale_progress_across_sqlite_connections(tmp_path):
    first = SqliteOperationRepository(tmp_path / "journal.sqlite3")
    second = SqliteOperationRepository(tmp_path / "journal.sqlite3", recover=False)
    try:
        app, project, request = setup_intake(tmp_path, journal=first)
        queued = app.start(project.id, request)
        running = app.lifecycle.claim(queued.id, "Чтение")
        other = OperationLifecycle(
            app.lifecycle.repository, second, RLock(), utc_now, random_id
        )
        assert other.claim(queued.id, "Дублирующий worker") is None
        other.cancel(project.id, queued.id)
        late = running.model_copy(update={"progress": 90})
        assert first.compare_and_save(running, late) is None
        assert first.get(queued.id).status == OperationStatus.CANCELLING
    finally:
        first.close()
        second.close()


def test_unknown_root_and_arbitrary_paths_are_rejected(tmp_path):
    app, project, request = setup_intake(tmp_path)
    with pytest.raises(ValueError):
        app.start(project.id, request.model_copy(update={"root_id": "unknown"}))
    for path in (
        "../main.dxf",
        "C:\\private\\main.dxf",
        "/etc/data.dxf",
        "\\\\server\\share\\main.dxf",
    ):
        with pytest.raises(ValueError):
            CadIntakeRequest(root_id="official", entry=path, entry_sha256="a" * 64)
    assert all(
        not Path(entry.path).is_absolute()
        for entry in app.discovery.directory("official").entries
    )
    assert (
        str(tmp_path)
        not in app.discovery.fingerprint("official", request.entry).model_dump_json()
    )


def test_project_with_plan_is_not_intake_target(tmp_path):
    app, project, request = setup_intake(tmp_path)
    project.plan = Plan()
    app.lifecycle.repository.save(project)
    with pytest.raises(ValueError, match="без готового плана"):
        app.start(project.id, request)
    assert app.lifecycle.latest(project.id, OperationKind.INSPECT_CAD_PACKAGE) is None


def test_sqlite_claim_handles_legacy_json_missing_new_optional_fields(tmp_path):
    journal = SqliteOperationRepository(tmp_path / "journal.sqlite3")
    try:
        app, project, _ = setup_intake(tmp_path, journal=journal)
        operation = app.lifecycle.start(project.id, OperationKind.CALCULATE_GEOMETRY)
        legacy = operation.model_dump_json(exclude={"cad_intake"})
        with journal._connection:
            journal._connection.execute(
                "UPDATE project_operations SET payload = ? WHERE id = ?",
                (legacy, operation.id),
            )
        assert (
            app.lifecycle.claim(operation.id, "Проверяем старую операцию").status
            == OperationStatus.RUNNING
        )
    finally:
        journal.close()


def test_completed_journal_is_not_overwritten_by_late_failure(tmp_path):
    app, project, request = setup_intake(tmp_path)
    operation = app.start(project.id, request)
    app.run(operation.id)
    app.lifecycle.fail(operation.id, "late error", "LATE", ValueError("late"))
    assert (
        app.lifecycle.get(project.id, operation.id).status == OperationStatus.COMPLETED
    )
    assert (
        app.lifecycle.latest(project.id, OperationKind.INSPECT_CAD_PACKAGE).id
        == operation.id
    )
