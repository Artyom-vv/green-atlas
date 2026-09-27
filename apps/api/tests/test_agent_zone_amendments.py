"""Real HTTP source turns fill typed zone slots without dropping conditions."""
import json

import pytest

from app import planning_assistant as local
from app.agent_runtime import routes
from app.agent_runtime.zone_sources import resolve_zone_sources
from app.agent_runtime.zone_workflow import bind_zone_intent, prepare_zone_change
from test_agent_zone_lifecycle import runtime


def source_model(monkeypatch, operation):
    # Deliberately omit source slots: complete source facts, not model guesses,
    # determine whether an action can be bound and prepared.
    draft = {"operation": "zones", "scope_mode": "explicit", "zone": {"operation": operation}}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    monkeypatch.setattr(local, "configured_model", lambda: "test")


def dispatcher(monkeypatch, client):
    worker, scheduled = routes._run_in_background, []
    monkeypatch.setattr(routes, "_run_in_background", lambda *args: scheduled.append(args))
    def execute(url):
        response = client.post(url + "/run")
        assert response.status_code == 200, response.text
        assert response.json()["state"]["status"] == "scheduled"
        worker(*scheduled.pop(0))
        return client.get(url).json()
    return execute


@pytest.mark.parametrize("operation,turns,slot_values", [
    ("update", ["Переименуй участок в «Сад».", "Участок east"], {"label": "Сад", "target_zone_id": "east"}),
    ("update", ["Переименуй участок east.", "Название «Сад»"], {"target_zone_id": "east", "label": "Сад"}),
    ("create", ["Создай участок «Сад».", "Контур source-area"], {"label": "Сад", "geometry_reference": "source-area"}),
    ("update", ["Измени контур участка по контуру source-area.", "Участок east"], {"geometry_reference": "source-area", "target_zone_id": "east"}),
    ("delete", ["Удали участок west.", "Участок east"], {"target_zone_id": "east"}),
    ("update", ["Переименуй участок west.", "Участок east", "Название «Сад»"], {"target_zone_id": "east", "label": "Сад"}),
    ("update", ["Измени участок east с названием «Сад» по контуру.", "Контур source-area"], {"target_zone_id": "east", "label": "Сад", "geometry_reference": "source-area"}),
    ("update", ["Переименуй участок east по контуру source-area.", "Название «Сад»"], {"target_zone_id": "east", "label": "Сад", "geometry_reference": "source-area"}),
    ("update", ["Измени участок в «Сад» по контуру.", "Участок east", "Контур source-area"], {"target_zone_id": "east", "label": "Сад", "geometry_reference": "source-area"}),
    ("update", ["Переименуй участок east.", "Контур source-area", "Название «Сад»"], {"target_zone_id": "east", "label": "Сад", "geometry_reference": "source-area"}),
])
def test_http_short_answers_keep_source_slots_through_full_preview_and_commit(runtime, monkeypatch, operation, turns, slot_values):
    app, project, _reference, _store, client = runtime
    source_model(monkeypatch, operation)
    execute = dispatcher(monkeypatch, client)
    before = app.get(project.id).model_dump(mode="json")
    response = client.post(f"/api/projects/{project.id}/agent-runs", json={"text": turns[0]})
    assert response.status_code == 201, response.text
    url = f"/api/projects/{project.id}/agent-runs/{response.json()['state']['run_id']}"
    current = execute(url)
    for answer in turns[1:]:
        assert current["state"]["status"] == "waiting_question", current
        response = client.post(url + "/answer", json={"text": answer})
        assert response.status_code == 200, response.text
        assert response.json()["state"]["status"] == "queued"
        current = execute(url)
    assert current["state"]["status"] == "waiting_approval", current
    intent = current["state"]["intent"]
    assert intent["source_turns"] == intent["zone"]["source_turns"] == turns
    assert intent["raw_text"] == intent["zone"]["source_text"] == "\n\n".join(turns)
    ledger = {item["slot"]: item["value"] for item in intent["zone"]["amendments"]}
    for slot, value in slot_values.items():
        assert (ledger[slot]["feature_id"] if slot == "geometry_reference" else ledger[slot]) == value
    assert app.get(project.id).model_dump(mode="json") == before
    pending = current["state"]["pending_approval"]
    response = client.get(url + "/zone-preview", params={"preview_ref": pending["preview_ref"]})
    assert response.status_code == 200, response.text
    full = response.json()
    assert full["operation"] == operation and full["can_apply"]
    response = client.post(url + "/approve", json={"preview_ref": pending["preview_ref"]})
    assert response.status_code == 200, response.text
    assert response.json()["state"]["status"] == "finished"
    saved = app.get(project.id).model_dump(mode="json")
    assert saved["planting_zones"] == full["after_zones"]
    assert saved["plan"]["objects"] == before["plan"]["objects"]
    assert saved["state_version"] == before["state_version"] + 1
    assert sum(event["kind"] == "commit_applied" for event in response.json()["events"]) == 1


@pytest.mark.parametrize("turns,answer_status", [
    (["Переименуй участок в «Сад» только при разрешении архитектора.", "Участок east"], 200),
    (["Переименуй участок в «Сад».", "Участок east только при разрешении архитектора"], 200),
    (["Переименуй участок в «Сад».", "Участок east и участок west"], 200),
    (["Переименуй участок в «Сад».", "Не выбирай участок east"], 422),
    (["Переименуй участок в «Сад».", "Удали участок east"], 200),
    (["Переименуй участок в «Сад».", "Дополнение пользователя: Участок east"], 200),
    (["Переименуй участок в «Сад» только при разрешении архитектора.", "Переименуй участок east в «Сад»."], 200),
])
def test_answer_cannot_drop_an_earlier_condition_or_authorize_extra_effects(runtime, monkeypatch, turns, answer_status):
    app, project, _reference, _store, client = runtime
    source_model(monkeypatch, "update")
    execute = dispatcher(monkeypatch, client)
    before = app.get(project.id).model_dump(mode="json")
    response = client.post(f"/api/projects/{project.id}/agent-runs", json={"text": turns[0]})
    assert response.status_code == 201, response.text
    url = f"/api/projects/{project.id}/agent-runs/{response.json()['state']['run_id']}"
    original = execute(url)
    assert original["state"]["status"] == "waiting_question"
    response = client.post(url + "/answer", json={"text": turns[1]})
    assert response.status_code == answer_status, response.text
    if answer_status == 422:
        assert client.get(url).json() == original
        assert app.get(project.id).model_dump(mode="json") == before
        return
    result = execute(url)
    assert result["state"]["status"] == "waiting_question"
    assert result["state"]["intent"]["source_turns"] == turns
    assert result["state"]["pending_approval"] is None and result["state"]["tool_calls"] == []
    assert app.get(project.id).model_dump(mode="json") == before


def test_saved_slot_provenance_is_rechecked_before_domain_prepare(runtime):
    app, project, _reference, _store, _client = runtime
    turns = ["Переименуй участок в «Сад».", "Участок east"]
    proposed, _ = resolve_zone_sources(turns, project)
    bound = bind_zone_intent("\n\n".join(turns), project, proposed, source_turns=turns)
    forged = bound.model_copy(update={"amendments": bound.amendments[1:]})
    with pytest.raises(ValueError, match="основания"):
        prepare_zone_change(app, project.id, forged)
