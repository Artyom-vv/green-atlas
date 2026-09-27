import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import planning_assistant as assistant


def test_default_is_off_and_cloud_models_are_not_allowed(monkeypatch):
    monkeypatch.delenv('GREEN_ATLAS_LOCAL_MODEL', raising=False)
    assert not assistant.assistant_status().available
    monkeypatch.setenv('GREEN_ATLAS_LOCAL_MODEL', 'qwen3.5:cloud')
    with pytest.raises(HTTPException) as error:
        assistant.interpret(assistant.PlanningBriefInput(task='Больше тени'))
    assert error.value.status_code == 503


def test_only_explicit_text_goes_to_local_model(monkeypatch):
    seen = []
    brief = dict(profile='shade', max_sites=40, unsupported=[], questions=[])
    def respond(path, body, **kwargs):
        seen.append((path, body))
        return {'message': {'content': json.dumps(brief)}}
    monkeypatch.setattr(assistant, 'local_json', respond)
    assert assistant.interpret_task('Больше тени, максимум 40 посадок', 'qwen3.5:4b').max_sites == 40
    body = seen[0][1]
    assert body['messages'][1]['content'] == 'Больше тени, максимум 40 посадок'
    assert 'tools' not in body
    assert 'project' not in body
    assert body['stream'] is False


@pytest.mark.parametrize('extra', [dict(x=1, y=2), dict(can_apply=True), dict(commands=['delete'])])
def test_model_cannot_return_actions_or_geometry(extra):
    with pytest.raises(ValidationError):
        assistant.PlanningBrief.model_validate(dict(profile='shade', max_sites=40, unsupported=[], questions=[], **extra))


@pytest.mark.parametrize('quantity', [0, 501, '40', True])
def test_model_quantity_is_strict_and_bounded(quantity):
    with pytest.raises(ValidationError):
        assistant.PlanningBrief.model_validate(dict(profile='shade', max_sites=quantity, unsupported=[], questions=[]))


def test_paraphrased_restriction_never_becomes_an_accepted_task(monkeypatch):
    def respond(*args, **kwargs):
        return {'message': {'content': json.dumps(dict(profile='shade', max_sites=40, unsupported=['invented explanation'], questions=[]))}}
    monkeypatch.setattr(assistant, 'local_json', respond)
    result = assistant.interpret_task('Только липы, максимум 40', 'qwen3.5:4b')
    assert result.unsupported == ['Только липы, максимум 40']


def test_failure_releases_single_request_lock(monkeypatch):
    monkeypatch.setenv('GREEN_ATLAS_LOCAL_MODEL', 'qwen3.5:4b')
    def fail(*args):
        raise ValueError('malformed model JSON')
    monkeypatch.setattr(assistant, 'interpret_task', fail)
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            assistant.interpret(assistant.PlanningBriefInput(task='Больше тени'))
        assert error.value.status_code == 503


@pytest.mark.parametrize('requirement,unsupported', [
    ('Соблюдай ограничения проекта', False),
    ('Учитывай все ограничения чертежа', False),
    ('Соблюдай ограничения инсоляции', True),
    ('Соблюдай ограничения проекта и бюджет 10000 рублей', True),
])
def test_generic_constraint_request_is_already_handled_but_specific_requirements_are_not_lost(monkeypatch, requirement, unsupported):
    monkeypatch.setattr(assistant, 'local_json', lambda *a, **kw: {'message': {'content': json.dumps(dict(
        arrangement='area', profile='shade', max_sites=15, unsupported=[requirement], questions=[]))}})
    result = assistant.interpret_task('Больше тени. Максимум 15 растений. ' + requirement, 'qwen3.5:4b')
    assert bool(result.unsupported) is unsupported


def test_concurrent_request_is_rejected(monkeypatch):
    monkeypatch.setenv('GREEN_ATLAS_LOCAL_MODEL', 'qwen3.5:4b')
    assistant._busy.acquire()
    try:
        with pytest.raises(HTTPException) as error:
            assistant.interpret(assistant.PlanningBriefInput(task='Больше тени'))
        assert error.value.status_code == 429
    finally:
        assistant._busy.release()


def test_input_has_no_project_payload_and_no_blank_tasks():
    for body in [dict(task='  '), dict(task='Больше тени', project={}), dict(task='x' * 2001)]:
        with pytest.raises(ValidationError):
            assistant.PlanningBriefInput.model_validate(body)
