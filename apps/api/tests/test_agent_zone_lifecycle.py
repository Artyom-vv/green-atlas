"""Zone proposals use the real runtime and saved-cache HTTP approval boundary."""
from app.composition import get_runtime
from contextlib import closing
from datetime import timedelta
import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import api, planning_assistant as local
from app.agent_interpreter import project_context
from app.agent_runtime import routes, preview_routes
from app.agent_runtime.contracts import AgentIntent, Goal
from app.agent_runtime.engine import AgentEngine, _summarize_data
from app.agent_runtime.gateway import ToolGateway
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.store import AgentRunStore
from app.agent_runtime.zone_contracts import ZoneIntent
from app.agent_runtime.zone_workflow import bind_zone_intent
from app.planting_zone_changes import get_zone_change_service
from test_planting_zone_changes import seeded
from test_zone_workflow import project_with_contour


def install_draft(monkeypatch, zone):
    draft = {"operation": "zones", "scope_mode": "explicit", "zone": zone.model_dump(mode="json")}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    monkeypatch.setattr(local, "configured_model", lambda: "test")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    app, project, reference = project_with_contour()
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        monkeypatch.setattr(get_runtime(), "application", app)
        monkeypatch.setattr(routes, "_store", lambda: store)
        monkeypatch.setattr(preview_routes, "_store", lambda: store)
        http = FastAPI()
        http.include_router(routes.router)
        http.include_router(preview_routes.router)
        yield app, project, reference, store, TestClient(http)


def create(runtime, monkeypatch, text, zone):
    app, project, _ref, store, client = runtime
    install_draft(monkeypatch, zone)
    response = client.post(f"/api/projects/{project.id}/agent-runs", json={"text": text})
    assert response.status_code == 201, response.text
    return store.get(project.id, response.json()["state"]["run_id"])


def execute(runtime, record):
    app, project, _ref, store, _client = runtime
    def no_planner(_context):
        raise AssertionError("Bound zone actions must not require a second model decision")
    return AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id, no_planner)


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_http_zone_preview_exact_commit_and_idempotence(runtime, monkeypatch, operation):
    app, project, reference, store, client = runtime
    zone = ZoneIntent(operation=operation, **({"geometry_reference": reference, "label": "Сад"} if operation == "create"
        else {"target_zone_id": "east", **({"label": "Сад"} if operation == "update" else {})}))
    text = {"create": "Создай участок «Сад» из контура source-area.", "update": "Переименуй участок east в «Сад».",
            "delete": "Удали участок east."}[operation]
    before = app.get(project.id).model_dump(mode="json")
    record = create(runtime, monkeypatch, text, zone)
    assert record.state.intent.zone is not None
    assert record.state.intent.scope_mode == ("project" if operation == "create" else "explicit")
    assert (record.state.resolved_scope is None) == (operation == "create")
    if operation != "create":
        assert record.state.resolved_scope.zone_ids == ["east"]
    ready = execute(runtime, record)
    assert ready.state.status == "waiting_approval", ready.model_dump_json()
    pending = ready.state.pending_approval
    assert pending["kind"] == "planting_zones"
    path = f"/api/projects/{project.id}/agent-runs/{record.state.run_id}"
    full = client.get(path + "/zone-preview", params={"preview_ref": pending["preview_ref"]})
    assert full.status_code == 200, full.text
    preview = full.json()
    assert preview["operation"] == operation and preview["base_state_version"] == project.state_version
    assert preview["can_apply"] and not preview["affected_planting_ids"]
    assert client.get(path + "/preview", params={"preview_ref": pending["preview_ref"]}).status_code == 409
    assert app.get(project.id).model_dump(mode="json") == before
    approved = client.post(path + "/approve", json={"preview_ref": pending["preview_ref"]})
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["state"]["status"] == "finished", result
    receipt = result["events"][-1]["payload"]
    assert receipt["kind"] == "planting_zones" and receipt["plantings_unchanged"] is True
    assert receipt["target_zone_id"] == preview["target_zone_id"]
    evidence = next(event["payload"] for event in result["events"]
                    if event["kind"] == "tool_result" and event["payload"]["call_id"] == receipt["preview_ref"])
    summary = evidence["data"]
    assert evidence["verification"]["status"] == "verified"
    assert summary["id"] == receipt["preview_id"] and summary["digest"] == receipt["digest"]
    for side in ("before", "after"):
        target = next((zone for zone in preview[f"{side}_zones"] if zone["id"] == receipt["target_zone_id"]), None)
        assert summary[f"target_{side}"] == ({"id": target["id"], "label": target["label"]} if target else None)
    assert summary["geometry_changed"] is False
    assert "coordinates" not in json.dumps(summary) and "before_zones" not in summary and "after_zones" not in summary
    assert _summarize_data("prepare_zone_change", summary) == summary
    saved = app.get(project.id)
    assert saved.state_version == project.state_version + 1
    assert saved.plan.objects == project.plan.objects and saved.plan.version == project.plan.version
    assert [z.model_dump(mode="json") for z in saved.planting_zones] == preview["after_zones"]
    assert client.post(path + "/approve", json={"preview_ref": pending["preview_ref"]}).json()["revision"] == result["revision"]
    assert client.post(path + "/approve", json={"preview_ref": "another"}).status_code == 409
    assert client.get(path + "/zone-preview", params={"preview_ref": pending["preview_ref"]}).status_code == 409


