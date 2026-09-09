import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import project_assistant as assistant


def request(**changes):
    return assistant.ProjectChatInput(message='Удали посадки с участка', project_name='Проверка', planting_count=22, selected_count=0,
                                      zones=[{'id': 'west', 'label': 'Западный', 'planting_count': 22}], **changes)


def mock_reply(monkeypatch, **changes):
    payload = assistant.ProjectChatReply(action='delete', reply='На каком участке?', **changes).model_dump()
    seen = []
    def respond(path, body, **kwargs):
        seen.append(body)
        return {'message': {'content': json.dumps(payload)}}
    monkeypatch.setattr(assistant.local, 'local_json', respond)
    return seen


def test_conversation_is_bounded_local_and_cannot_execute(monkeypatch):
    seen = mock_reply(monkeypatch)
    result = assistant.interpret_chat(request(history=[{'role': 'user', 'content': 'Обсудим западный участок'}]), 'qwen3.5:4b')
    assert result.scope == 'ask'
    assert seen[0]['messages'][1]['content'] == 'Обсудим западный участок'
    assert '"planting_count": 22' in seen[0]['messages'][0]['content']
    assert 'tools' not in seen[0]
    assert seen[0]['stream'] is False


@pytest.mark.parametrize('scope,zone_id', [('zone', 'unknown'), ('selection', None)])
def test_unresolved_scope_never_expands_to_project(monkeypatch, scope, zone_id):
    mock_reply(monkeypatch, scope=scope, zone_id=zone_id)
    result = assistant.interpret_chat(request(), 'qwen3.5:4b')
    assert result.scope == 'ask'


def test_model_cannot_invent_global_authority(monkeypatch):
    mock_reply(monkeypatch, scope='project')
    result = assistant.interpret_chat(request(), 'qwen3.5:4b')
    assert result.scope == 'ask'


def test_explicit_all_zones_does_not_need_repeated_clarification(monkeypatch):
    mock_reply(monkeypatch, scope='ask')
    command = request().model_copy(update={'message': 'Удали проектные посадки на всех участках'})
    assert assistant.interpret_chat(command, 'qwen3.5:4b').scope == 'project'


def test_exclusion_prevents_global_scope(monkeypatch):
    mock_reply(monkeypatch, scope='project')
    command = request().model_copy(update={'message': 'Удали посадки на всех участках кроме западного'})
    assert assistant.interpret_chat(command, 'qwen3.5:4b').scope == 'ask'


@pytest.mark.parametrize('count', [None, 80])
def test_explicit_limit_survives_model_omission(monkeypatch, count):
    def respond(*args, **kwargs):
        return {'message': {'content': assistant.ProjectChatReply(action='building_screen', scope='project', reply='Подготовлю', max_sites=count).model_dump_json()}}
    monkeypatch.setattr(assistant.local, 'local_json', respond)
    command = request().model_copy(update={'message': 'Посади группы деревьев вдоль зданий на всех участках, не больше 10 посадок'})
    result = assistant.interpret_chat(command, 'qwen3.5:4b')
    assert result.scope == 'project'
    assert result.max_sites == 10


@pytest.mark.parametrize('changes', [{'max_sites': '22'}, {'max_sites': 501}, {'horizon': 41}, {'commands': ['delete']}, {'can_apply': True}])
def test_model_output_is_not_an_execution_protocol(changes):
    with pytest.raises(ValidationError):
        assistant.ProjectChatReply(action='reply', reply='Ответ', **changes)


def test_limits_context_size():
    with pytest.raises(ValidationError):
        request(data_gaps=['x' * 601])
    with pytest.raises(ValidationError):
        request(history=[{'role': 'user', 'content': 'Вопрос'}] * 13)


def test_errors_release_lock(monkeypatch):
    monkeypatch.setenv('GREEN_ATLAS_LOCAL_MODEL', 'qwen3.5:4b')
    def fail(*args):
        raise ValueError('bad reply')
    monkeypatch.setattr(assistant, 'interpret_chat', fail)
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            assistant.chat(request())
        assert error.value.status_code == 503
    assert assistant.local._busy.acquire(blocking=False)
    assistant.local._busy.release()


def test_explicit_forecast_does_not_need_model_or_zone(monkeypatch):
    monkeypatch.delenv('GREEN_ATLAS_LOCAL_MODEL', raising=False)
    command = request().model_copy(update={'message': 'Покажи рост через 20 лет'})
    result = assistant.chat(command)
    assert (result.action, result.horizon) == ('growth', 20)


@pytest.mark.parametrize('message', ['Не показывай рост через 20 лет', 'Покажи рост через 20 лет и удали деревья', 'Покажи рост через 60 лет', 'Как выполнить «покажи рост через 20 лет»?'])
def test_view_shortcut_never_drops_other_requirements(message):
    assert assistant.explicit_view_request(message) is None
