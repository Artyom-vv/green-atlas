"""Real subprocesses, synthetic Core/worker/package; no AutoCAD or database."""

import json
import os
import plistlib
import signal
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path

import psutil
import pytest

from app.native_query import process
from app.native_query.contracts import PROTOCOL, NativeObjectQuery
from app.native_query.process import run_native_query
from app.native_query.process_contracts import (
    CadPackageFile,
    NativeInputPackage,
    NativeQueryProcessConfig,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX Core supervisor")
FIXTURE = Path(__file__).with_name("native_query_process_fixture.py")


def setup_job(tmp_path, mode="ok", **options):
    additional_routes = options.pop("additional_routes", ())
    source = tmp_path / "исходный пакет"
    source.mkdir()
    files = []
    for name in ("entry.dwg", "refs/reference.dwg"):
        path = source / name
        path.parent.mkdir(exist_ok=True)
        content = ("synthetic CAD " + name).encode()
        path.write_bytes(content)
        files.append(CadPackageFile(name, sha256(content).hexdigest(), len(content)))
    package = NativeInputPackage(source, "entry.dwg", tuple(files))
    query = NativeObjectQuery(
        request_id="a" * 32, source_sha256=files[0].sha256, units_code=6,
        targets=[{"route": "6E16/7BF", "capability": "area", "additional_routes": additional_routes}], points=[(1.0, 2.0, 0.0)],
    )
    worker = tmp_path / "GreenAtlasQuery.bundle"
    binary = worker / process.WORKER_BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"curated worker, never executed by the fake Core")
    binary.chmod(0o700)
    (worker / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": process.WORKER_ID,
        "CFBundleExecutable": "GreenAtlasBridge",
        "CFBundleShortVersionString": "0.1.38",
    }))
    jobs = tmp_path / "расчёты with spaces"
    jobs.mkdir()
    reply = {
        "schema": PROTOCOL, "scope": "selected_instance_measurements_only",
        "request_id": query.request_id, "request_sha256": "0" * 64,
        "source_sha256": query.source_sha256, "units_code": query.units_code,
        "plugin_version": "0.1.38", "database_revision": "fake-db-guid",
        "database_modified_flags": 0, "point_count": 1, "elapsed_ms": 1.5,
        "objects": [{
            "route": query.targets[0].route, "entity_type": "AcDbPolyline", "layer": "Здания",
            "additional_routes": list(additional_routes),
            "capability": "area", "interior_known": True, "preparation_error": "",
            "prepare_ms": 0.5, "answers": [{
                "point_index": 0, "status": 0, "membership": "occupied",
                "distance_units": 1.25, "error": "",
            }],
        }],
    }
    core = tmp_path / "fake-core"
    core.write_text(
        f"#!{sys.executable}\nMODE = {mode!r}\n"
        f"OPTIONS = {dict(reply=reply, source=str(source / 'entry.dwg'), **options)!r}\n"
        + FIXTURE.read_text(), encoding="utf-8",
    )
    core.chmod(0o700)
    template = tmp_path / "bootstrap.dwt"
    template.write_bytes(b"synthetic empty template")
    config = NativeQueryProcessConfig(
        core, worker, jobs, sha256(binary.read_bytes()).hexdigest(), "0.1.38",
        bootstrap_template=template,
        timeout_seconds=5, terminate_grace_seconds=0.1, poll_seconds=0.01,
        min_free_bytes=1,
    )
    return package, query, config


