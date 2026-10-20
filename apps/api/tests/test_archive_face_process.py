"""Run owned subprocesses to exercise failure, cleanup and retry boundaries."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from completed_windows_job_fixture import spawn_completed
from test_native_query_process import setup_job

from app.native_query.archive import archive_face_process
from app.native_query.archive.archive_face_process import prepare_archive_faces
from app.native_query.transport import process
from app.operations.progress import OperationCancelled, operation_cancellation
from app.shared import operation_diagnostics
from app.shared.operation_diagnostics import NativeOperationError

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX Core supervisor fixture"
)

REQUEST = b"checked fixture request\n"


def face_core(path: Path, mode: str) -> None:
    path.write_text(
        f"#!{sys.executable}\nMODE={mode!r}\n"
        + """
import hashlib,json,sys,time
from pathlib import Path
job=Path.cwd()
request=(job/'request.txt').read_bytes()
source=job/'package/entry.dwg'
reply={'session_id':'a'*32,'request_sha256':hashlib.sha256(request).hexdigest(),
       'layers':['SITE'],'face_policy':'native-planar-faces/1',
       'faces':[],'linear_routes':[],'face_issues':[]}
output=job/'native.json'
output.write_text(json.dumps(reply))
lifecycle={'schema':'green-atlas.native-query-lifecycle/1',
           'request_sha256':hashlib.sha256(request).hexdigest(),
           'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
           'reply_sha256':hashlib.sha256(output.read_bytes()).hexdigest(),
           'database_destroyed':True,'working_database_restored':True,
           'field_evaluation_restored':True}
