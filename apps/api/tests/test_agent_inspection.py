from app.composition import get_runtime
import pytest

from app import api, agent_conversations as routes
from app.agent_conversations import project_store
from app.agent_interpreter import TaskInterpretation
from app.agent_memory import TaskPatch
from app.agent_loop import ReadStep, run_read_agent
from app.main import app
from test_agent_conversations import client
from test_agent_planning import existing_application


@pytest.mark.parametrize("intent", ["discuss", "new_task"])
def test_inspection_preserves_task_and_persists_evidence_once(client, monkeypatch, intent):
    domain, project = existing_application()
    monkeypatch.setattr(get_runtime(), "application", domain)
    monkeypatch.setattr(routes.local, "configured_model", lambda: "test")
    monkeypatch.setattr(routes, "interpret_task", lambda *_: (TaskPatch(operation="inspect"), TaskInterpretation(intent=intent, operation="inspect")))
    store = app.dependency_overrides[project_store]()
    chat = store.create(project.id)
    chat = store.append(project.id, chat["id"], expected_revision=1, record_id="task", kind="message",
                        payload={"role": "user", "text": "100 деревьев"},
                        patch=TaskPatch(operation="place", quantity=100, scope="zones", zone_ids=["west"]))
    original_task = chat["task"]
    before = domain.get(project.id).model_dump_json()
    steps = iter([ReadStep(action="tool", tool="find_plantings", arguments_json="{}", text="", evidence=[]),
                  ReadStep(action="answer", tool="", arguments_json="", text="В проекте 3 посадки.", evidence=[0])])
    calls = []

    def inspect(*args, **kwargs):
        calls.append(kwargs["context"])
        return run_read_agent(*args, **kwargs, choose=lambda *_: next(steps))

    monkeypatch.setattr(routes, "run_read_agent", inspect)
    url = f"/api/projects/{project.id}/conversations/{chat['id']}/interpret"
    request = {"record_id": "question", "expected_revision": chat["revision"], "text": "Сколько посадок?"}
    response = client.post(url, json=request)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["task"] == original_task
    assert [r["kind"] for r in result["records"][-3:]] == ["message", "tool_event", "message"]
    evidence = result["records"][-2]["payload"]["content"]
    assert evidence["event"] == "project_inspected"
    assert evidence["inspection"]["events"][0]["data"]["result"]["total"] == 3
    assert result["records"][-1]["payload"]["content"]["text"] == "В проекте 3 посадки."
    assert client.post(url, json=request).json() == result
    assert len(calls) == 1
    assert domain.get(project.id).model_dump_json() == before


def test_evidence_write_failure_rolls_back_source_and_answer(client):
    import sqlite3
    store = app.dependency_overrides[project_store]()
    chat = store.create("p")
    chat = store.append("p", chat["id"], expected_revision=1, record_id="tools:question", kind="tool_event", payload={"event": "existing"})
    with pytest.raises(sqlite3.IntegrityError):
        store.append("p", chat["id"], expected_revision=chat["revision"], record_id="question", kind="message",
                     payload={"role": "user", "text": "Сколько?"}, assistant_text="3",
                     tool_result={"event": "project_inspected"})
    assert store.get("p", chat["id"]) == chat