def test_success_uses_private_copies_profile_and_imported_codec(tmp_path, monkeypatch):
    package, query, config = setup_job(tmp_path)
    calls = []
    popen = subprocess.Popen

    def spawn(command, **kwargs):
        calls.append((command, kwargs))
        return popen(command, **kwargs)

    monkeypatch.setattr(process.subprocess, "Popen", spawn)
    result = run_native_query(package, query, config)
    assert result.failure is None and result.reply is not None
    receipt = result.receipt
    assert receipt.exit_code == 0 and receipt.signals == ()
    assert receipt.diagnostic_reply is result.reply
    assert receipt.package_sha256 == package.sha256
    assert receipt.source_verified_before and receipt.source_verified_after
    assert receipt.staged_verified_before and receipt.staged_verified_after
    job = receipt.job_directory
    assert job.parent == config.job_root and stat_mode(job) == 0o700
    for item in package.files:
        staged = job / "package" / item.path
        assert staged.read_bytes() == (package.root / item.path).read_bytes()
        assert not os.path.samefile(staged, package.root / item.path)
        assert staged.stat().st_nlink == 1
    assert (job / "QueryWorker.dbx" / process.WORKER_BINARY).read_bytes() == (
        config.worker_bundle / process.WORKER_BINARY
    ).read_bytes()
    assert stat_mode(job / "QueryWorker.dbx" / process.WORKER_BINARY) == 0o700
    assert receipt.diagnostic_output == (job / "native.json").read_bytes()
    assert receipt.log == (job / "core.log").read_bytes()
    assert b"fake Core diagnostic" in receipt.log
    command, flags = calls[0]
    assert command == receipt.command and command[0] == str(config.core_executable)
    assert flags["shell"] is False and flags["start_new_session"] is True
    assert flags["cwd"] == job and flags["stdin"] == subprocess.DEVNULL
    observed = json.loads((job / "observed.json").read_bytes())
    assert observed["pid"] == observed["pgid"] == observed["sid"]
    assert observed["pgid"] != os.getpgrp()
    assert (job / "request.txt").read_bytes() == process.encode_request(query, job / "native.json")
    script = (job / "query.scr").read_text()
    assert "GAQUERYPACKAGE" in script and "QueryWorker.dbx" in script
    assert script.isascii()
    assert (job / "package-entry.txt").read_text(encoding="utf-8") == str(job / "package" / package.entry) + "\n"
    assert observed["drawing"] == str(job / "Bootstrap.dwg")
    assert (job / "Bootstrap.dwg").read_bytes() == config.bootstrap_template.read_bytes()
    assert receipt.lifecycle_verified
    repeated = run_native_query(package, query, config)
    assert repeated.failure is None and repeated.receipt.job_directory != job
    index = command.index("/isolate")
    assert repeated.receipt.command[index + 1:] != command[index + 1:]


def stat_mode(path):
    return path.stat().st_mode & 0o777


def test_area_group_request_is_passed_through_the_current_codec(tmp_path):
    package, query, config = setup_job(tmp_path, additional_routes=("6E16/78DC", "6E16/16020"))
    result = run_native_query(package, query, config)
    assert result.failure is None and result.reply is not None
    assert result.reply.objects[0].additional_routes == query.targets[0].additional_routes
    job = result.receipt.job_directory
    assert (job / "request.txt").read_bytes() == process.encode_request(query, job / "native.json")


@pytest.mark.parametrize("mode", ["missing_cleanup", "wrong_cleanup", "false_cleanup", "numeric_cleanup", "bootstrap_mutation"])
def test_valid_reply_and_zero_exit_without_verified_cleanup_is_not_success(tmp_path, mode):
    package, query, config = setup_job(tmp_path, mode)
    result = run_native_query(package, query, config)
    assert result.reply is None and result.failure.code == "invalid_reply"
    assert result.receipt.exit_code == 0 and not result.receipt.lifecycle_verified
    assert result.receipt.diagnostic_reply is not None


@pytest.mark.parametrize("code", [254, 7])
def test_nonzero_exit_never_promotes_a_valid_native_reply(tmp_path, code):
    package, query, config = setup_job(tmp_path, f"exit{code}")
    result = run_native_query(package, query, config)
    assert result.reply is None and result.failure.code == "process_exit"
    assert result.receipt.exit_code == code
    assert result.receipt.diagnostic_reply is not None
    assert result.receipt.diagnostic_reply.objects[0].answers[0].distance_units == 1.25
    assert result.receipt.diagnostic_output
    assert result.receipt.source_verified_after and result.receipt.staged_verified_after


@pytest.mark.parametrize("mode", ["missing", "malformed", "identity", "version", "dbmod", "request_mutation"])
def test_missing_corrupt_or_changed_identity_is_a_typed_failure(tmp_path, mode):
    package, query, config = setup_job(tmp_path, mode)
    result = run_native_query(package, query, config)
    assert result.reply is None and result.failure.code == "invalid_reply"
    assert result.receipt.exit_code == 0
    assert result.receipt.diagnostic_reply is None
    assert result.receipt.source_verified_after