def test_blocked_zone_delete_preserves_plantings_and_asks_once(runtime, monkeypatch):
    app, project, _ref, _store, _client = runtime
    before = app.get(project.id).model_dump(mode="json")
    record = create(runtime, monkeypatch, "Удали участок west.", ZoneIntent(operation="delete", target_zone_id="west"))
    result = execute(runtime, record)
    assert result.state.status == "waiting_question" and result.state.pending_approval is None
    assert result.state.pending_question["code"] == "ZONE_CHANGE_BLOCKED"
    assert result.state.last_result.data["affected_planting_count"] == 1
    assert len(result.state.tool_calls) == 1
    assert app.get(project.id).model_dump(mode="json") == before


@pytest.mark.parametrize("text,zone", [
    ("Удали участок east.", ZoneIntent(operation="delete", target_zone_id="west")),
    ("Удали участок missing.", ZoneIntent(operation="delete", target_zone_id="missing")),
    ("Удали часть участка east.", ZoneIntent(operation="delete", target_zone_id="east")),
    ("Удали выделенный участок.", ZoneIntent(operation="delete", target_zone_id="east")),
    ("Переименуй участок east в «Сад» и расширь на 3 метра.", ZoneIntent(operation="update", target_zone_id="east", label="Сад")),
])
def test_unsupported_zone_source_becomes_question_without_any_tool(runtime, monkeypatch, text, zone):
    app, project, _ref, _store, _client = runtime
    before = app.get(project.id).model_dump(mode="json")
    record = create(runtime, monkeypatch, text, zone)
    assert record.state.intent.zone is None and record.state.intent.unresolved_requirements
    result = execute(runtime, record)
    assert result.state.status == "waiting_question"
    assert result.state.pending_question["code"] == "ZONE_INTENT_UNRESOLVED"
    assert not result.state.tool_calls and result.state.pending_approval is None
    assert app.get(project.id).model_dump(mode="json") == before


def test_cancel_during_zone_preview_discards_late_approval(runtime, monkeypatch):
    app, project, _ref, store, _client = runtime
    record = create(runtime, monkeypatch, "Удали участок east.", ZoneIntent(operation="delete", target_zone_id="east"))
    gateway = ToolGateway(app)
    original = gateway.call
    def cancelling(context, call):
        result = original(context, call)
        routes.cancel_run(project.id, record.state.run_id)
        return result
    monkeypatch.setattr(gateway, "call", cancelling)
    result = AgentEngine(store, gateway).run(record.state.run_id, project.id, lambda _: None)
    assert result.state.status == "cancelled" and result.state.pending_approval is None
    assert app.get(project.id).state_version == project.state_version


