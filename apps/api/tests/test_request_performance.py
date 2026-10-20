"""Local bounded diagnostics retain request timing without copying private inputs."""

import asyncio
import json
import os
from unittest.mock import Mock

import anyio
import pytest
from test_preview_http_cancellation import FakePreview, wait_event

from app import main
from app.composition import get_application
from app.shared import performance
from app.shared.performance import (
    configure_performance_log,
    performance_span,
    performance_stage,
    performance_trace,
    record_hash,
)

PROJECT_ID = "43e3d40e-fc6c-4dcd-ad95-b0635c6fb3f7"
SECRET = "sk-private-token-and-user-path-/Users/private/drawing.dwg"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def local_log(tmp_path, monkeypatch):
    monkeypatch.setattr(performance, "_sink", None)
    assert configure_performance_log(tmp_path)

    def read():
        paths = [
            tmp_path / "Logs" / f"performance.jsonl{suffix}"
            for suffix in (".2", ".1", "")
        ]
        return [
            json.loads(line)
            for path in paths
            if path.exists()
            for line in path.read_text().splitlines()
        ]

    return tmp_path, read


def test_nested_stage_and_hash_aggregates_without_sensitive_metadata(local_log):
    _, read = local_log
    with performance_trace("pattern.preview", PROJECT_ID):
        with performance_stage("pattern.prepare", objects=2, path=SECRET, body=SECRET):
            record_hash(10, 0.001)
            with performance_stage("native.load", total=3, records=SECRET):
                record_hash(20, 0.002)
    events = read()
    assert [item["event"] for item in events] == [
        "trace_start",
        "stage_start",
        "stage_start",
        "stage_end",
        "stage_end",
        "trace_end",
    ]
    assert len({item["request_id"] for item in events}) == 1
    assert all(
        item["pid"] == os.getpid() and item["project_id"] == PROJECT_ID
        for item in events
    )
    assert events[2]["parent_stage_id"] == events[1]["stage_id"]
    assert (
        events[-1]["hash_count"] == 2
        and events[-1]["hash_bytes"] == 30
        and events[-1]["hash_ms"] == 3
    )
    assert events[3]["hash_bytes"] == 20 and events[4]["hash_bytes"] == 30
    assert events[-1]["stage_summary"]["native.load"]["count"] == 1
    assert SECRET not in json.dumps(events)
    assert events[0]["source_manifest_sha256"] is None
    assert "platform" in events[0] and "python_version" in events[0]


@pytest.mark.anyio
async def test_threadpool_keeps_request_and_parent_stage_context(local_log):
    _, read = local_log

    @performance_span("native.load")
    def worker():
        record_hash(123, 0.001)

    with (
        performance_trace("pattern.preview", PROJECT_ID),
        performance_stage("pattern.prepare"),
    ):
        await anyio.to_thread.run_sync(worker)
    events = read()
    stages = [event for event in events if event["event"] == "stage_start"]
    assert stages[1]["parent_stage_id"] == stages[0]["stage_id"]
    assert events[-1]["hash_count"] == 1 and events[-1]["hash_bytes"] == 123
    assert performance._current_trace.get() is None


def test_repeated_labels_are_sampled_and_aggregated_without_unbounded_event_list(
    local_log,
):
    _, read = local_log
    with performance_trace("pattern.preview", PROJECT_ID):
        for _ in range(100):
            with performance_stage("native.load"):
                record_hash(2, 0.001)
    events = read()
    assert len(events) == 18
    assert events[-1]["suppressed_stage_count"] == 92
    assert events[-1]["stage_summary"]["native.load"]["count"] == 100
    assert events[-1]["stage_summary"]["native.load"]["hash_count"] == 100
    assert events[-1]["hash_bytes"] == 200


def test_batch_children_only_update_summary_while_major_stages_keep_samples(local_log):
    _, read = local_log
    with performance_trace("pattern.preview", PROJECT_ID):
        with performance_stage("domain.processing"):
            for _ in range(20):
                with performance_stage("domain.batch"):
                    with performance_stage("domain.batch.masks", objects=256):
                        record_hash(7, 0.001)
    events = read()
    assert not any(event.get("stage") == "domain.batch.masks" for event in events)
    for event_type in ("stage_start", "stage_end"):
        assert (
            sum(
                event["event"] == event_type and event.get("stage") == "domain.batch"
                for event in events
            )
            == 8
        )
        assert any(
            event["event"] == event_type and event.get("stage") == "domain.processing"
            for event in events
        )
    summary = events[-1]["stage_summary"]
    assert summary["domain.batch.masks"]["count"] == 20
    assert summary["domain.batch.masks"]["hash_count"] == 20
    assert summary["domain.batch.masks"]["hash_bytes"] == 140
    assert summary["domain.batch.masks"]["hash_ms"] == 20
    assert summary["domain.batch"]["count"] == 20
    assert events[-1]["hash_count"] == 20 and events[-1]["hash_bytes"] == 140
    assert events[-1]["suppressed_stage_count"] == 32


