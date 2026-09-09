import pytest
from app.agent_conditions import bind_placement_conditions, compact_constraint_evidence
from app.contracts import Project


@pytest.mark.parametrize('text', ['нормативные отступы', 'сохраняя нормативные отступы', 'соблюдая нормативные отступы', 'нормативные отступы соблюдай', 'соблюдайте нормативные отступы', 'с соблюдением нормативных отступов', 'при соблюдении нормативных отступов'])
def test_supported_setback_request_retains_source_and_missing_evidence(text):
    result = bind_placement_conditions(Project(name='Нет геометрии'), [text], [])
    assert result['bindings'][0]['source'] == text
    assert result['full_compliance_verified'] is False
    assert all(rule['status'] == 'no_geometry' for rule in result['rules'])


@pytest.mark.parametrize('text', ['не соблюдать нормативные отступы', 'отступ 10 метров', 'нормативные отступы и не ближе 10 метров', 'нормативные отступы кроме дороги'])
def test_extra_or_negative_requirements_are_never_discarded(text):
    with pytest.raises(ValueError, match='не связано'):
        bind_placement_conditions(Project(name='Проверка'), [text], [])


def test_exclusions_do_not_become_a_generic_setback_policy():
    with pytest.raises(ValueError, match='исключённые'):
        bind_placement_conditions(Project(name='Проверка'), ['нормативные отступы'], ['северная часть'])


def test_overlapping_setback_phrases_keep_one_source_fragment():
    assert compact_constraint_evidence(['нормативные отступы', 'сохраняя нормативные отступы']) == [
        'сохраняя нормативные отступы'
    ]