def test_stale_preview_get_and_commit_fail_then_explicit_resume_rebinds_same_target(runtime, monkeypatch):
    app, project, _ref, store, client = runtime
    record = create(runtime, monkeypatch, "Переименуй участок east в «Сад».", ZoneIntent(operation="update", target_zone_id="east", label="Сад"))
    ready = execute(runtime, record)
    old_ref = ready.state.pending_approval["preview_ref"]
    path = f"/api/projects/{project.id}/agent-runs/{record.state.run_id}"
    modified = app.get(project.id)
    modified.name = "Обновлённый проект"
    app.repository.save(modified)
    current = app.get(project.id)
    assert current.state_version != project.state_version
    assert client.get(path + "/zone-preview", params={"preview_ref": old_ref}).status_code == 409
    failed = client.post(path + "/approve", json={"preview_ref": old_ref})
    assert failed.json()["state"]["status"] == "failed"
    assert app.get(project.id).model_dump(mode="json") == current.model_dump(mode="json")
    restarted = routes.resume_run(project.id, record.state.run_id)
    assert restarted.state.intent.zone.draft.zone_id == "east"
    assert restarted.state.intent.zone.draft.base_state_version == current.state_version
    assert restarted.state.pending_approval is None and restarted.state.last_result is None
    ready = execute(runtime, restarted)
    assert ready.state.status == "waiting_approval" and ready.state.pending_approval["preview_ref"] != old_ref
    assert client.get(path + "/zone-preview", params={"preview_ref": old_ref}).status_code == 409
    assert client.post(path + "/approve", json={"preview_ref": old_ref}).status_code == 409


def test_stale_before_prepare_is_one_honest_question(runtime, monkeypatch):
    app, project, _ref, _store, _client = runtime
    record = create(runtime, monkeypatch, "Удали участок east.", ZoneIntent(operation="delete", target_zone_id="east"))
    changed = app.get(project.id)
    changed.name = "Новая версия"
    app.repository.save(changed)
    result = execute(runtime, record)
    assert result.state.status == "waiting_question" and len(result.state.tool_calls) == 1
    assert result.state.pending_question["code"] == "STALE_PROJECT"
    assert result.state.intent.zone.draft.base_state_version == project.state_version


def test_answer_amends_blocked_zone_scope_while_preserving_source_history(runtime, monkeypatch):
    app, project, _ref, _store, _client = runtime
    record = create(runtime, monkeypatch, "Удали участок west.", ZoneIntent(operation="delete", target_zone_id="west"))
    waiting = execute(runtime, record)
    install_draft(monkeypatch, ZoneIntent(operation="delete", target_zone_id="east"))
    answered = routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text="Удали участок east."))
    assert answered.state.intent.raw_text == answered.state.intent.zone.source_text == "Удали участок west.\n\nУдали участок east."
    assert answered.state.intent.source_turns == ["Удали участок west.", "Удали участок east."]
    assert answered.state.intent.explicit_zone_ids == ["east"]
    assert answered.state.last_result is None and answered.state.pending_approval is None
    ready = execute(runtime, answered)
    assert ready.state.status == "waiting_approval"
    assert ready.state.last_result.data["target_zone_id"] == "east"
    assert app.get(project.id).state_version == project.state_version


