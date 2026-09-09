import pytest
from fastapi.testclient import TestClient

from app.agent_conversations import project_store
from app.agent_memory import ConversationStore
from app.main import app
from app import agent_conversations as routes
from app.agent_memory import TaskPatch
from app.agent_interpreter import TaskInterpretation


@pytest.fixture
def client(tmp_path):
    store = ConversationStore(tmp_path / "conversations.db")
    app.dependency_overrides[project_store] = lambda: store
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(project_store, None)
    store.close()


def test_conversations_separate_projects_and_reject_stale_writes(client):
    base = "/api/projects/project-a/conversations"
    first = client.post(base, json={"title": "Вдоль зданий"}).json()
    second = client.post(base, json={"title": "Сквер"}).json()
    assert first["id"] != second["id"]
    assert len(client.get(base).json()) == 2
    url = f"{base}/{first['id']}/messages"
    request = {"record_id": "user-1", "expected_revision": 1, "text": "100 деревьев"}
    written = client.post(url, json=request)
    assert written.status_code == 200
    assert client.post(url, json=request).json() == written.json()
    assert client.post(url, json={**request, "record_id": "user-2"}).status_code == 409
    assert client.get(f"/api/projects/project-b/conversations/{first['id']}").status_code == 404
    assert client.get(f"{base}/{second['id']}").json()["records"] == []


def test_browser_cannot_forge_task_or_assistant_result(client):
    base = "/api/projects/p/conversations"
    chat = client.post(base, json={}).json()
    url = f"{base}/{chat['id']}/messages"
    request = {"record_id": "user", "expected_revision": 1, "text": "Дуб"}
    for extra in ({"role": "assistant"}, {"patch": {"quantity": 100}}, {"status": "applied"}):
        assert client.post(url, json={**request, **extra}).status_code == 422
    assert client.get(f"{base}/{chat['id']}").json()["revision"] == 1


def test_create_retry_does_not_duplicate_chat(client):
    base = "/api/projects/p/conversations"
    request = {"title": "Деревья вдоль зданий", "request_id": "create-1"}
    first = client.post(base, json=request)
    assert first.status_code == 201
    assert client.post(base, json=request).json() == first.json()
    assert len(client.get(base).json()) == 1
    assert client.post(base, json={**request, "title": "Другая задача"}).status_code == 409
    assert client.post("/api/projects/other/conversations", json=request).json()["id"] != first.json()["id"]


def test_legacy_import_preserves_text_status_and_is_repeatable(client):
    base = "/api/projects/p/conversations"
    payload = {"source_id": "browser-history-v1", "messages": [
        {"id": "one", "role": "user", "text": "Вдоль зданий"},
        {"id": "two", "role": "assistant", "text": "Подготовлю предложение", "result": "Предложение отклонено"},
    ]}
    first = client.post(f"{base}/import", json=payload)
    assert first.status_code == 200
    chat = first.json()
    assert client.post(f"{base}/import", json=payload).json() == chat
    assert len(client.get(base).json()) == 1
    assert chat["records"][1]["payload"]["content"]["text"] == "Подготовлю предложение"
    assert chat["records"][2]["kind"] == "tool_event"
    assert chat["task"]["values"]["operation"] is None
    payload["messages"].append({"id": "three", "role": "user", "text": "Дуб"})
    assert client.post(f"{base}/import", json=payload).status_code == 409
    assert client.get(f"{base}/{chat['id']}").json() == chat


def test_invalid_import_is_not_partially_written(client):
    base = "/api/projects/p/conversations"
    payload = {"source_id": "old", "messages": [{"id": "1", "role": "user", "text": "valid"}, {"id": "2", "role": "tool", "text": "invalid"}]}
    assert client.post(f"{base}/import", json=payload).status_code == 422
    assert client.get(base).json() == []


def test_missing_project_does_not_create_orphan_conversation():
    with TestClient(app) as client:
        assert client.post("/api/projects/nonexistent-agent-project/conversations", json={}).status_code == 404


