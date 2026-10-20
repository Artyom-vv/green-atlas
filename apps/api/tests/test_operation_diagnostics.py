"""Ошибки операций имеют независимый приватный журнал, а не только stderr."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from threading import RLock
from unittest.mock import Mock
from uuid import uuid4

import pytest
from test_capture_preparation_operation import setup as preparation_fixture

from app.operations.adapters import SqliteOperationRepository
from app.operations.capture_preparation import CapturePreparationApplication
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.operations.lifecycle import OperationLifecycle
from app.shared import operation_diagnostics as diagnostic
from app.shared import performance
from app.shared.identity import random_id, utc_now
from app.shared.messages import message
from app.shared.operation_diagnostics import (
    NativeFailureDiagnostic,
    NativeOperationError,
)

capture_setup = preparation_fixture

SECRET = "private-input-key-and-CAD-payload"
OPERATION = str(uuid4())
PROJECT = str(uuid4())


@pytest.fixture(autouse=True)
def isolated_sink(monkeypatch):
    monkeypatch.setattr(diagnostic, "_sink", None)


@pytest.fixture
def operation():
    operations = SqliteOperationRepository(":memory:")
    current = operations.create(
        ProjectOperation(
            project_id=PROJECT,
            kind=OperationKind.PREPARE_CAD_CAPTURE,
            status=OperationStatus.RUNNING,
            progress=37,
        )
    )
    lifecycle = OperationLifecycle(Mock(), operations, RLock(), utc_now, random_id)
    try:
        yield lifecycle, operations, current
    finally:
        operations.close()


def write(error, **kwargs):
    return diagnostic.record_operation_failure(
        OPERATION,
        code="CAD_PREPARATION_FAILED",
        error=error,
        project_id=PROJECT,
        kind="prepare_cad_capture",
        **kwargs,
    )


def rows(root):
    return [
        json.loads(line)
        for line in (root / "Logs/operations.jsonl").read_text().splitlines()
    ]


def native_failure(root):
    return NativeOperationError(
        SECRET,
        diagnostic=NativeFailureDiagnostic(
            failure_code="process_group_alive",
            exit_code=0,
            tree_empty=True,
            signals=("TerminateJobObject",),
            core_log_path=root / "ga-native-query-private-job/core.log",
            phase="post_exit_cleanup",
            cleanup_attempted=True,
            cleanup_confirmed=True,
        ),
    )


def test_durable_event_survives_missing_stdio_and_keeps_only_safe_cause_fields(
    tmp_path, monkeypatch
):
    assert diagnostic.configure_operation_diagnostics(tmp_path)
    fsync = Mock(wraps=os.fsync)
    monkeypatch.setattr(performance.os, "fsync", fsync)
    failure = native_failure(tmp_path)
    try:
        try:
            raise failure
        except NativeOperationError as error:
            raise RuntimeError(SECRET + " outer") from error
    except RuntimeError as error:
        with monkeypatch.context() as context:
            context.setattr(sys, "stdout", None)
            context.setattr(sys, "stderr", None)
            assert write(error)
    fsync.assert_called_once()
    event = rows(tmp_path)[0]
    assert event["schema"] == "green-atlas.operation-failure/1"
    assert event["operation_id"] == OPERATION and event["project_id"] == PROJECT
    assert [value["class"] for value in event["exceptions"]] == [
        "RuntimeError",
        "NativeOperationError",
    ]
    assert all(value["stack"] for value in event["exceptions"])
    assert event["native"] == {
        "failure_code": "process_group_alive",
        "phase": "post_exit_cleanup",
        "exit_code": 0,
        "tree_empty": True,
        "signals": ["TerminateJobObject"],
        "cleanup_attempted": True,
        "cleanup_confirmed": True,
        "core_log_path": str(failure.diagnostic.core_log_path),
    }
    text = (tmp_path / "Logs/operations.jsonl").read_text()
    assert SECRET not in text
    assert "diagnostic=" not in text and "Traceback" not in text
    for exception in event["exceptions"]:
        for frame in exception["stack"]:
            assert "/" not in frame["file"] and "\\" not in frame["file"]
    assert not (tmp_path / "Logs/performance.jsonl").exists()


def test_rotation_is_bounded_and_latest_event_is_durable(tmp_path, monkeypatch):
    monkeypatch.setattr(performance, "_MAX_BYTES", 1536)
    assert diagnostic.configure_operation_diagnostics(tmp_path)
    fsync = Mock(wraps=os.fsync)
    monkeypatch.setattr(performance.os, "fsync", fsync)
    for _ in range(30):
        assert write(ValueError(SECRET))
    paths = list((tmp_path / "Logs").glob("operations.jsonl*"))
    assert len(paths) == 3 and all(path.stat().st_size <= 1536 for path in paths)
    assert rows(tmp_path)[-1]["code"] == "CAD_PREPARATION_FAILED"
    assert fsync.call_count == 30
    assert all(SECRET not in path.read_text() for path in paths)


def test_performance_default_does_not_fsync_or_change_filename(tmp_path, monkeypatch):
    sink = performance._LocalLog(tmp_path)
    fsync = Mock(side_effect=AssertionError("Performance logging must stay cheap"))
    monkeypatch.setattr(performance.os, "fsync", fsync)
    sink.write({"event": "unchanged"})
    assert sink.enabled and sink.path.name == "performance.jsonl"
    fsync.assert_not_called()
    assert not (tmp_path / "Logs/operations.jsonl").exists()


@pytest.mark.parametrize("filename", ["../outside.log", "/outside.log", "other.jsonl"])
def test_log_target_cannot_be_selected_by_a_filename(tmp_path, filename):
    with pytest.raises(ValueError, match="Unknown"):
        performance._LocalLog(tmp_path, filename=filename)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(
    os.name == "nt", reason="Windows privacy uses the existing guarded handle adapter"
)
def test_new_private_log_has_private_posix_modes(tmp_path):
    assert diagnostic.configure_operation_diagnostics(tmp_path)
    assert write(ValueError(SECRET))
    assert stat.S_IMODE((tmp_path / "Logs").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "Logs/operations.jsonl").stat().st_mode) == 0o600


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_alias_log_is_refused_without_touching_target(tmp_path, alias):
    directory = tmp_path / "Logs"
    directory.mkdir()
    target = tmp_path / "untouched"
    target.write_bytes(b"private existing data")
    path = directory / "operations.jsonl"
    if alias == "hardlink":
        os.link(target, path)
    else:
        try:
            path.symlink_to(target)
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                pytest.skip("Windows account cannot create file symlinks")
            raise
    assert not diagnostic.configure_operation_diagnostics(tmp_path)
    assert not write(ValueError(SECRET))
    assert target.read_bytes() == b"private existing data"


@pytest.mark.parametrize("failure", ["unconfigured", "write", "fsync"])
def test_failure_to_write_never_hides_operation_or_claims_a_saved_log(
    tmp_path,
    monkeypatch,
    operation,
    failure,
):
    lifecycle, operations, current = operation
    if failure != "unconfigured":
        assert diagnostic.configure_operation_diagnostics(tmp_path)
        if failure == "write":
            monkeypatch.setattr(
                diagnostic._sink, "write", Mock(side_effect=OSError(SECRET))
            )
        else:
            monkeypatch.setattr(
                performance.os, "fsync", Mock(side_effect=OSError(SECRET))
            )
    lifecycle.fail(
        current.id,
        "Подготовка остановлена",
        "CAD_PREPARATION_FAILED",
        native_failure(tmp_path),
    )
    saved = operations.get(current.id)
    assert saved.status is OperationStatus.FAILED and saved.progress == 37
    assert saved.error.code == "CAD_PREPARATION_FAILED"
    assert saved.error.message == message("operation_failed_without_log")
    assert (
        SECRET not in saved.model_dump_json()
        and "core.log" not in saved.model_dump_json()
    )


@pytest.mark.parametrize("cancelled", [False, True])
def test_lifecycle_keeps_public_error_sanitized_and_cancellation_authoritative(
    tmp_path,
    operation,
    cancelled,
):
    lifecycle, operations, current = operation
    assert diagnostic.configure_operation_diagnostics(tmp_path)
    if cancelled:
        lifecycle.cancel(current.project_id, current.id)
    lifecycle.fail(
        current.id,
        "Подготовка остановлена",
        "CAD_PREPARATION_FAILED",
        native_failure(tmp_path),
    )
    saved = operations.get(current.id)
    assert saved.status is (
        OperationStatus.CANCELLED if cancelled else OperationStatus.FAILED
    )
    if not cancelled:
        assert saved.error.model_dump() == {
            "code": "CAD_PREPARATION_FAILED",
            "message": message("operation_failed"),
        }
    assert (
        SECRET not in saved.model_dump_json()
        and str(tmp_path) not in saved.model_dump_json()
    )
    event = rows(tmp_path)[0]
    assert event["operation_id"] == current.id
    assert event["native"]["failure_code"] == "process_group_alive"


def test_exception_chain_is_bounded_even_with_a_cycle(tmp_path):
    assert diagnostic.configure_operation_diagnostics(tmp_path)
    failures = [ValueError(SECRET) for _ in range(8)]
    for first, second in zip(failures, failures[1:]):
        first.__cause__ = second
    failures[-1].__cause__ = failures[0]
    assert write(failures[0])
    assert len(rows(tmp_path)[0]["exceptions"]) == 4


def test_unknown_native_fields_are_never_copied_verbatim(tmp_path):
    assert diagnostic.configure_operation_diagnostics(tmp_path)
    error = NativeOperationError(
        SECRET,
        diagnostic=NativeFailureDiagnostic(
            failure_code=SECRET,
            phase=SECRET,
            exit_code=True,
            tree_empty=1,
            signals=(SECRET,),
            core_log_path=tmp_path / "outside/private.log",
        ),
    )
    assert write(error)
    native = rows(tmp_path)[0]["native"]
    assert native["failure_code"] == native["phase"] == "unknown"
    assert native["exit_code"] is None and native["tree_empty"] is None
    assert native["signals"] == [] and native["core_log_path"] is None
    assert SECRET not in (tmp_path / "Logs/operations.jsonl").read_text()


def test_capture_failure_is_logged_without_publishing_project_changes(
    capture_setup, tmp_path
):
    repository, project, lifecycle, operations = capture_setup
    before = project.model_dump_json()
    assert diagnostic.configure_operation_diagnostics(tmp_path)
    application = CapturePreparationApplication(
        repository, lifecycle, Mock(side_effect=native_failure(tmp_path))
    )
    current = application.start(project.id)
    application.run(current.id)
    saved = operations.get(current.id)
    assert saved.status is OperationStatus.FAILED
    assert saved.error.code == "CAD_PREPARATION_FAILED"
    assert saved.error.message == message("operation_failed")
    assert repository.get(project.id).model_dump_json() == before
    event = rows(tmp_path)[0]
    assert event["operation_id"] == current.id
    assert event["native"]["phase"] == "post_exit_cleanup"
    assert "NativeOperationError" in [item["class"] for item in event["exceptions"]]


@pytest.mark.skipif(
    os.name != "posix", reason="Synthetic Core script runs only on POSIX"
)
def test_archive_cleanup_failure_reaches_capture_operation_private_log(
    capture_setup,
    tmp_path,
    monkeypatch,
):
    from completed_windows_job_fixture import spawn_completed
    from test_archive_face_process import REQUEST, face_core
    from test_native_query_process import setup_job

    from app.native_query.archive.archive_face_process import prepare_archive_faces
    from app.native_query.transport import process

    fixture = tmp_path / "synthetic-core"
    fixture.mkdir()
    package, _, config = setup_job(fixture)
    face_core(config.core_executable, "ok")
    source_before = (package.root / "entry.dwg").read_bytes()

    def spawn(command, job):
        return spawn_completed(
            command,
            job,
            on_stop=lambda path: (path / "native.json").write_bytes(
                b"changed during cleanup"
            ),
        )

    monkeypatch.setattr(process, "_spawn", spawn)
    repository, project, lifecycle, operations = capture_setup
    before = project.model_dump_json()
    assert diagnostic.configure_operation_diagnostics(tmp_path)

    def prepare(_project, _report):
        prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)

    application = CapturePreparationApplication(repository, lifecycle, prepare)
    current = application.start(project.id)
    application.run(current.id)
    saved = operations.get(current.id)
    assert saved.status is OperationStatus.FAILED
    assert saved.error.code == "CAD_PREPARATION_FAILED"
    assert "core.log" not in saved.model_dump_json()
    assert repository.get(project.id).model_dump_json() == before
    assert (package.root / "entry.dwg").read_bytes() == source_before
    event = rows(tmp_path)[0]
    assert event["native"]["failure_code"] == "invalid_reply"
    assert event["native"]["tree_empty"] is True
    assert event["native"]["signals"] == ["TerminateJobObject"]
    assert event["native"]["cleanup_attempted"] is True
    assert event["native"]["cleanup_confirmed"] is False
    assert event["native"]["phase"] == "completion_postcheck"
    assert Path(event["native"]["core_log_path"]).is_file()