def test_resume_cannot_retarget_a_deleted_zone_or_changed_contour(runtime, monkeypatch):
    app, project, reference, _store, _client = runtime
    record = create(runtime, monkeypatch, "Создай участок из контура source-area.", ZoneIntent(operation="create", geometry_reference=reference))
    routes.cancel_run(project.id, record.state.run_id)
    changed = app.get(project.id)
    feature = next(item for item in changed.geometry.feature_collection["features"] if item.get("id") == "source-area")
    feature["geometry"] = json.loads(json.dumps(feature["geometry"]))
    feature["geometry"]["coordinates"][0][0][0] += 1
    app.repository.save(changed)
    with pytest.raises(HTTPException) as error:
        routes.resume_run(project.id, record.state.run_id)
    assert error.value.status_code == 409


@pytest.mark.parametrize("field,value", [("target_zone_id", "west"), ("can_apply", False), ("after_area_m2", 0),
    ("target_before", {"id": "east", "label": "Неподтверждённое название"}),
    ("target_after", {"id": "east", "label": "Несуществующий участок"}), ("geometry_changed", True)])
def test_saved_summary_tampering_is_rejected_on_get_and_approval(runtime, monkeypatch, field, value):
    app, project, _ref, store, client = runtime
    record = create(runtime, monkeypatch, "Удали участок east.", ZoneIntent(operation="delete", target_zone_id="east"))
    ready = execute(runtime, record)
    event = next(item for item in ready.events if item.kind == "tool_result")
    event.payload["data"][field] = value
    with store.connection:
        store.connection.execute("UPDATE agent_run_events SET payload=? WHERE run_id=? AND sequence=?",
            (json.dumps(event.payload), record.state.run_id, event.sequence))
    path = f"/api/projects/{project.id}/agent-runs/{record.state.run_id}"
    ref = ready.state.pending_approval["preview_ref"]
    assert client.get(path + "/zone-preview", params={"preview_ref": ref}).status_code == 409
    assert client.post(path + "/approve", json={"preview_ref": ref}).status_code == 409
    assert app.get(project.id).state_version == project.state_version


def test_expired_full_cache_never_commits_saved_compact_success(runtime, monkeypatch):
    app, project, _ref, _store, client = runtime
    record = create(runtime, monkeypatch, "Удали участок east.", ZoneIntent(operation="delete", target_zone_id="east"))
    ready = execute(runtime, record)
    service = get_zone_change_service(app)
    now = service._clock()
    monkeypatch.setattr(service, "_clock", lambda: now + timedelta(hours=1))
    path = f"/api/projects/{project.id}/agent-runs/{record.state.run_id}"
    ref = ready.state.pending_approval["preview_ref"]
    assert client.get(path + "/zone-preview", params={"preview_ref": ref}).status_code == 409
    assert client.post(path + "/approve", json={"preview_ref": ref}).json()["state"]["status"] == "failed"
    assert app.get(project.id).state_version == project.state_version


def test_exact_geometry_update_has_full_before_after_and_preserves_other_zones(runtime, monkeypatch):
    app, project, reference, _store, client = runtime
    record = create(runtime, monkeypatch, "Измени контур участка east по контуру source-area.",
        ZoneIntent(operation="update", target_zone_id="east", geometry_reference=reference))
    ready = execute(runtime, record)
    assert ready.state.status == "waiting_approval"
    path = f"/api/projects/{project.id}/agent-runs/{record.state.run_id}"
    ref = ready.state.pending_approval["preview_ref"]
    preview = client.get(path + "/zone-preview", params={"preview_ref": ref}).json()
    assert preview["before_zones"][0] == preview["after_zones"][0]
    assert preview["before_zones"][1]["geometry"] != preview["after_zones"][1]["geometry"]
    assert preview["after_zones"][1]["geometry"] == record.state.intent.zone.draft.geometry
    assert client.post(path + "/approve", json={"preview_ref": ref}).json()["state"]["status"] == "finished"
    assert app.get(project.id).plan.objects == project.plan.objects