@pytest.mark.parametrize("mode", ["source_mutation", "staged_mutation"])
def test_package_is_checked_after_worker_exit_including_xrefs(tmp_path, mode):
    package, query, config = setup_job(tmp_path, mode)
    result = run_native_query(package, query, config)
    assert result.reply is None and result.failure.code == "integrity_failure"
    assert result.receipt.diagnostic_reply is not None
    assert result.receipt.source_verified_after == (mode != "source_mutation")
    assert result.receipt.staged_verified_after == (mode != "staged_mutation")


@pytest.mark.parametrize("mode", ["output_flood", "temporary_flood", "log_flood"])
def test_bounded_output_and_log_are_retained_as_diagnostics(tmp_path, mode):
    package, query, config = setup_job(tmp_path, mode, bytes=128 * 1024)
    config = replace(config, max_reply_bytes=2048, max_log_bytes=1024)
    result = run_native_query(package, query, config)
    expected = "log_limit" if mode == "log_flood" else "output_limit"
    assert result.reply is None and result.failure.code == expected
    assert len(result.receipt.log) <= 1024
    assert len(result.receipt.diagnostic_output) <= 2048
    assert result.receipt.signals
    for name, budget in (("core.log", 1024), ("native.json", 2048), ("native.json.tmp", 2048)):
        path = result.receipt.job_directory / name
        if path.exists():
            assert path.stat().st_size <= budget
    assert result.receipt.source_verified_after


@pytest.mark.parametrize("mode", ["hang", "ignore_term"])
def test_timeout_does_not_promote_reply_and_escalates_if_needed(tmp_path, mode):
    package, query, config = setup_job(tmp_path, mode)
    # Include cold interpreter/staging time; the fixture writes its reply and
    # installs the signal handler before entering the controlled hang.
    config = replace(config, timeout_seconds=2)
    result = run_native_query(package, query, config)
    assert result.failure.code == "timeout" and result.reply is None
    assert result.receipt.diagnostic_reply is not None
    assert "SIGTERM" in result.receipt.signals
    assert ("SIGKILL" in result.receipt.signals) == (mode == "ignore_term")
    assert result.receipt.elapsed_seconds < 5


def test_cancellation_kills_only_owned_group_and_its_descendants(tmp_path, monkeypatch):
    package, query, config = setup_job(tmp_path, "child")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    signalled = []
    killpg = os.killpg

    def signal_group(pid, sig):
        if sig:
            signalled.append((pid, sig))
        return killpg(pid, sig)

    monkeypatch.setattr(process.os, "killpg", signal_group)
    try:
        result = run_native_query(package, query, config, cancelled=lambda: any(
            (directory / "child.pid").exists() for directory in config.job_root.iterdir()
        ))
        assert result.failure.code == "cancelled" and result.reply is None
        assert unrelated.poll() is None
        observed = json.loads((result.receipt.job_directory / "observed.json").read_text())
        assert {pid for pid, _ in signalled} == {observed["pid"]}
        assert [sig for _, sig in signalled] == [signal.SIGTERM, signal.SIGKILL]
        child_pid = int((result.receipt.job_directory / "child.pid").read_text())
        assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
        assert result.receipt.source_verified_after
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_precancel_does_not_spawn_or_create_job(tmp_path, monkeypatch):
    package, query, config = setup_job(tmp_path)
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **k: pytest.fail("Unexpected launch"))
    result = run_native_query(package, query, config, cancelled=lambda: True)
    assert result.failure.code == "cancelled" and result.receipt.job_directory is None
    assert list(config.job_root.iterdir()) == []


def test_launch_failure_is_typed_and_sources_are_reverified(tmp_path, monkeypatch):
    package, query, config = setup_job(tmp_path)

    def unavailable(*args, **kwargs):
        raise OSError("Core license launcher unavailable")

    monkeypatch.setattr(process.subprocess, "Popen", unavailable)
    result = run_native_query(package, query, config)
    assert result.failure.code == "launch_failed" and result.receipt.exit_code is None
    assert result.receipt.source_verified_after and result.receipt.staged_verified_after


