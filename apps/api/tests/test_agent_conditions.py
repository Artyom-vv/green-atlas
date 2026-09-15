import pytest
from app.agent_conditions import (bind_placement_conditions, compact_constraint_evidence,
    extract_supported_setback_constraints, extract_unsupported_constraint_evidence,
    is_supported_setback_constraint)
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


@pytest.mark.parametrize('source', [
    'соблюдай отступы', 'отступы соблюдай', 'соблюдайте отступы', 'отступы соблюдайте',
    'соблюдая отступы', 'соблюдать отступы', 'сохраняй отступы', 'сохраняя отступы',
    'сохраните отступы', 'с соблюдением отступов', 'при соблюдении отступов',
    'с учётом отступов', 'СОБЛЮДАЙ   ОТСТУПЫ', 'нормативные отступы соблюдай',
])
def test_generic_setback_word_orders_bind_the_same_existing_policy(source):
    assert is_supported_setback_constraint(source)
    assert extract_supported_setback_constraints(f'Посади 6 деревьев, {source}.') == [source]
    assert not extract_unsupported_constraint_evidence(f'Посади 6 деревьев, {source}.')
    result = bind_placement_conditions(Project(name='Проверка'), [source], [])
    assert result['bindings'][0]['rule_ids'] == ['pp743-3.6.3-building', 'pp743-3.6.3-road-edge']
    assert not result['full_compliance_verified']


@pytest.mark.parametrize('source', [
    'не соблюдай отступы', 'не соблюдайте отступы', 'не соблюдая отступы',
    'отступы не соблюдай', 'отступы не соблюдать', 'не надо соблюдать отступы',
    'без соблюдения отступов', 'без учёта отступов', 'игнорируй отступы',
    'соблюдай отступы 10 метров', 'отступы 10 метров соблюдай',
    'отступы соблюдай 10 метров', 'соблюдай отступы не менее десяти метров',
    'соблюдай отступы кроме дороги', 'отступы соблюдай только от зданий',
    'соблюдай отступы не 5, а 10 метров', 'соблюдай отступы=10м',
    'увеличь нормативные отступы', 'нормативные отступы не соблюдай',
    'не сохраняй нормативные отступы', 'ненормативные отступы',
])
def test_source_extraction_never_turns_modified_or_negative_setbacks_into_a_safe_substring(source):
    assert not is_supported_setback_constraint(source)
    assert not extract_supported_setback_constraints(source)
    assert extract_unsupported_constraint_evidence(source)
    with pytest.raises(ValueError, match='не связано'):
        bind_placement_conditions(Project(name='Проверка'), [source], [])


@pytest.mark.parametrize('source', ['соблюдай отступы', 'отступы соблюдай'])
def test_separate_numeric_constraint_stays_explicit_beside_generic_policy(source):
    text = f'Посади деревья, {source} и не ближе 10 метров'
    assert extract_supported_setback_constraints(text) == [source]
    assert extract_unsupported_constraint_evidence(text) == ['не ближе 10 метров']