def test_interpretation_persists_turn_atomically_and_retry_skips_model(client, monkeypatch):
    project = client.post("/api/projects", json={"name": "Диалоги агента"}).json()
    project_id = project["id"]
    before = client.get(f"/api/projects/{project_id}").json()
    base = f"/api/projects/{project_id}/conversations"
    chat = client.post(base, json={}).json()
    calls = []
    def interpret(*args):
        calls.append(args)
        return TaskPatch(quantity=100), TaskInterpretation(intent="amend")
    monkeypatch.setattr(routes.local, "configured_model", lambda: "local")
    monkeypatch.setattr(routes, "interpret_task", interpret)
    url = f"{base}/{chat['id']}/interpret"
    payload = {"record_id": "first", "expected_revision": 1, "text": "100 деревьев"}
    response = client.post(url, json=payload)
    assert response.status_code == 200
    assert response.json()["task"]["values"]["quantity"] == 100
    assert len(response.json()["records"]) == 2
    assert client.post(url, json=payload).json() == response.json()
    assert len(calls) == 1
    assert client.get(f"/api/projects/{project_id}").json() == before
    assert client.post(url, json={**payload, "record_id": "second"}).status_code == 409
    assert len(calls) == 1


def test_interpretation_freezes_map_selection_and_keeps_message_context(client, monkeypatch):
    from app import api
    from test_placement_allocation import application
    domain, project = application()
    monkeypatch.setattr(api, "application", domain)
    monkeypatch.setattr(routes.local, "configured_model", lambda: "local")
    contexts = []
    def interpret(text, task, records, context, model):
        contexts.append(context)
        return TaskPatch(scope="selection", quantity=100), TaskInterpretation(intent="amend")
    monkeypatch.setattr(routes, "interpret_task", interpret)
    base = f"/api/projects/{project.id}/conversations"
    chat = client.post(base, json={}).json()
    url = f"{base}/{chat['id']}/interpret"
    payload = {"record_id": "selected", "expected_revision": 1, "text": "Здесь 100 деревьев",
               "map_context": {"state_version": project.state_version, "zone_ids": ["west"]}}
    response = client.post(url, json=payload)
    assert response.status_code == 200
    saved = response.json()
    assert saved["task"]["values"]["zone_ids"] == ["west"]
    assert saved["task"]["values"]["scope"] == "zones"
    assert saved["records"][0]["payload"]["content"]["map_context"]["zone_ids"] == ["west"]
    assert contexts[0]["map_context"]["selected_zone_ids"] == ["west"]
    assert client.post(url, json=payload).json() == saved
    assert len(contexts) == 1
    project.name = "Updated after the selection"
    domain.repository.save(project)
    stale = client.post(url, json={**payload, "record_id": "stale", "expected_revision": saved["revision"]})
    assert stale.status_code == 409
    assert client.get(f"{base}/{chat['id']}").json() == saved
    assert len(contexts) == 1


def test_prepare_existing_task_stores_preview_once_without_applying(client, monkeypatch):
    from app import api
    from test_agent_planning import existing_application
    domain, project = existing_application()
    monkeypatch.setattr(api, "application", domain)
    monkeypatch.setattr(routes.local, "configured_model", lambda: "local")
    monkeypatch.setattr(routes, "interpret_task", lambda *args: (
        TaskPatch(operation="delete", scope="objects", object_ids=["one", "two"]), TaskInterpretation(intent="amend")))
    base = f"/api/projects/{project.id}/conversations"
    chat = client.post(base, json={}).json()
    url = f"{base}/{chat['id']}"
    turn = client.post(f"{url}/interpret", json={"record_id": "delete", "expected_revision": 1, "text": "Удалите выбранные посадки"}).json()
    before = domain.get(project.id).model_dump_json()
    request = {"record_id": "preview", "expected_revision": turn["revision"]}
    response = client.post(f"{url}/prepare", json=request)
    assert response.status_code == 200
    prepared = response.json()
    record = prepared["records"][-1]
    assert record["kind"] == "tool_event"
    assert record["payload"]["content"]["event"] == "task_prepared"
    assert set(record["payload"]["content"]["preparation"]["change_set"]["deletion_ids"]) == {"one", "two"}
    assert client.post(f"{url}/prepare", json=request).json() == prepared
    assert len(domain._change_set_previews) == 1
    assert client.post(f"{url}/prepare", json={**request, "record_id": "stale"}).status_code == 409
    assert domain.get(project.id).model_dump_json() == before
