"""HTTP dispatch owns one durable attempt; late workers cannot restart it."""
from app.composition import get_runtime
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from threading import Event, Thread

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api, planning_assistant as local
from app.agent_runtime import routes
from app.agent_runtime.gateway import ToolGateway
from app.agent_runtime.store import AgentRunStore
from test_zone_workflow import project_with_contour


@pytest.fixture
def execution(tmp_path, monkeypatch):
    app, project, reference = project_with_contour()
    path = tmp_path / "runs.sqlite3"
    store = AgentRunStore(path)
    stores = [store]
    monkeypatch.setattr(get_runtime(), "application", app)
    monkeypatch.setattr(routes, "_store", lambda: stores[-1])
    monkeypatch.setattr(local, "configured_model", lambda: "test")
    draft = {"operation": "zones", "scope_mode": "project", "zone": {"operation": "create",
        "label": "Сад", "geometry_reference": reference.model_dump(mode="json")}}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_kw: {"message": {"content": json.dumps(draft)}})
    worker = routes._run_in_background
    dispatched = []
    monkeypatch.setattr(routes, "_run_in_background", lambda *args: dispatched.append(args))
    http = FastAPI()
    http.include_router(routes.router)
    client = TestClient(http)
    created = client.post(f"/api/projects/{project.id}/agent-runs", json={"text": "Создай участок «Сад» из контура source-area."})
    assert created.status_code == 201, created.text
    run_id = created.json()["state"]["run_id"]
    url = f"/api/projects/{project.id}/agent-runs/{run_id}"
    yield app, project, stores, path, client, url, worker, dispatched
    for item in stores:
        item.close()


def test_compiled_task_does_not_schedule_until_run_and_duplicate_dispatch_is_idempotent(execution):
    app, project, _stores, _path, client, url, worker, dispatched = execution
    compiled = client.get(url).json()
    assert compiled["state"]["status"] == "queued"
    assert compiled["state"]["execution_attempt_id"] is None
    assert dispatched == []
    scheduled = client.post(url + "/run").json()
    assert scheduled["state"]["status"] == "scheduled"
    assert scheduled["state"]["execution_attempt_id"]
    assert len(dispatched) == 1
    for _ in range(3):
        assert client.post(url + "/run").json() == scheduled
    assert len(dispatched) == 1
    worker(*dispatched[0])
    finished_calculation = client.get(url).json()
    assert finished_calculation["state"]["status"] == "waiting_approval"
    assert finished_calculation["state"]["execution_attempt_id"] == scheduled["state"]["execution_attempt_id"]
    assert sum(event["kind"] == "run_started" for event in finished_calculation["events"]) == 1
    worker(*dispatched[0])
    assert client.get(url).json() == finished_calculation
    assert app.get(project.id).state_version == project.state_version


def test_duplicate_http_run_and_duplicate_worker_during_running_do_not_fail_active_attempt(execution, monkeypatch):
    _app, _project, _stores, _path, client, url, worker, dispatched = execution
    entered, release = Event(), Event()
    original = ToolGateway.call

    def delayed(self, context, call):
        entered.set()
        assert release.wait(10)
        return original(self, context, call)

    monkeypatch.setattr(ToolGateway, "call", delayed)
    scheduled = client.post(url + "/run").json()
    thread = Thread(target=worker, args=dispatched[0])
    thread.start()
    try:
        assert entered.wait(10)
        running = client.get(url).json()
        assert running["state"]["status"] == "running"
        assert running["state"]["execution_attempt_id"] == scheduled["state"]["execution_attempt_id"]
        assert client.post(url + "/run").json() == running
        assert len(dispatched) == 1
        worker(*dispatched[0])
        assert client.get(url).json() == running
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    result = client.get(url).json()
    assert result["state"]["status"] == "waiting_approval"
    assert result["state"]["failure"] is None
    assert sum(event["kind"] == "run_started" for event in result["events"]) == 1


def test_cancelled_scheduled_worker_cannot_run_after_explicit_resume(execution):
    _app, _project, _stores, _path, client, url, worker, dispatched = execution
    first = client.post(url + "/run").json()
    cancelled = client.post(url + "/cancel").json()
    assert cancelled["state"]["status"] == "cancelled"
    assert cancelled["state"]["execution_attempt_id"] == first["state"]["execution_attempt_id"]
    resumed = client.post(url + "/resume").json()
    assert resumed["state"]["status"] == "queued"
    assert resumed["state"]["execution_attempt_id"] is None
    second = client.post(url + "/run").json()
    assert second["state"]["execution_attempt_id"] != first["state"]["execution_attempt_id"]
    worker(*dispatched[0])
    assert client.get(url).json() == second
    worker(*dispatched[1])
    assert client.get(url).json()["state"]["status"] == "waiting_approval"


def test_http_answer_returns_compiled_unstarted_task_and_next_run_claims_new_attempt(execution):
    _app, project, _stores, _path, client, _url, worker, dispatched = execution
    created = client.post(f"/api/projects/{project.id}/agent-runs", json={"text": "Создай участок «Сад»."})
    assert created.status_code == 201, created.text
    run_id = created.json()["state"]["run_id"]
    url = f"/api/projects/{project.id}/agent-runs/{run_id}"
    first = client.post(url + "/run").json()
    worker(*dispatched[0])
    question = client.get(url).json()
    assert question["state"]["status"] == "waiting_question"
    answered = client.post(url + "/answer", json={"text": "Создай участок «Сад» из контура source-area."})
    assert answered.status_code == 200, answered.text
    ready = answered.json()
    assert ready["state"]["status"] == "queued"
    assert ready["state"]["execution_attempt_id"] is None
    assert len(dispatched) == 1
    assert client.get(url).json() == ready
    second = client.post(url + "/run").json()
    assert second["state"]["execution_attempt_id"] != first["state"]["execution_attempt_id"]
    worker(*dispatched[0])
    assert client.get(url).json() == second
    worker(*dispatched[1])
    assert client.get(url).json()["state"]["status"] == "waiting_approval"