def test_zone_create_works_before_a_planting_plan_exists(runtime, monkeypatch):
    app, project, reference, store, client = runtime
    changed = app.get(project.id)
    changed.plan = None
    app.repository.save(changed)
    project = app.get(project.id)
    from app.agent_runtime.zone_workflow import zone_geometry_references
    reference = type(reference).model_validate(next(item["reference"] for item in zone_geometry_references(project)
        if item["reference"]["feature_id"] == "source-area"))
    runtime = app, project, reference, store, client
    record = create(runtime, monkeypatch, "Создай участок из контура source-area.", ZoneIntent(operation="create", geometry_reference=reference))
    ready = execute(runtime, record)
    assert ready.state.status == "waiting_approval" and ready.state.plan_version is None
    result = routes.approve_run(project.id, record.state.run_id, routes.AgentRunApproval(preview_ref=ready.state.pending_approval["preview_ref"]))
    assert result.state.status == "finished" and app.get(project.id).plan is None


def test_replayed_preview_event_from_another_run_cannot_authorize_zone(runtime, monkeypatch):
    app, project, _ref, store, client = runtime
    zone = ZoneIntent(operation="delete", target_zone_id="east")
    first = execute(runtime, create(runtime, monkeypatch, "Удали участок east.", zone))
    second = create(runtime, monkeypatch, "Удали участок east.", zone)
    event = next(item for item in first.events if item.kind == "tool_result")
    stolen = store.checkpoint(project.id, second.state.run_id, expected_revision=second.revision,
        state=second.state.model_copy(update={"status": "waiting_approval", "pending_approval": first.state.pending_approval}),
        kind="tool_result", payload=event.payload)
    path = f"/api/projects/{project.id}/agent-runs/{stolen.state.run_id}"
    ref = first.state.pending_approval["preview_ref"]
    assert client.get(path + "/zone-preview", params={"preview_ref": ref}).status_code == 409
    assert client.post(path + "/approve", json={"preview_ref": ref}).status_code == 409
    assert app.get(project.id).state_version == project.state_version


def test_cached_extra_effects_with_recomputed_digest_cannot_pass_get_or_commit(runtime, monkeypatch):
    from app.planting_zone_changes import ZoneChangePreview, _preview_digest
    app, project, _ref, store, client = runtime
    ready = execute(runtime, create(runtime, monkeypatch, "Удали участок east.", ZoneIntent(operation="delete", target_zone_id="east")))
    event = next(item for item in ready.events if item.kind == "tool_result")
    service = get_zone_change_service(app)
    preview = ZoneChangePreview.model_validate_json(service._previews[event.payload["data"]["id"]])
    forged = preview.model_copy(update={"after_zones": (preview.after_zones[0].model_copy(update={"label": "Непрошенное название"}),)})
    forged = forged.model_copy(update={"digest": _preview_digest(forged)})
    service._previews[forged.id] = forged.model_dump_json()
    event.payload["data"]["digest"] = forged.digest
    with store.connection:
        store.connection.execute("UPDATE agent_run_events SET payload=? WHERE run_id=? AND sequence=?",
            (json.dumps(event.payload), ready.state.run_id, event.sequence))
    path = f"/api/projects/{project.id}/agent-runs/{ready.state.run_id}"
    ref = ready.state.pending_approval["preview_ref"]
    assert client.get(path + "/zone-preview", params={"preview_ref": ref}).status_code == 409
    assert client.post(path + "/approve", json={"preview_ref": ref}).status_code == 409
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("kind", [None, "plantings"])
def test_zone_proposal_cannot_use_legacy_or_planting_approval_kind(runtime, monkeypatch, kind):
    app, project, _ref, store, client = runtime
    ready = execute(runtime, create(runtime, monkeypatch, "Удали участок east.", ZoneIntent(operation="delete", target_zone_id="east")))
    pending = {key: value for key, value in ready.state.pending_approval.items() if key != "kind"}
    if kind is not None:
        pending["kind"] = kind
    store.checkpoint(project.id, ready.state.run_id, expected_revision=ready.revision,
        state=ready.state.model_copy(update={"pending_approval": pending}), kind="test_tamper", payload={})
    path = f"/api/projects/{project.id}/agent-runs/{ready.state.run_id}"
    ref = pending["preview_ref"]
    assert client.get(path + "/zone-preview", params={"preview_ref": ref}).status_code == 409
    assert client.get(path + "/preview", params={"preview_ref": ref}).status_code == 409
    assert client.post(path + "/approve", json={"preview_ref": ref}).status_code == 409
    assert app.get(project.id).state_version == project.state_version