if MODE=='numeric_cleanup': lifecycle['database_destroyed']=1
if MODE=='wrong_cleanup': lifecycle['reply_sha256']='f'*64
if MODE=='changed_request': (job/'request.txt').write_bytes(b'changed')
if MODE=='changed_input': source.write_bytes(b'changed')
if MODE=='temporary_reply': (job/'native.json.tmp').write_bytes(b'partial')
if MODE=='malformed': output.write_bytes(b'{broken')
if MODE!='missing_cleanup': (job/'lifecycle.json').write_text(json.dumps(lifecycle))
if MODE=='exit7': sys.exit(7)
if MODE=='hang': time.sleep(10)
"""
    )


def test_restart_uses_fresh_job_and_preserves_archive(tmp_path):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "hang")
    config = replace(config, timeout_seconds=0.2)
    before = {
        item.path: (package.root / item.path).read_bytes() for item in package.files
    }
    with pytest.raises(ValueError, match="не завершена"):
        prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    failed_job = next(config.job_root.iterdir())
    assert (failed_job / "core.log").exists()
    face_core(config.core_executable, "ok")
    reply = prepare_archive_faces(
        package, REQUEST, "a" * 32, ("SITE",), replace(config, timeout_seconds=5)
    )
    assert reply.layers == ("SITE",)
    assert len(list(config.job_root.iterdir())) == 2
    assert before == {
        item.path: (package.root / item.path).read_bytes() for item in package.files
    }


@pytest.mark.parametrize(
    "mode",
    [
        "numeric_cleanup",
        "wrong_cleanup",
        "changed_request",
        "changed_input",
        "temporary_reply",
        "malformed",
        "missing_cleanup",
        "exit7",
    ],
)
def test_incomplete_or_unbound_job_never_returns_prepared_faces(tmp_path, mode):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, mode)
    with pytest.raises(ValueError):
        prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    # A damaged staged copy does not change the retained input package.
    assert (package.root / "entry.dwg").read_bytes().startswith(b"synthetic CAD")


def test_capture_and_layer_identity_are_checked(tmp_path):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "ok")
    with pytest.raises(ValueError, match="другому заданию"):
        prepare_archive_faces(package, REQUEST, "b" * 32, ("SITE",), config)
    with pytest.raises(ValueError, match="другому заданию"):
        prepare_archive_faces(package, REQUEST, "a" * 32, ("OTHER",), config)


def test_cancel_running_preparation_allows_a_fresh_retry(tmp_path):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "hang")

    def cancel_after_child_started():
        if any(config.job_root.glob("*/native.json")):
            raise OperationCancelled("Cancelled by the project owner")

    with operation_cancellation(cancel_after_child_started):
        with pytest.raises(OperationCancelled):
            prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    assert (next(config.job_root.iterdir()) / "core.log").exists()
    face_core(config.core_executable, "ok")
    assert prepare_archive_faces(
        package, REQUEST, "a" * 32, ("SITE",), config
    ).layers == ("SITE",)
    assert len(list(config.job_root.iterdir())) == 2


def test_windows_completed_face_job_cleans_owned_tree_then_validates_again(
    tmp_path, monkeypatch
):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "ok")
    children = []

    def spawn(command, job):
        child = spawn_completed(command, job)
        children.append(child)
        return child

    monkeypatch.setattr(process, "_spawn", spawn)
    reply = prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    assert reply.layers == ("SITE",)
    assert children[0].stops == children[0].closes == 1
    assert (package.root / "entry.dwg").read_bytes().startswith(b"synthetic CAD")


@pytest.mark.parametrize(
    "mode",
    [
        "malformed",
        "missing_cleanup",
        "wrong_cleanup",
        "numeric_cleanup",
        "temporary_reply",
        "exit7",
        "changed_request",
        "changed_input",
    ],
)
def test_windows_remaining_descendant_does_not_bypass_face_evidence(
    tmp_path, monkeypatch, mode
):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, mode)
    monkeypatch.setattr(process, "_spawn", spawn_completed)
    with pytest.raises(NativeOperationError) as error:
        prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    assert not error.value.diagnostic.cleanup_confirmed
    assert error.value.diagnostic.signals == ("TerminateJobObject",)


@pytest.mark.parametrize(
    "mutated",
    [
        "native.json",
        "lifecycle.json",
        "request.txt",
        "package/entry.dwg",
        "Bootstrap.dwg",
    ],
)
def test_windows_face_evidence_changed_by_cleanup_is_refused(
    tmp_path, monkeypatch, mutated
):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "ok")

    def spawn(command, job):
        return spawn_completed(
            command,
            job,
            on_stop=lambda path: (path / mutated).write_bytes(
                b"modified during cleanup"
            ),
        )

    monkeypatch.setattr(process, "_spawn", spawn)
    with pytest.raises(NativeOperationError) as error:
        prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    assert error.value.diagnostic.cleanup_attempted
    assert not error.value.diagnostic.cleanup_confirmed
    assert error.value.diagnostic.tree_empty is True


def test_windows_face_cancellation_during_cleanup_remains_cancellation(
    tmp_path, monkeypatch
):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "ok")

    cancelled = [False]

    def cancel(_job):
        cancelled[0] = True

    def check_cancelled():
        if cancelled[0]:
            raise OperationCancelled("owner cancelled cleanup")

    monkeypatch.setattr(
        process,
        "_spawn",
        lambda command, job: spawn_completed(command, job, on_stop=cancel),
    )
    with operation_cancellation(check_cancelled):
        with pytest.raises(OperationCancelled):
            prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)


def test_late_final_timeout_has_typed_durable_core_diagnostics(tmp_path, monkeypatch):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "ok")
    monkeypatch.setattr(process, "_spawn", spawn_completed)
    original = archive_face_process._validate_completed_faces
    calls = []

    def validate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise process._Stop("timeout", "private timeout text / password")
        return original(*args, **kwargs)

    monkeypatch.setattr(archive_face_process, "_validate_completed_faces", validate)
    with pytest.raises(NativeOperationError) as error:
        prepare_archive_faces(package, REQUEST, "a" * 32, ("SITE",), config)
    diagnostic = error.value.diagnostic
    assert len(calls) == 3 and diagnostic.failure_code == "timeout"
    assert diagnostic.phase == "completion_validation" and diagnostic.exit_code == 0
    assert diagnostic.cleanup_confirmed and diagnostic.tree_empty is True
    assert diagnostic.signals == ("TerminateJobObject",)
    assert diagnostic.core_log_path.is_file()
    assert str(diagnostic.core_log_path) not in str(error.value)
    monkeypatch.setattr(operation_diagnostics, "_sink", None)
    private = tmp_path / "private-service"
    private.mkdir(mode=0o700)
    assert operation_diagnostics.configure_operation_diagnostics(private)
    assert operation_diagnostics.record_operation_failure(
        "a" * 32,
        code="CAD_PREPARATION_FAILED",
        error=error.value,
        kind="prepare_cad_capture",
    )
    raw = (private / "Logs/operations.jsonl").read_text()
    record = json.loads(raw.splitlines()[-1])
    assert record["native"]["failure_code"] == "timeout"
    assert record["native"]["phase"] == "completion_validation"
    assert record["native"]["core_log_path"] == str(diagnostic.core_log_path)
    assert "private timeout text / password" not in raw


def test_posix_timeout_diagnostic_keeps_actual_stop_signals(tmp_path):
    package, _, config = setup_job(tmp_path)
    face_core(config.core_executable, "hang")
    with pytest.raises(NativeOperationError) as error:
        prepare_archive_faces(
            package, REQUEST, "a" * 32, ("SITE",), replace(config, timeout_seconds=0.2)
        )
    assert error.value.diagnostic.failure_code == "timeout"
    assert error.value.diagnostic.signals
    assert all(
        signal in {"SIGTERM", "SIGKILL"} for signal in error.value.diagnostic.signals
    )