def test_aggregate_only_batch_child_failure_keeps_error_event_and_exception(local_log):
    _, read = local_log
    failure = RuntimeError(SECRET)
    with pytest.raises(RuntimeError) as caught:
        with performance_trace("pattern.preview", PROJECT_ID):
            with performance_stage("domain.batch"):
                with performance_stage("domain.batch.masks", objects=256):
                    record_hash(13, 0.002)
                    raise failure
    assert caught.value is failure
    events = read()
    child = [event for event in events if event.get("stage") == "domain.batch.masks"]
    assert len(child) == 1 and child[0]["event"] == "stage_end"
    assert child[0]["sampled"] is False
    assert child[0]["outcome"] == "error"
    assert child[0]["error_class"] == "RuntimeError"
    assert child[0]["hash_count"] == 1 and child[0]["hash_bytes"] == 13
    assert events[-1]["event"] == "trace_end"
    assert events[-1]["outcome"] == "error"
    assert events[-1]["stage_summary"]["domain.batch.masks"]["count"] == 1
    assert events[-1]["stage_summary"]["domain.batch.masks"]["hash_bytes"] == 13
    assert SECRET not in json.dumps(events)
    assert performance._current_trace.get() is None


def test_rotation_keeps_three_bounded_files_and_valid_json(local_log, monkeypatch):
    root, read = local_log
    monkeypatch.setattr(performance, "_MAX_BYTES", 2048)
    for _ in range(100):
        with performance_trace("pattern.preview", PROJECT_ID):
            pass
    paths = list((root / "Logs").iterdir())
    assert len(paths) == 3
    assert {path.name for path in paths} == {
        "performance.jsonl",
        "performance.jsonl.1",
        "performance.jsonl.2",
    }
    assert all(path.stat().st_size <= 2048 for path in paths)
    assert read()