def test_selected_zone_mutation_is_honest_unsupported_even_with_valid_snapshot(runtime, monkeypatch):
    app, project, _ref, store, client = runtime
    install_draft(monkeypatch, ZoneIntent(operation="update", target_zone_id="east", label="Сад"))
    response = client.post(f"/api/projects/{project.id}/agent-runs", json={
        "text": "Переименуй выделенный участок в «Сад».", "selection_context": {
            "project_id": project.id, "state_version": project.state_version,
            "plan_version": project.plan.version, "zone_ids": ["east"], "object_ids": []}})
    assert response.status_code == 201, response.text
    record = store.get(project.id, response.json()["state"]["run_id"])
    assert record.state.resolved_scope is None
    assert record.state.intent.selection_binding is None and record.state.intent.zone is None
    result = execute(runtime, record)
    assert result.state.status == "waiting_question" and result.state.pending_question["slot"] == "zone"
    assert result.state.pending_approval is None and not result.state.tool_calls


def test_model_context_gets_source_geometry_references_and_never_full_polygons(runtime, monkeypatch):
    app, project, reference, store, _client = runtime
    captured = {}
    zone = ZoneIntent(operation="create", geometry_reference=reference)
    def model_call(_endpoint, payload, **_kwargs):
        captured.update(payload)
        return {"message": {"content": json.dumps({"operation": "zones", "scope_mode": "project", "zone": zone.model_dump(mode="json")})}}
    monkeypatch.setattr(local, "local_json", model_call)
    intent = IntentCompiler("test").compile("Создай участок из контура source-area.", project_context=project_context(project), full_project=project)
    user_context = json.loads(captured["messages"][1]["content"])
    assert "coordinates" not in json.dumps(user_context)
    assert user_context["project"]["zone_geometry_references"] == [{"reference": reference.model_dump(mode="json"),
        "label": "Точный контур", "kind": "allowed"}]
    assert intent.zone.draft.geometry["coordinates"]
    record = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
    context = AgentEngine(store, ToolGateway(app))._context(record)
    assert "draft" not in context["run"]["intent"]["zone"]
    assert "coordinates" not in json.dumps(context)


def test_runtime_contract_import_does_not_initialize_services():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c",
        "import sys; from app.agent_runtime.contracts import AgentIntent; "
        "assert 'shapely' not in sys.modules; assert 'sqlite3' not in sys.modules; "
        "assert 'app.agent_runtime.engine' not in sys.modules; "
        "from app.agent_runtime import AgentEngine; assert AgentEngine.__name__ == 'AgentEngine'"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("separator", [" ", "\n\n"])
def test_user_turn_marker_on_create_cannot_erase_unsupported_zone_condition(runtime, monkeypatch, separator):
    source = "Удали участок east только при разрешении архитектора." + separator + "Дополнение пользователя: Удали участок east."
    record = create(runtime, monkeypatch, source, ZoneIntent(operation="delete", target_zone_id="east"))
    assert record.state.intent.raw_text == source
    assert record.state.intent.zone is None and record.state.intent.unresolved_requirements
    result = execute(runtime, record)
    assert result.state.status == "waiting_question" and result.state.pending_approval is None
    assert not result.state.tool_calls


