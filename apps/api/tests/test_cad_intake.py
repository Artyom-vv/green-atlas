"""Journal compatibility and concurrency remain after retiring CAD new-input."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import RLock

import pytest

from app.cad_intake.contracts import CadIntakeRecord, CadIntakeRequest
from app.operations.adapters import SqliteOperationRepository
from app.operations.contracts import OperationKind, OperationStatus
from app.operations.lifecycle import OperationLifecycle
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project
from app.shared.identity import random_id, utc_now


@contextmanager
def journal_lifecycle(
    journal: SqliteOperationRepository | None = None,
) -> Iterator[tuple[OperationLifecycle, Project]]:
    with ExitStack() as resources:
        repository = SqliteProjectRepository(":memory:")
        resources.callback(repository.close)
        if journal is None:
            journal = SqliteOperationRepository(":memory:")
            resources.callback(journal.close)
        project = repository.create(Project(name="Saved CAD journal"))
        yield (
            OperationLifecycle(repository, journal, RLock(), utc_now, random_id),
            project,
        )


def test_project_cas_rejects_stale_operation_start() -> None:
    with journal_lifecycle() as (lifecycle, project):
        token = set_expected_project_version("9")
        try:
            with pytest.raises(ProjectVersionConflict):
                lifecycle.start(project.id, OperationKind.CALCULATE_GEOMETRY)
        finally:
            reset_expected_project_version(token)
        assert lifecycle.latest(project.id, OperationKind.CALCULATE_GEOMETRY) is None


def test_saved_journal_recovery_preserves_request_and_blocks_stale_claim(
    tmp_path: Path,
) -> None:
    database = tmp_path / "journal.sqlite3"
    first = SqliteOperationRepository(database)
    with journal_lifecycle(first) as (lifecycle, project):
        request = CadIntakeRequest(
            root_id="official", entry="main.dxf", entry_sha256="a" * 64
        )
        try:
            operation = lifecycle.start(
                project.id,
                OperationKind.INSPECT_CAD_PACKAGE,
                cad_intake=CadIntakeRecord(request=request),
            )
            assert lifecycle.claim(operation.id, "Reading") is not None
        finally:
            first.close()
        reopened = SqliteOperationRepository(database)
        try:
            restored = reopened.get(operation.id)
            assert restored.status == OperationStatus.INTERRUPTED
            assert restored.cad_intake is not None
            assert restored.cad_intake.request == request
            lifecycle.operations = reopened
            assert lifecycle.claim(operation.id, "Late worker") is None
            retry = lifecycle.start(project.id, OperationKind.INSPECT_CAD_PACKAGE)
            assert retry.retry_of_operation_id == operation.id
        finally:
            reopened.close()


def test_operation_cas_blocks_stale_progress_across_sqlite_connections(
    tmp_path: Path,
) -> None:
    first = SqliteOperationRepository(tmp_path / "journal.sqlite3")
    second = SqliteOperationRepository(tmp_path / "journal.sqlite3", recover=False)
    try:
        with journal_lifecycle(first) as (lifecycle, project):
            queued = lifecycle.start(project.id, OperationKind.CALCULATE_GEOMETRY)
            running = lifecycle.claim(queued.id, "Reading")
            assert running is not None
            other = OperationLifecycle(
                lifecycle.repository, second, RLock(), utc_now, random_id
            )
            assert other.claim(queued.id, "Duplicate worker") is None
            other.cancel(project.id, queued.id)
            late = running.model_copy(update={"progress": 90})
            assert first.compare_and_save(running, late) is None
            assert first.get(queued.id).status == OperationStatus.CANCELLING
    finally:
        first.close()
        second.close()


def test_sqlite_claim_handles_saved_json_missing_optional_cad_fields(
    tmp_path: Path,
) -> None:
    journal = SqliteOperationRepository(tmp_path / "journal.sqlite3")
    try:
        with journal_lifecycle(journal) as (lifecycle, project):
            operation = lifecycle.start(project.id, OperationKind.CALCULATE_GEOMETRY)
            legacy = operation.model_dump_json(
                exclude={"cad_intake", "cad_preview", "cad_prepare"}
            )
            with journal._connection:
                journal._connection.execute(
                    "UPDATE project_operations SET payload = ? WHERE id = ?",
                    (legacy, operation.id),
                )
            claimed = lifecycle.claim(operation.id, "Saved operation")
            assert claimed is not None and claimed.status == OperationStatus.RUNNING
    finally:
        journal.close()


def test_completed_journal_is_not_overwritten_by_late_failure() -> None:
    with journal_lifecycle() as (lifecycle, project):
        operation = lifecycle.start(project.id, OperationKind.CALCULATE_GEOMETRY)
        assert lifecycle.claim(operation.id, "Reading") is not None
        lifecycle.update(
            operation.id,
            status=OperationStatus.COMPLETED,
            progress=100,
            stage="Complete",
        )
        lifecycle.fail(operation.id, "Late error", "LATE", ValueError("late"))
        assert (
            lifecycle.get(project.id, operation.id).status == OperationStatus.COMPLETED
        )
        latest = lifecycle.latest(project.id, OperationKind.CALCULATE_GEOMETRY)
        assert latest is not None and latest.id == operation.id
