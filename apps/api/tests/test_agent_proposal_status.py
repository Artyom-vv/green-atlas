from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app import api
from app.agent_conversations import project_store
from app.agent_memory import TaskPatch
from app.main import app
from app.history.adapters import InMemoryProjectHistory
from test_agent_conversations import client
from test_agent_planning import existing_application


@pytest.mark.parametrize("condition,expected", [
    ("current", "ready"), ("expired", "expired"), ("evicted", "unavailable"),
    ("changed_project", "stale"), ("changed_task", "superseded"), ("later_preview", "superseded"),
    ("declined", "declined"),
])
def test_durable_proposal_is_checked_against_live_availability(client, monkeypatch, condition, expected):
    domain, project = existing_application()
    domain.history = InMemoryProjectHistory()
    monkeypatch.setattr(api, "application", domain)
    store = app.dependency_overrides[project_store]()
    chat = store.create(project.id)
    chat = store.append(project.id, chat["id"], expected_revision=1, record_id="request", kind="message",
                        payload={"role": "user", "text": "Удалить выбранное"},
                        patch=TaskPatch(operation="delete", scope="objects", object_ids=["one"]))
    base = f"/api/projects/{project.id}/conversations/{chat['id']}"
    response = client.post(f"{base}/prepare", json={"record_id": "preview", "expected_revision": chat["revision"]})
    assert response.status_code == 200
    chat = response.json()
    preview = chat["records"][-1]["payload"]["content"]["preparation"]["change_set"]
    if condition == "expired":
        domain._change_set_previews[preview["id"]].preview.expires_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    elif condition == "evicted":
        domain._change_set_previews.clear()
    elif condition == "changed_project":
        project.name = "Changed after calculation"
        domain.repository.save(project)
    elif condition == "changed_task":
        chat = store.append(project.id, chat["id"], expected_revision=chat["revision"], record_id="amend", kind="message",
                            payload={"role": "user", "text": "Только второе"}, patch=TaskPatch(object_ids=["two"]))
    elif condition == "later_preview":
        chat = client.post(f"{base}/prepare", json={"record_id": "new-preview", "expected_revision": chat["revision"]}).json()
    elif condition == "declined":
        request = {"record_id": "decline", "expected_revision": chat["revision"]}
        original_task = chat["task"]
        chat = client.post(f"{base}/proposals/preview/decline", json=request).json()
        assert chat["task"] == original_task
        assert client.post(f"{base}/proposals/preview/decline", json=request).json() == chat
    before = domain.get(project.id).model_dump_json()
    result = client.get(f"{base}/proposals/preview/status")
    assert result.status_code == 200
    assert result.json() == {"record_id": "preview", "status": expected, "can_apply": expected == "ready"}
    assert domain.get(project.id).model_dump_json() == before
    assert store.get(project.id, chat["id"]) == {key: value for key, value in chat.items() if key != "can_prepare"}
    assert client.get(f"{base}/proposals/request/status").status_code == 404
    assert client.get(f"/api/projects/other/conversations/{chat['id']}/proposals/preview/status").status_code == 404
    confirm = f"{base}/proposals/preview/confirm"
    assert client.post(confirm, json={"expected_revision": chat["revision"] - 1}).status_code == 409
    assert domain.get(project.id).model_dump_json() == before
    applied = client.post(confirm, json={"expected_revision": chat["revision"]})
    if expected != "ready":
        assert applied.status_code == 409
        assert domain.get(project.id).model_dump_json() == before
    else:
        assert applied.status_code == 200, applied.text
        assert "one" not in [item.id for item in domain.get(project.id).plan.objects]
        after = domain.get(project.id).model_dump_json()
        assert client.post(confirm, json={"expected_revision": chat["revision"]}).status_code == 409
        assert domain.get(project.id).model_dump_json() == after


def test_confirmation_serializes_with_concurrent_task_amendment(client, monkeypatch):
    domain, project = existing_application()
    domain.history = InMemoryProjectHistory()
    monkeypatch.setattr(api, "application", domain)
    store = app.dependency_overrides[project_store]()
    chat = store.create(project.id)
    chat = store.append(project.id, chat["id"], expected_revision=1, record_id="request", kind="message",
                        payload={"role": "user", "text": "Удалить первое"},
                        patch=TaskPatch(operation="delete", scope="objects", object_ids=["one"]))
    base = f"/api/projects/{project.id}/conversations/{chat['id']}"
    chat = client.post(f"{base}/prepare", json={"record_id": "preview", "expected_revision": chat["revision"]}).json()
    entered, release, attempted, amended = Event(), Event(), Event(), Event()
    original = domain.apply_change_set

    def paused_apply(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    def amend():
        attempted.set()
        result = store.append(project.id, chat["id"], expected_revision=chat["revision"], record_id="amend",
                              kind="message", payload={"role": "user", "text": "Теперь второе"},
                              patch=TaskPatch(object_ids=["two"]))
        amended.set()
        return result

    monkeypatch.setattr(domain, "apply_change_set", paused_apply)
    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmation = pool.submit(client.post, f"{base}/proposals/preview/confirm", json={"expected_revision": chat["revision"]})
        try:
            assert entered.wait(5)
            amendment = pool.submit(amend)
            assert attempted.wait(5)
            assert not amended.wait(0.05)
        finally:
            release.set()
        assert confirmation.result(timeout=5).status_code == 200
        assert amendment.result(timeout=5)["task"]["values"]["object_ids"] == ["two"]
    assert amended.is_set()
    assert "one" not in [item.id for item in domain.get(project.id).plan.objects]
    assert "two" in [item.id for item in domain.get(project.id).plan.objects]