def _symlink_or_skip(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip(
                "Windows symlink fixture requires Developer Mode or symlink privilege"
            )
        raise


@pytest.mark.parametrize("kind", ["directory_link", "file_link", "hardlink"])
def test_existing_external_link_target_is_never_written(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(performance, "_sink", None)
    root = tmp_path / "profile"
    root.mkdir()
    outside = tmp_path / "external"
    outside.mkdir()
    secret_file = outside / "private.jsonl"
    secret_file.write_text(SECRET)
    if kind == "directory_link":
        _symlink_or_skip(root / "Logs", outside, directory=True)
    else:
        (root / "Logs").mkdir()
        path = root / "Logs" / "performance.jsonl"
        if kind == "file_link":
            _symlink_or_skip(path, secret_file)
        else:
            os.link(secret_file, path)
    assert not configure_performance_log(root)
    with performance_trace("pattern.preview", PROJECT_ID):
        pass
    assert secret_file.read_text() == SECRET


def test_read_only_profile_or_write_failure_never_changes_business_result(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(performance, "_sink", None)
    # Windows opens the log with a guarded Win32 HANDLE, bypassing os.open.
    # This shared boundary injects an actual denied log open on either platform.
    denied_open = Mock(side_effect=PermissionError(SECRET))
    monkeypatch.setattr(performance._LocalLog, "_open", denied_open)
    assert not configure_performance_log(tmp_path)
    denied_open.assert_called_once()
    with performance_trace("pattern.preview", PROJECT_ID):
        with performance_stage("native.load"):
            record_hash(1, 0.001)
    assert performance._current_trace.get() is None


@pytest.mark.skipif(
    os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0,
    reason="Real chmod denial requires a non-root POSIX user; Windows access uses ACLs",
)
def test_posix_read_only_profile_keeps_calculation_available(tmp_path, monkeypatch):
    monkeypatch.setattr(performance, "_sink", None)
    profile = tmp_path / "read-only-profile"
    profile.mkdir()
    profile.chmod(0o500)
    try:
        assert not configure_performance_log(profile)
        with performance_trace("pattern.preview", PROJECT_ID):
            result = 2 + 2
        assert result == 4
        assert performance._current_trace.get() is None
        assert not (profile / "Logs").exists()
    finally:
        profile.chmod(0o700)


def test_logging_failure_does_not_replace_calculation_failure(local_log, monkeypatch):
    sink = performance._sink
    monkeypatch.setattr(sink, "write", Mock(side_effect=OSError(SECRET)))
    failure = ValueError("Actual calculation failure")
    with pytest.raises(ValueError) as caught:
        with (
            performance_trace("pattern.preview", PROJECT_ID),
            performance_stage("native.load"),
        ):
            raise failure
    assert caught.value is failure
    assert performance._current_trace.get() is None


def test_disabled_logging_does_not_run_stage_clock(monkeypatch):
    monkeypatch.setattr(
        performance, "_current_trace", performance.ContextVar("disabled", default=None)
    )
    clock = Mock(side_effect=AssertionError("Disabled stage must be free"))
    monkeypatch.setattr(performance, "monotonic", clock)
    with performance_stage("native.load"):
        record_hash(10, 0.001)
    clock.assert_not_called()


class TracedPreview(FakePreview):
    def __init__(self, outcome):
        super().__init__(outcome == "cancelled")
        self.outcome = outcome

    @performance_span("pattern.prepare")
    def preview_pattern(self, project_id, payload):
        record_hash(25, 0.002)
        result = super().preview_pattern(project_id, payload)
        if self.outcome == "error":
            raise RuntimeError(SECRET)
        return result


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome,status", [("ok", 200), ("error", 500), ("cancelled", 499)]
)
async def test_real_asgi_logs_normal_error_cancel_without_private_inputs(
    local_log, monkeypatch, outcome, status
):
    _, read = local_log
    fake = TracedPreview(outcome)
    monkeypatch.setitem(main.app.dependency_overrides, get_application, lambda: fake)
    monkeypatch.setattr(main, "get_application", lambda: fake)
    body = json.dumps(
        dict(type="fill", base_plan_version=1, zone_ids=[SECRET], target_count=1)
    ).encode()
    queue = asyncio.Queue()
    await queue.put(dict(type="http.request", body=body, more_body=False))
    messages = []

    async def send(message):
        messages.append(message)

    path = f"/api/projects/{PROJECT_ID}/plan/patterns/preview"
    scope = dict(
        type="http",
        asgi=dict(version="3.0", spec_version="2.3"),
        http_version="1.1",
        method="POST",
        scheme="http",
        path=path,
        raw_path=path.encode(),
        query_string=("token=" + SECRET).encode(),
        root_path="",
        headers=[
            (b"content-type", b"application/json"),
            (b"authorization", SECRET.encode()),
        ],
        client=("127.0.0.1", 12345),
        server=("127.0.0.1", 8000),
    )
    task = asyncio.create_task(main.app(scope, queue.get, send))
    await wait_event(fake.started)
    if outcome == "cancelled":
        await queue.put(dict(type="http.disconnect"))
    await asyncio.wait_for(task, timeout=3)
    assert (
        next(
            message["status"]
            for message in messages
            if message["type"] == "http.response.start"
        )
        == status
    )
    events = read()
    assert events[0]["event"] == "trace_start" and events[-1]["event"] == "trace_end"
    assert events[-1]["outcome"] == outcome and events[-1]["status_code"] == status
    assert events[-1]["hash_count"] == 1 and events[-1]["hash_bytes"] == 25
    assert any(event.get("stage") == "pattern.prepare" for event in events)
    assert SECRET not in json.dumps(events)
    assert performance._current_trace.get() is None
    if outcome == "error":
        assert events[-1]["error_class"] == "RuntimeError"
    if outcome == "cancelled":
        assert events[-1]["error_class"] == "OperationCancelled"


def test_existing_oversized_diagnostic_files_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(performance, "_sink", None)
    monkeypatch.setattr(performance, "_MAX_BYTES", 2048)
    directory = tmp_path / "Logs"
    directory.mkdir()
    for suffix in ("", ".1", ".2"):
        (directory / ("performance.jsonl" + suffix)).write_bytes(b"x" * 4096)
    assert configure_performance_log(tmp_path)
    with performance_trace("pattern.preview", PROJECT_ID):
        pass
    assert sum(path.stat().st_size for path in directory.iterdir()) <= 3 * 2048