@pytest.mark.parametrize("old_worker_fails", [False, True])
@pytest.mark.parametrize("process_restarted", [False, True])
def test_late_result_or_exception_from_cancelled_attempt_cannot_overwrite_new_attempt(execution, monkeypatch, old_worker_fails, process_restarted):
    app, project, stores, path, client, url, worker, dispatched = execution
    entered, release = Event(), Event()
    original = ToolGateway.call

    def delayed(self, context, call):
        entered.set()
        assert release.wait(10)
        if old_worker_fails:
            raise RuntimeError("Old execution lost its connection")
        return original(self, context, call)

    monkeypatch.setattr(ToolGateway, "call", delayed)
    client.post(url + "/run")
    thread = Thread(target=worker, args=dispatched[0])
    thread.start()
    try:
        assert entered.wait(10)
        if process_restarted:
            # The executing worker retains the first store. The replacement
            # process recovers and starts its own attempt through HTTP.
            stores.append(AgentRunStore(path, execution_owner_id="replacement-process"))
            assert client.get(url).json()["state"]["failure"]["code"] == "EXECUTION_INTERRUPTED"
        else:
            assert client.post(url + "/cancel").json()["state"]["status"] == "cancelled"
        assert client.post(url + "/resume").json()["state"]["status"] == "queued"
        second = client.post(url + "/run").json()
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert client.get(url).json() == second
    assert app.get(project.id).state_version == project.state_version
    monkeypatch.setattr(ToolGateway, "call", original)
    worker(*dispatched[1])
    assert client.get(url).json()["state"]["status"] == "waiting_approval"


def test_failed_worker_start_checkpoint_stops_scheduled_attempt_without_tool_work(execution, monkeypatch):
    app, project, stores, _path, client, url, worker, dispatched = execution
    scheduled = client.post(url + "/run").json()
    checkpoint = stores[0].checkpoint

    def unavailable_start(*args, **kwargs):
        if kwargs.get("kind") == "run_started":
            raise OSError("Worker claim checkpoint unavailable")
        return checkpoint(*args, **kwargs)

    monkeypatch.setattr(stores[0], "checkpoint", unavailable_start)
    worker(*dispatched[0])
    result = client.get(url).json()
    assert result["state"]["status"] == "failed"
    assert result["state"]["execution_attempt_id"] == scheduled["state"]["execution_attempt_id"]
    assert result["state"]["failure"]["code"] == "AGENT_RUNTIME_FAILED"
    assert result["state"]["tool_calls"] == []
    assert client.post(url + "/run").json() == result
    assert len(dispatched) == 1
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("stage", ["scheduled", "running"])
def test_process_restart_recovers_orphan_attempt_without_automatic_reexecution(execution, stage):
    _app, project, stores, path, client, url, worker, dispatched = execution
    scheduled = client.post(url + "/run").json()
    run_id = scheduled["state"]["run_id"]
    attempt = scheduled["state"]["execution_attempt_id"]
    if stage == "running":
        record, started = stores[-1].start_execution(project.id, run_id, attempt)
        assert started and record.state.status == "running"
    stores.append(AgentRunStore(path, execution_owner_id="restarted-process"))
    recovered = client.get(url).json()
    assert recovered["state"]["status"] == "failed"
    assert recovered["state"]["failure"]["code"] == "EXECUTION_INTERRUPTED"
    assert recovered["state"]["failure"]["retryable"] is True
    assert recovered["state"]["execution_attempt_id"] == attempt
    assert client.get(url).json() == recovered
    assert client.post(url + "/run").json() == recovered
    assert len(dispatched) == 1
    assert client.post(url + "/resume").json()["state"]["execution_attempt_id"] is None
    fresh = client.post(url + "/run").json()
    assert fresh["state"]["execution_attempt_id"] != attempt
    # An old worker/store may finish late. Its reads must not classify the
    # newer process's attempt as orphan or claim that process's worker.
    assert stores[0].get(project.id, run_id).model_dump(mode="json") == fresh
    observed, started = stores[0].start_execution(project.id, run_id, fresh["state"]["execution_attempt_id"])
    assert not started and observed.model_dump(mode="json") == fresh
    worker(*dispatched[0])
    assert client.get(url).json() == fresh
    worker(*dispatched[1])
    assert client.get(url).json()["state"]["status"] == "waiting_approval"


def test_two_store_connections_atomically_accept_only_one_execution(execution):
    _app, project, stores, path, client, url, _worker, _dispatched = execution
    run_id = client.get(url).json()["state"]["run_id"]
    with closing(AgentRunStore(path)) as second:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(store.claim_execution, project.id, run_id) for store in [stores[0], second]]
            results = [job.result() for job in jobs]
    assert sum(claimed for _, claimed in results) == 1
    assert len({record.state.execution_attempt_id for record, _ in results}) == 1
    assert all(record.state.status == "scheduled" for record, _ in results)


def test_legacy_running_checkpoint_without_worker_ownership_is_recovered_on_open(execution):
    _app, project, stores, path, client, url, _worker, _dispatched = execution
    record = stores[0].get(project.id, client.get(url).json()["state"]["run_id"])
    stores[0].checkpoint(project.id, record.state.run_id, expected_revision=record.revision,
        state=record.state.model_copy(update={"status": "running"}), kind="run_started", payload={})
    stores.append(AgentRunStore(path))
    restored = client.get(url).json()
    assert restored["state"]["status"] == "failed"
    assert restored["state"]["failure"]["code"] == "EXECUTION_INTERRUPTED"
