"""The map command is complete only after an exact UI acknowledgment."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json

import pytest

from app import planning_assistant as local
from test_agent_commit_recovery import persistent_runtime


def create_control(runtime, monkeypatch, text="Покажи участок west на карте."):
    app, project, store, client = runtime
    def no_model(*_args, **_kwargs):
        raise AssertionError("A source-bound map command must not invoke the model")
    monkeypatch.setattr(local, "local_json", no_model)
    created = client.post(f"/api/projects/{project.id}/agent-runs", json={"text": text})
    assert created.status_code == 201, created.text
    return f"/api/projects/{project.id}/agent-runs/{created.json()['state']['run_id']}"


def ready_control(runtime, monkeypatch, text="Покажи участок west на карте."):
    app, project, _store, client = runtime
    before = app.get(project.id).model_dump(mode="json")
    path = create_control(runtime, monkeypatch, text)
    started = client.post(path + "/run")
    assert started.status_code == 200, started.text
    waiting = client.get(path).json()
    assert waiting["state"]["status"] == "waiting_ui", waiting
    assert waiting["state"]["pending_approval"] is None
    assert waiting["state"]["control_result"] is None
    prepared = [event for event in waiting["events"] if event["kind"] == "tool_result"]
    assert len(prepared) == 1 and prepared[0]["payload"]["name"] == "focus_zone"
    assert prepared[0]["payload"]["verification"]["status"] == "verified"
    assert app.get(project.id).model_dump(mode="json") == before
    return path, waiting["state"]["control_command"], waiting


def acknowledgement(command, *, status="completed", error_code=None):
    return {"command_id": command["id"], **{key: command[key] for key in (
        "project_id", "run_id", "execution_attempt_id", "zone_id", "geometry_version", "geometry_digest")},
        "status": status, "error_code": error_code}


@pytest.mark.parametrize("text", ["Покажи участок west на карте.", "Покажи участок «West» на карте.", "Покажи участок 1 на карте."])
@pytest.mark.parametrize("has_plan", [True, False])
def test_exact_control_proof_and_ack_preserve_complete_project(persistent_runtime, monkeypatch, text, has_plan):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    if "участок 1" in text:
        current = app.get(project.id)
        current.planting_zones[0].label = "Допустимая область 1"
        app.repository.save(current)
    if not has_plan:
        current = app.get(project.id)
        current.plan = None
        app.repository.save(current)
    path, command, waiting = ready_control(runtime, monkeypatch, text)
    proof = client.get(path + "/control-command", params={"command_id": command["id"]})
    assert proof.status_code == 200, proof.text
    assert proof.json()["command"] == command
    serialized = proof.json()["geometry_json"]
    assert sha256(serialized.encode("utf-8")).hexdigest() == command["geometry_digest"]
    zone = next(item for item in app.get(project.id).planting_zones if item.id == command["zone_id"])
    assert json.loads(serialized) == zone.geometry
    assert client.get(path + "/control-command", params={"command_id": "wrong-command"}).status_code == 409
    assert client.post(path + "/run").json()["revision"] == waiting["revision"]
    before = app.get(project.id).model_dump(mode="json")
    done = client.post(path + "/control-result", json=acknowledgement(command))
    assert done.status_code == 200, done.text
    assert done.json()["state"]["status"] == "finished"
    assert done.json()["state"]["outcome_ref"] == "control:" + command["id"]
    assert done.json()["events"][-1]["kind"] == "control_completed"
    assert client.post(path + "/control-result", json=acknowledgement(command)).json()["revision"] == done.json()["revision"]
    assert client.get(path + "/control-command").status_code == 409
    assert app.get(project.id).model_dump(mode="json") == before


@pytest.mark.parametrize("answer_text", ["Участок east", "«Выбранный участок 99 из сада»"])
def test_missing_target_short_answer_stays_in_same_control_run(persistent_runtime, monkeypatch, answer_text):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    if answer_text.startswith("«"):
        current = app.get(project.id)
        current.planting_zones[1].label = answer_text[1:-1]
        app.repository.save(current)
    path = create_control(runtime, monkeypatch, "Покажи участок на карте.")
    client.post(path + "/run")
    question = client.get(path).json()
    assert question["state"]["status"] == "waiting_question"
    assert question["state"]["pending_question"]["slot"] == "control"
    assert question["state"]["control_command"] is None
    before = app.get(project.id).model_dump(mode="json")
    answer = client.post(path + "/answer", json={"text": answer_text})
    assert answer.status_code == 200, answer.text
    assert answer.json()["state"]["status"] == "queued"
    assert answer.json()["state"]["control_command"] is None
    client.post(path + "/run")
    waiting = client.get(path).json()
    assert waiting["state"]["status"] == "waiting_ui", waiting
    assert waiting["state"]["control_command"]["zone_id"] == "east"
    assert app.get(project.id).model_dump(mode="json") == before


def test_repeated_unresolved_control_answer_keeps_full_history_without_dispatch(persistent_runtime, monkeypatch):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    path = create_control(runtime, monkeypatch, "Покажи участок на карте.")
    before = app.get(project.id).model_dump(mode="json")
    for answer_text in ("Участок", "Какой-нибудь"):
        client.post(path + "/run")
        question = client.get(path).json()
        assert question["state"]["status"] == "waiting_question"
        if answer_text == "Участок":
            assert client.post(path + "/cancel").json()["state"]["status"] == "cancelled"
            resumed = client.post(path + "/resume")
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["state"]["status"] == "queued"
            assert resumed.json()["state"]["resolved_scope"] is None
            client.post(path + "/run")
            assert client.get(path).json()["state"]["status"] == "waiting_question"
        response = client.post(path + "/answer", json={"text": answer_text})
        assert response.status_code == 200, response.text
        assert response.json()["state"]["intent"]["source_turns"][-1] == answer_text
    client.post(path + "/run")
    pending = client.get(path).json()
    assert pending["state"]["status"] == "waiting_question"
    assert pending["state"]["control_command"] is None
    assert pending["state"]["intent"]["source_turns"] == ["Покажи участок на карте.", "Участок", "Какой-нибудь"]
    assert not any(event["kind"] == "control_issued" for event in pending["events"])
    assert app.get(project.id).model_dump(mode="json") == before


def test_quoted_selection_word_cannot_replace_named_control_target(persistent_runtime, monkeypatch):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    current = app.get(project.id)
    current.planting_zones[1].label = "Выбранный сад"
    current = app.repository.save(current)
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: pytest.fail("Control must bypass the model"))
    created = client.post(f"/api/projects/{project.id}/agent-runs", json={
        "text": "Покажи участок «Выбранный сад» на карте.", "selection_context": {
            "project_id": project.id, "state_version": current.state_version, "plan_version": current.plan.version,
            "zone_ids": ["west"], "object_ids": []}})
    assert created.status_code == 201, created.text
    path = f"/api/projects/{project.id}/agent-runs/{created.json()['state']['run_id']}"
    assert created.json()["state"]["intent"]["selection_binding"] is None
    client.post(path + "/run")
    waiting = client.get(path).json()
    assert waiting["state"]["status"] == "waiting_ui", waiting
    assert waiting["state"]["control_command"]["zone_id"] == "east"


@pytest.mark.parametrize("text", ["Покажи участок «Сад» на карте.", "Покажи участки west и east на карте.",
    "Покажи участок west на карте только после разрешения архитектора."])
def test_ambiguous_or_conditional_control_never_issues_ui_command(persistent_runtime, monkeypatch, text):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    current = app.get(project.id)
    for zone in current.planting_zones:
        zone.label = "Сад"
    app.repository.save(current)
    path = create_control(runtime, monkeypatch, text)
    before = app.get(project.id).model_dump(mode="json")
    client.post(path + "/run")
    question = client.get(path).json()
    assert question["state"]["status"] == "waiting_question", question
    assert question["state"]["control_command"] is None
    assert not any(event["kind"] == "control_issued" for event in question["events"])
    assert app.get(project.id).model_dump(mode="json") == before


@pytest.mark.parametrize("field,value", [("command_id", "different"), ("project_id", "different"),
    ("run_id", "different"), ("execution_attempt_id", "different"), ("zone_id", "east"),
    ("geometry_version", 999), ("geometry_digest", "0" * 64)])
def test_ack_cannot_change_command_identity(persistent_runtime, monkeypatch, field, value):
    runtime = persistent_runtime()
    _app, _project, _store, client = runtime
    path, command, waiting = ready_control(runtime, monkeypatch)
    payload = {**acknowledgement(command), field: value}
    assert client.post(path + "/control-result", json=payload).status_code == 409
    assert client.get(path).json()["revision"] == waiting["revision"]


@pytest.mark.parametrize("entry", ["get", "proof", "ack"])
@pytest.mark.parametrize("change", ["geometry_version", "contour", "source_label"])
def test_stale_geometry_or_source_invalidates_control_without_success(persistent_runtime, monkeypatch, entry, change):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    path, command, _waiting = ready_control(runtime, monkeypatch, "Покажи участок «West» на карте.")
    current = app.get(project.id)
    if change == "geometry_version":
        current.geometry_version += 1
    elif change == "contour":
        current.planting_zones[0].geometry["coordinates"][0][1][0] += 0.125
    else:
        current.planting_zones[0].label = "Renamed"
    app.repository.save(current)
    before = app.get(project.id).model_dump(mode="json")
    if entry == "proof":
        assert client.get(path + "/control-command").status_code == 409
    elif entry == "ack":
        response = client.post(path + "/control-result", json=acknowledgement(command))
        assert response.status_code == 200, response.text
        assert response.json()["state"]["status"] == "failed"
    failed = client.get(path).json()
    assert failed["state"]["status"] == "failed"
    assert failed["state"]["failure"]["code"] == "CONTROL_STALE"
    assert not any(event["kind"] == "control_completed" for event in failed["events"])
    assert app.get(project.id).model_dump(mode="json") == before


def test_plan_only_revision_does_not_invalidate_same_geometry_control(persistent_runtime, monkeypatch):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    path, command, _waiting = ready_control(runtime, monkeypatch)
    current = app.get(project.id)
    current.plan.objects[0].locked = not current.plan.objects[0].locked
    current.plan.version += 1
    app.repository.save(current)
    before = app.get(project.id).model_dump(mode="json")
    assert client.get(path + "/control-command").status_code == 200
    assert client.post(path + "/control-result", json=acknowledgement(command)).json()["state"]["status"] == "finished"
    assert app.get(project.id).model_dump(mode="json") == before


def test_cancel_resume_new_attempt_rejects_late_ack_and_never_replays_old_command(persistent_runtime, monkeypatch):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    path, command, _waiting = ready_control(runtime, monkeypatch)
    before = app.get(project.id).model_dump(mode="json")
    cancelled = client.post(path + "/cancel").json()
    assert cancelled["state"]["status"] == "cancelled"
    assert client.post(path + "/control-result", json=acknowledgement(command)).json()["revision"] == cancelled["revision"]
    queued = client.post(path + "/resume").json()
    assert queued["state"]["status"] == "queued"
    assert queued["state"]["control_command"] is None and queued["state"]["control_result"] is None
    assert client.post(path + "/control-result", json=acknowledgement(command)).status_code == 409
    client.post(path + "/run")
    new_command = client.get(path).json()["state"]["control_command"]
    assert new_command["id"] != command["id"] and new_command["execution_attempt_id"] != command["execution_attempt_id"]
    assert client.post(path + "/control-result", json=acknowledgement(new_command)).json()["state"]["status"] == "finished"
    assert client.post(path + "/control-result", json=acknowledgement(command)).status_code == 409
    assert app.get(project.id).model_dump(mode="json") == before


@pytest.mark.parametrize("status,code", [("failed", "MAP_NOT_READY"), ("failed", "MAP_UNAVAILABLE"),
    ("cancelled", "FOCUS_INTERRUPTED"), ("unknown", "CONTROL_OUTCOME_UNKNOWN")])
def test_ui_failure_is_honest_and_unknown_outcome_cannot_replay(persistent_runtime, monkeypatch, status, code):
    runtime = persistent_runtime()
    app, project, _store, client = runtime
    path, command, _waiting = ready_control(runtime, monkeypatch)
    before = app.get(project.id).model_dump(mode="json")
    payload = acknowledgement(command, status=status, error_code=code)
    result = client.post(path + "/control-result", json=payload)
    assert result.status_code == 200, result.text
    failed = result.json()
    assert failed["state"]["status"] == "failed" and failed["state"]["failure"]["code"] == code
    assert failed["state"]["failure"]["retryable"] is (status != "unknown")
    assert client.post(path + "/control-result", json=payload).json()["revision"] == failed["revision"]
    assert client.post(path + "/control-result", json=acknowledgement(command)).status_code == 409
    if status == "unknown":
        for endpoint in ("run", "cancel", "resume", "answer"):
            response = client.post(path + "/" + endpoint, **({"json": {"text": "Участок east"}} if endpoint == "answer" else {}))
            assert response.status_code == 200, response.text
            assert response.json()["revision"] == failed["revision"]
    else:
        resumed = client.post(path + "/resume").json()
        assert resumed["state"]["status"] == "queued" and resumed["state"]["control_command"] is None
        client.post(path + "/run")
        assert client.get(path).json()["state"]["control_command"]["id"] != command["id"]
    assert app.get(project.id).model_dump(mode="json") == before


@pytest.mark.parametrize("lost_response", [False, True])
def test_command_and_ack_survive_process_restart_without_second_dispatch(persistent_runtime, monkeypatch, lost_response):
    runtime = persistent_runtime()
    app, project, store, client = runtime
    path, command, waiting = ready_control(runtime, monkeypatch)
    if lost_response:
        checkpoint = store.checkpoint
        def persist_then_disconnect(*args, **kwargs):
            saved = checkpoint(*args, **kwargs)
            if kwargs.get("kind") == "control_completed":
                raise OSError("Connection lost after durable UI acknowledgment")
            return saved
        monkeypatch.setattr(store, "checkpoint", persist_then_disconnect)
        with pytest.raises(OSError):
            client.post(path + "/control-result", json=acknowledgement(command))
    before = app.get(project.id).model_dump(mode="json")
    store.close()
    app.repository._connection.close()
    app, _current, store, client = persistent_runtime()
    restored = client.get(path).json()
    if not lost_response:
        assert restored["state"]["status"] == "waiting_ui" and restored["revision"] == waiting["revision"]
        assert client.get(path + "/control-command").json()["command"] == command
    finished = client.post(path + "/control-result", json=acknowledgement(command)).json()
    assert finished["state"]["status"] == "finished"
    assert sum(event["kind"] == "control_issued" for event in finished["events"]) == 1
    assert sum(event["kind"] == "control_completed" for event in finished["events"]) == 1
    assert app.get(project.id).model_dump(mode="json") == before


def test_cancel_wins_before_command_checkpoint(persistent_runtime, monkeypatch):
    runtime = persistent_runtime()
    app, project, store, client = runtime
    path = create_control(runtime, monkeypatch)
    checkpoint = store.checkpoint
    def cancel_before_publish(*args, **kwargs):
        if kwargs.get("kind") == "control_issued":
            assert client.post(path + "/cancel").json()["state"]["status"] == "cancelled"
        return checkpoint(*args, **kwargs)
    monkeypatch.setattr(store, "checkpoint", cancel_before_publish)
    before = app.get(project.id).model_dump(mode="json")
    client.post(path + "/run")
    result = client.get(path).json()
    assert result["state"]["status"] == "cancelled" and result["state"]["control_command"] is None
    assert not any(event["kind"] == "control_issued" for event in result["events"])
    assert app.get(project.id).model_dump(mode="json") == before


def test_concurrent_duplicate_acks_share_one_checkpoint(persistent_runtime, monkeypatch):
    runtime = persistent_runtime()
    _app, _project, _store, client = runtime
    path, command, waiting = ready_control(runtime, monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: client.post(path + "/control-result", json=acknowledgement(command)), range(2)))
    assert all(result.status_code == 200 for result in results)
    assert {result.json()["revision"] for result in results} == {waiting["revision"] + 1}