@pytest.mark.parametrize("problem", ["hash", "missing", "symlink", "directory_link", "wrong_query", "gui_bundle", "worker_hash", "nested_jobs", "request_limit"])
def test_invalid_explicit_inputs_never_launch(tmp_path, monkeypatch, problem):
    package, query, config = setup_job(tmp_path)
    if problem == "hash":
        (package.root / "entry.dwg").write_bytes(b"changed")
    elif problem in {"missing", "symlink"}:
        path = package.root / "refs/reference.dwg"
        content = path.read_bytes()
        path.unlink()
        if problem == "symlink":
            target = tmp_path / "external.dwg"
            target.write_bytes(content)
            path.symlink_to(target)
    elif problem == "directory_link":
        (package.root / "refs").rename(tmp_path / "external")
        (package.root / "refs").symlink_to(tmp_path / "external", target_is_directory=True)
    elif problem == "wrong_query":
        query = query.model_copy(update={"source_sha256": "b" * 64})
    elif problem == "gui_bundle":
        path = config.worker_bundle / "Contents/Info.plist"
        info = plistlib.loads(path.read_bytes())
        info["CFBundleIdentifier"] = "ru.green-atlas.autocad-bridge"
        path.write_bytes(plistlib.dumps(info))
    elif problem == "worker_hash":
        config = replace(config, worker_binary_sha256="b" * 64)
    elif problem == "nested_jobs":
        config = replace(config, job_root=package.root)
    elif problem == "request_limit":
        config = replace(config, max_request_bytes=1)
    monkeypatch.setattr(process.subprocess, "Popen", lambda *a, **k: pytest.fail("Unexpected launch"))
    result = run_native_query(package, query, config)
    assert result.reply is None and result.failure.code == "invalid_input"


def test_native_output_link_cannot_read_or_truncate_user_file(tmp_path):
    package, query, config = setup_job(tmp_path, "symlink_output")
    source = (package.root / "entry.dwg").read_bytes()
    result = run_native_query(package, query, config)
    assert result.failure.code == "invalid_reply" and result.reply is None
    assert not result.receipt.diagnostic_output
    assert (package.root / "entry.dwg").read_bytes() == source


def test_unpublished_atomic_write_is_diagnostic_only_even_on_exit_zero(tmp_path):
    package, query, config = setup_job(tmp_path, "temporary_reply")
    result = run_native_query(package, query, config)
    assert result.failure.code == "invalid_reply" and result.reply is None
    assert result.receipt.exit_code == 0 and result.receipt.diagnostic_reply is not None
    assert (result.receipt.job_directory / "native.json.tmp").is_file()


def test_worker_cannot_redirect_log_to_user_file(tmp_path):
    package, query, config = setup_job(tmp_path, "linked_log")
    source = (package.root / "entry.dwg").read_bytes()
    result = run_native_query(package, query, config)
    assert result.failure.code == "io_failure" and result.reply is None
    assert (package.root / "entry.dwg").read_bytes() == source


def test_successful_leader_exit_cannot_leave_worker_descendants(tmp_path):
    package, query, config = setup_job(tmp_path, "orphan")
    result = run_native_query(package, query, config)
    assert result.failure.code == "process_group_alive" and result.reply is None
    assert result.receipt.exit_code == 0
    assert "SIGKILL" in result.receipt.signals
    child_pid = int((result.receipt.job_directory / "child.pid").read_text())
    assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE


@pytest.mark.parametrize("path", ["../file.dwg", "/tmp/file.dwg", "a//file.dwg", "a/./file.dwg", "C:\\file.dwg", "file\n.dwg", "file.txt"])
def test_catalogue_rejects_unsafe_or_non_cad_relative_paths(path):
    with pytest.raises(ValueError):
        CadPackageFile(path, "a" * 64, 1)


def test_catalogue_is_immutable_and_rejects_ambiguous_names(tmp_path):
    package, query, config = setup_job(tmp_path)
    with pytest.raises(FrozenInstanceError):
        package.entry = "other.dwg"
    with pytest.raises(ValueError):
        replace(package, files=(*package.files, replace(package.files[0], path="ENTRY.dwg")))
    with pytest.raises(ValueError):
        replace(package, entry="absent.dwg")
    assert package.sha256 == replace(package, files=tuple(reversed(package.files))).sha256
    with pytest.raises(ValueError):
        replace(config, timeout_seconds=float("nan"))
