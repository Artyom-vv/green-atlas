"""Message validation and accumulated source history have separate HTTP limits."""
from app.composition import get_runtime
from contextlib import closing

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import api, planning_assistant as local
from app.agent_runtime import routes
from app.agent_runtime.contracts import AgentDecision, AgentIntent, Goal
from app.agent_runtime.history_budget import (
    MAX_INITIAL_MESSAGE_CHARS, MAX_ANSWER_MESSAGE_CHARS, MAX_HISTORY_CHARS, MAX_HISTORY_TURNS,
    SOURCE_TURN_SEPARATOR,
)
from app.agent_runtime.store import AgentRunStore
from test_zone_workflow import project_with_contour


@pytest.fixture
def history_http(tmp_path, monkeypatch):
    app, project, _reference = project_with_contour()
    compiled = []

    class SourcePreservingCompiler:
        """Isolate budget enforcement from interpretation of arbitrary filler."""
        def __init__(self, _model):
            pass

        def compile(self, text, *, source_turns=None, **_kwargs):
            turns = list(source_turns or [text])
            compiled.append((text, turns))
            return AgentIntent(raw_text=text, source_turns=turns, goal=Goal(operation="inspect"), scope_mode="project")

    class QuestionPlanner:
        def __init__(self, *_args):
            pass

        def __call__(self, _context):
            return AgentDecision(action="ask", missing_slot="requirements", question="Уточните условия задания.")

    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        monkeypatch.setattr(get_runtime(), "application", app)
        monkeypatch.setattr(routes, "_store", lambda: store)
        monkeypatch.setattr(local, "configured_model", lambda: "test")
        monkeypatch.setattr(routes, "IntentCompiler", SourcePreservingCompiler)
        monkeypatch.setattr(routes, "LocalPlanner", QuestionPlanner)
        http = FastAPI()
        http.include_router(routes.router)
        yield TestClient(http), f"/api/projects/{project.id}/agent-runs", compiled


def create_question(client, base, text):
    response = client.post(base, json={"text": text})
    assert response.status_code == 201, response.text
    url = base + "/" + response.json()["state"]["run_id"]
    question(client, url)
    return url


def question(client, url):
    response = client.post(url + "/run")
    assert response.status_code == 200, response.text
    assert client.get(url).json()["state"]["status"] == "waiting_question"


@pytest.mark.parametrize("answer_size", [1, MAX_ANSWER_MESSAGE_CHARS])
def test_initial_maximum_and_valid_answer_preserve_history_over_2000_chars(history_http, answer_size):
    client, base, compiled = history_http
    prefix = "Сохрани посадки и условия. "
    initial = prefix + "у" * (MAX_INITIAL_MESSAGE_CHARS - len(prefix))
    answer = "я" * answer_size
    url = create_question(client, base, initial)
    response = client.post(url + "/answer", json={"text": answer})
    assert response.status_code == 200, response.text
    intent = response.json()["state"]["intent"]
    assert len(intent["raw_text"]) > MAX_INITIAL_MESSAGE_CHARS
    assert intent["raw_text"] == SOURCE_TURN_SEPARATOR.join([initial, answer])
    assert intent["source_turns"] == [initial, answer]
    assert compiled[-1] == (intent["raw_text"], [initial, answer])
    assert client.get(url).json()["state"]["intent"] == intent


def test_accumulated_character_limit_returns_safe_error_without_mutating_run(history_http):
    client, base, compiled = history_http
    initial = "У" * MAX_INITIAL_MESSAGE_CHARS
    answer = "Я" * MAX_ANSWER_MESSAGE_CHARS
    turns = [initial]
    url = create_question(client, base, initial)
    while len(SOURCE_TURN_SEPARATOR.join([*turns, answer])) <= MAX_HISTORY_CHARS:
        response = client.post(url + "/answer", json={"text": answer})
        assert response.status_code == 200, response.text
        turns.append(answer)
        question(client, url)
    remaining = MAX_HISTORY_CHARS - len(SOURCE_TURN_SEPARATOR.join(turns)) - len(SOURCE_TURN_SEPARATOR)
    assert 0 < remaining <= MAX_ANSWER_MESSAGE_CHARS
    final_answer = "ю" * remaining
    exact = client.post(url + "/answer", json={"text": final_answer})
    assert exact.status_code == 200, exact.text
    turns.append(final_answer)
    assert len(exact.json()["state"]["intent"]["raw_text"]) == MAX_HISTORY_CHARS
    question(client, url)
    before = client.get(url).json()
    compile_count = len(compiled)
    response = client.post(url + "/answer", json={"text": "я"})
    assert response.status_code == 413, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "AGENT_HISTORY_LIMIT"
    assert "Ответ не добавлен" in detail["message"]
    assert detail["details"]["max_history_chars"] == MAX_HISTORY_CHARS
    assert len(compiled) == compile_count
    assert client.get(url).json() == before
    assert before["state"]["intent"]["source_turns"] == turns
    assert before["state"]["intent"]["raw_text"] == SOURCE_TURN_SEPARATOR.join(turns)


def test_more_than_40_turns_survive_and_explicit_turn_budget_does_not_truncate(history_http):
    client, base, compiled = history_http
    turns = ["Не меняй существующие посадки."]
    url = create_question(client, base, turns[0])
    for index in range(1, MAX_HISTORY_TURNS):
        answer = f"Уточнение {index}"
        response = client.post(url + "/answer", json={"text": answer})
        assert response.status_code == 200, response.text
        turns.append(answer)
        if len(turns) == 41:
            assert response.json()["state"]["intent"]["source_turns"] == turns
        question(client, url)
    before = client.get(url).json()
    compile_count = len(compiled)
    response = client.post(url + "/answer", json={"text": "Следующее уточнение"})
    assert response.status_code == 413, response.text
    assert response.json()["detail"]["code"] == "AGENT_HISTORY_LIMIT"
    assert response.json()["detail"]["details"]["max_history_turns"] == MAX_HISTORY_TURNS
    assert len(compiled) == compile_count
    assert client.get(url).json() == before
    assert before["state"]["intent"]["source_turns"] == turns


def test_per_message_limits_remain_in_force(history_http):
    client, base, compiled = history_http
    assert client.post(base, json={"text": "У" * (MAX_INITIAL_MESSAGE_CHARS + 1)}).status_code == 422
    assert compiled == []
    url = create_question(client, base, "Сохрани все условия задания.")
    before = client.get(url).json()
    assert client.post(url + "/answer", json={"text": "Я" * (MAX_ANSWER_MESSAGE_CHARS + 1)}).status_code == 422
    assert client.get(url).json() == before


def test_intent_cannot_hide_oversized_history_behind_short_raw_text():
    with pytest.raises(ValidationError, match="предел истории"):
        AgentIntent(raw_text="Короткое представление", source_turns=["я" * MAX_HISTORY_CHARS, "Уточнение"],
            goal=Goal(operation="inspect"))