def test_user_marker_inside_actual_answer_cannot_erase_zone_conditions(runtime, monkeypatch):
    _app, project, _ref, _store, _client = runtime
    record = create(runtime, monkeypatch, "Удали участок west.", ZoneIntent(operation="delete", target_zone_id="west"))
    execute(runtime, record)
    install_draft(monkeypatch, ZoneIntent(operation="delete", target_zone_id="east"))
    text = "Удали участок east только при разрешении архитектора. Дополнение пользователя: Удали участок east."
    answered = routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text=text))
    assert answered.state.intent.raw_text == "Удали участок west.\n\n" + text
    assert answered.state.intent.source_turns == ["Удали участок west.", text]
    assert answered.state.intent.zone is None and answered.state.intent.unresolved_requirements
    result = execute(runtime, answered)
    assert result.state.status == "waiting_question" and result.state.pending_approval is None


def fail_zone_completion_once(monkeypatch, store):
    checkpoint = store.checkpoint
    failed = False

    def save(*args, **kwargs):
        nonlocal failed
        if kwargs.get("kind") == "commit_applied" and not failed:
            failed = True
            raise OSError("Lost connection before completion checkpoint")
        return checkpoint(*args, **kwargs)

    monkeypatch.setattr(store, "checkpoint", save)


def recover_request(client, path, endpoint, preview_ref):
    if endpoint == "get":
        return client.get(path)
    body = ({"preview_ref": preview_ref} if endpoint == "approve" else
            {"text": "Создай участок ещё раз."} if endpoint == "answer" else None)
    return client.post(path + "/" + endpoint, json=body)


@pytest.mark.parametrize("endpoint", ["get", "approve", "resume", "cancel", "answer", "run"])
def test_lost_zone_completion_recovers_receipt_without_reapplying(runtime, monkeypatch, endpoint):
    app, project, reference, store, client = runtime
    ready = execute(runtime, create(runtime, monkeypatch, "Создай участок «Сад» из контура source-area.",
        ZoneIntent(operation="create", geometry_reference=reference, label="Сад")))
    path = f"/api/projects/{project.id}/agent-runs/{ready.state.run_id}"
    preview_ref = ready.state.pending_approval["preview_ref"]
    fail_zone_completion_once(monkeypatch, store)
    with pytest.raises(OSError):
        client.post(path + "/approve", json={"preview_ref": preview_ref})
    committed = app.get(project.id)
    assert committed.state_version == project.state_version + 1
    assert len(committed.planting_zones) == len(project.planting_zones) + 1
    interrupted = store.get(project.id, ready.state.run_id)
    assert interrupted.events[-1].kind == "commit_started"
    assert interrupted.state.status == "waiting_approval"
    # Recovery must use the historical applied receipt, not a current preview.
    changed = app.get(project.id)
    changed.name = "Later project revision"
    app.repository.save(changed)
    service = get_zone_change_service(app)
    now = service._clock()
    monkeypatch.setattr(service, "_clock", lambda: now + timedelta(hours=1))
    before_recovery = app.get(project.id).model_dump(mode="json")
    response = recover_request(client, path, endpoint, preview_ref)
    assert response.status_code == 200, response.text
    recovered = response.json()
    assert recovered["state"]["status"] == "finished"
    assert recovered["state"]["snapshot_version"] == committed.state_version
    assert recovered["state"]["pending_approval"] is None
    assert sum(event["kind"] == "commit_applied" for event in recovered["events"]) == 1
    assert client.get(path).json()["revision"] == recovered["revision"]
    assert client.post(path + "/approve", json={"preview_ref": preview_ref}).json()["revision"] == recovered["revision"]
    assert app.get(project.id).model_dump(mode="json") == before_recovery


@pytest.mark.parametrize("endpoint", ["get", "approve", "resume", "cancel", "answer", "run"])
def test_lost_receipt_leaves_unknown_outcome_and_never_reapplies(runtime, monkeypatch, endpoint):
    app, project, reference, store, client = runtime
    ready = execute(runtime, create(runtime, monkeypatch, "Создай участок «Сад» из контура source-area.",
        ZoneIntent(operation="create", geometry_reference=reference, label="Сад")))
    path = f"/api/projects/{project.id}/agent-runs/{ready.state.run_id}"
    preview_ref = ready.state.pending_approval["preview_ref"]
    fail_zone_completion_once(monkeypatch, store)
    with pytest.raises(OSError):
        client.post(path + "/approve", json={"preview_ref": preview_ref})
    committed = app.get(project.id).model_dump(mode="json")
    # Cache loss alone now recovers from the repository. Simulate unavailable
    # authoritative receipt storage to exercise the conservative barrier.
    service = get_zone_change_service(app)
    service._applied.clear()
    service._previews.clear()
    monkeypatch.setattr(app, "get_zone_change_receipt", lambda *_args: None)
    response = recover_request(client, path, endpoint, preview_ref)
    assert response.status_code == 200, response.text
    unknown = response.json()
    assert unknown["state"]["status"] == "failed"
    assert unknown["state"]["failure"]["code"] == "APPROVAL_OUTCOME_UNKNOWN"
    assert unknown["state"]["failure"]["retryable"] is False
    assert unknown["state"]["pending_approval"] is None
    assert not any(event["kind"] == "commit_applied" for event in unknown["events"])
    for action in ["get", "approve", "resume", "cancel", "answer", "run"]:
        repeated = recover_request(client, path, action, preview_ref)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["revision"] == unknown["revision"]
        assert repeated.json()["state"]["failure"]["code"] == "APPROVAL_OUTCOME_UNKNOWN"
    assert app.get(project.id).model_dump(mode="json") == committed


def test_zone_commit_does_not_write_if_attempt_checkpoint_fails(runtime, monkeypatch):
    app, project, reference, store, client = runtime
    ready = execute(runtime, create(runtime, monkeypatch, "Создай участок из контура source-area.",
        ZoneIntent(operation="create", geometry_reference=reference)))
    before = app.get(project.id).model_dump(mode="json")
    checkpoint = store.checkpoint

    def fail_started(*args, **kwargs):
        if kwargs.get("kind") == "commit_started":
            raise OSError("Attempt was not checkpointed")
        return checkpoint(*args, **kwargs)

    monkeypatch.setattr(store, "checkpoint", fail_started)
    path = f"/api/projects/{project.id}/agent-runs/{ready.state.run_id}"
    with pytest.raises(OSError):
        client.post(path + "/approve", json={"preview_ref": ready.state.pending_approval["preview_ref"]})
    assert app.get(project.id).model_dump(mode="json") == before
    assert store.get(project.id, ready.state.run_id).revision == ready.revision


def test_unknown_attempt_can_later_recover_exact_receipt(runtime, monkeypatch):
    app, project, reference, store, client = runtime
    ready = execute(runtime, create(runtime, monkeypatch, "Создай участок из контура source-area.",
        ZoneIntent(operation="create", geometry_reference=reference)))
    path = f"/api/projects/{project.id}/agent-runs/{ready.state.run_id}"
    fail_zone_completion_once(monkeypatch, store)
    with pytest.raises(OSError):
        client.post(path + "/approve", json={"preview_ref": ready.state.pending_approval["preview_ref"]})
    service = get_zone_change_service(app)
    lookup = app.get_zone_change_receipt
    service._applied.clear()
    monkeypatch.setattr(app, "get_zone_change_receipt", lambda *_args: None)
    assert client.get(path).json()["state"]["failure"]["code"] == "APPROVAL_OUTCOME_UNKNOWN"
    monkeypatch.setattr(app, "get_zone_change_receipt", lookup)
    recovered = client.get(path).json()
    assert recovered["state"]["status"] == "finished"
    assert sum(event["kind"] == "commit_applied" for event in recovered["events"]) == 1
    assert app.get(project.id).state_version == project.state_version + 1
