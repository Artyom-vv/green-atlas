import json

import pytest

from app.agent_memory import TaskPatch, TaskState
from app.agent_interpreter import interpret_task, project_context, local
from app.agent_readiness import next_question
from types import SimpleNamespace


CONTEXT = {"zones": [{"id": "six", "label": "Допустимая область 6", "number": 6}], "species": [{"id": "oak@1", "name": "Дуб", "kind": "tree"}]}


def test_context_numbers_match_visible_labels_not_array_positions():
    zones = [SimpleNamespace(id='audit', label='Аудит Зарядье')]
    zones += [SimpleNamespace(id=f'area-{i}', label='Допустимая область') for i in range(1, 7)]
    project = SimpleNamespace(planting_zones=zones, name='Реальная структура проекта', state_version=32)
    context = project_context(project)
    assert context['zones'][-1] == {'id': 'area-6', 'label': 'Допустимая область 6', 'number': 6}
    assert 'number' not in context['zones'][0]


@pytest.mark.parametrize('text', ['Заполни участок 6 деревьями и кустарниками', 'Нужно озеленить допустимую область 6: всего 60 растений', 'На допустимой области 6 посадите деревья'])
def test_numbered_zone_resolves_visible_alias_even_if_model_uses_wrong_id(monkeypatch, text):
    respond(monkeypatch, ['zone_ids'], {'zone_ids': ['fifth']}, response={'zone_numbers': [6]})
    context = {**CONTEXT, 'zones': [{'id': 'fifth', 'label': 'Допустимая область 5', 'number': 5}, {'id': 'six', 'label': 'Допустимая область 6', 'number': 6}]}
    patch, _ = interpret_task(text, TaskState(), [], context, 'local')
    assert patch.zone_ids == ['six']


@pytest.mark.parametrize('text', ['Посади деревья в 6 зоне', 'Заполни зону 6 деревьями', 'Размести растения на зоне №6'])
def test_agent_zone_extraction_resolves_zone_word(monkeypatch, text):
    respond(monkeypatch, [], {}, response={'zone_numbers': [6]})
    context = {**CONTEXT, 'zones': [
        {'id': 'five', 'label': 'Допустимая область 5', 'number': 5},
        {'id': 'six', 'label': 'Допустимая область 6', 'number': 6},
    ]}
    patch, _ = interpret_task(text, TaskState(), [], context, 'local')
    assert patch.zone_ids == ['six']


def test_bare_quantity_answer_does_not_reinterpret_retained_conditions(monkeypatch):
    state = TaskState().amended(TaskPatch(operation='place', scope='zones', zone_ids=['six'], plant_kind='mixed', arrangement='building_contour', species_mode='automatic', constraints=['нормативные отступы']), 'first')
    def unexpected(*args, **kwargs):
        raise AssertionError('An unambiguous answer to the quantity question needs no model')
    monkeypatch.setattr(local, 'local_json', unexpected)
    patch, result = interpret_task('60', state, [], CONTEXT, 'local')
    assert patch.model_dump(exclude_unset=True) == {'quantity': 60, 'quantity_mode': 'target'}
    assert result.intent == 'amend'
    updated = state.amended(patch, 'second')
    assert updated.values.zone_ids == ['six']
    assert updated.values.species_mode == 'automatic'
    assert updated.values.constraints == ['нормативные отступы']
    assert updated.provenance['zone_ids'] == 'first'


def test_bare_quantity_amendment_after_preview_keeps_active_planting_context(monkeypatch):
    state = TaskState().amended(TaskPatch(
        operation='place', scope='zones', zone_ids=['six'], quantity=60,
        plant_kind='mixed', arrangement='building_contour',
        species_mode='automatic', constraints=['нормативные отступы']), 'first')

    def unexpected(*args, **kwargs):
        raise AssertionError('A quantity correction in an active planting task needs no model')

    monkeypatch.setattr(local, 'local_json', unexpected)
    patch, result = interpret_task('500', state, [], CONTEXT, 'local')
    assert patch.model_dump(exclude_unset=True) == {'quantity': 500, 'quantity_mode': 'target'}
    updated = state.amended(patch, 'correction')
    assert result.intent == 'amend'
    assert updated.values.quantity == 500
    assert updated.values.zone_ids == ['six']
    assert updated.values.plant_kind == 'mixed'
    assert updated.values.arrangement == 'building_contour'
    assert updated.values.species_mode == 'automatic'
    assert updated.values.constraints == ['нормативные отступы']


def test_delegation_and_source_constraints_have_explicit_contract(monkeypatch):
    def output(*args, **kwargs):
        return {'message': {'content': json.dumps({'intent': 'new_task', 'operation': 'place', 'species_mode': 'automatic', 'stated_constraints': ['сохраняя нормативные отступы'], 'changes': []})}}
    monkeypatch.setattr(local, 'local_json', output)
    patch, _ = interpret_task('Посади на твоё усмотрение, сохраняя нормативные отступы', TaskState(), [], CONTEXT, 'local')
    assert patch.species_mode == 'automatic'
    assert patch.constraints == ['сохраняя нормативные отступы']


def test_natural_delegated_edge_road_fill_phrase_becomes_executable_intent(monkeypatch):
    def output(*args, **kwargs):
        return {'message': {'content': json.dumps({
            'intent': 'new_task', 'operation': 'place', 'plant_kind': 'tree',
            'scope': 'zones', 'species_mode': 'automatic', 'changes': [],
            'zone_numbers': [], 'zone_selection': 'none',
        })}}

    monkeypatch.setattr(local, 'local_json', output)
    patch, _ = interpret_task(
        'Ну там вот с краю зону сам выбери любую для теста и засей там на твоё усмотрени деревья вдоль улицы, потом приблизь меня',
        TaskState(), [], CONTEXT, 'local'
    )

    assert patch.operation == 'place'
    assert patch.scope == 'zones'
    assert patch.zone_ids == []
    assert patch.spatial_anchor == 'edge'
    assert patch.alignment_target == 'road'
    assert patch.plant_kind == 'tree'
    assert patch.arrangement == 'road_edges'
    assert patch.species_mode == 'automatic'
    assert patch.quantity is None
    assert patch.quantity_mode == 'fill_available'
    assert patch.post_action == 'focus_map'
    state = TaskState().amended(patch, 'natural-command')
    assert next_question(state) is None


def test_arrangement_phrase_cannot_become_a_user_constraint(monkeypatch):
    def output(*args, **kwargs):
        return {'message': {'content': json.dumps({
            'intent': 'new_task', 'operation': 'place', 'plant_kind': 'mixed',
            'scope': 'zones', 'zone_numbers': [6],
            'species_mode': 'automatic', 'stated_constraints': ['вдоль зданий'],
            'changes': [{'field': 'arrangement', 'value': 'building_contour'}],
        })}}
    monkeypatch.setattr(local, 'local_json', output)
    patch, result = interpret_task('Посади группы деревьев вдоль зданий в 6 зоне. Деревья и кустарники сам выбери', TaskState(), [], CONTEXT, 'local')
    assert patch.arrangement == 'building_groves'
    assert patch.constraints is None
    assert result.edit_action == 'keep'


def test_explicit_group_word_is_not_flattened_to_contour(monkeypatch):
    respond(monkeypatch, ['arrangement'], {'arrangement': 'building_contour'})
    patch, _ = interpret_task('Посади группы деревьев вдоль зданий в 6 зоне', TaskState(), [], CONTEXT, 'local')
    assert patch.arrangement == 'building_groves'


def test_zone_reference_cannot_become_a_user_constraint(monkeypatch):
    def output(*args, **kwargs):
        return {'message': {'content': json.dumps({
            'intent': 'new_task', 'operation': 'place', 'plant_kind': 'mixed',
            'scope': 'zones', 'zone_numbers': [6],
            'species_mode': 'automatic', 'stated_constraints': ['в 6 зоне'],
            'changes': [],
        })}}
    monkeypatch.setattr(local, 'local_json', output)
    patch, _ = interpret_task('Посади растения в 6 зоне', TaskState(), [], CONTEXT, 'local')
    assert patch.zone_ids == ['six']
    assert patch.constraints is None


def test_old_misclassified_arrangement_constraint_is_removed_on_amendment(monkeypatch):
    respond(monkeypatch, [], {})
    state = TaskState().amended(TaskPatch(
        operation='place', scope='zones', zone_ids=['six'], plant_kind='mixed',
        quantity=60, arrangement='building_contour', species_mode='automatic',
        constraints=['вдоль зданий']), 'previous')
    patch, _ = interpret_task('Деревья и кустарники сам выбери', state, [], CONTEXT, 'local')
    retained = state.amended(patch, 'amend')
    assert retained.values.constraints == []


def test_supported_constraint_is_recovered_when_model_omits_it(monkeypatch):
    respond(monkeypatch, [], {}, 'new_task')
    patch, _ = interpret_task('Посади деревья с соблюдением нормативных отступов', TaskState(), [], CONTEXT, 'local')
    assert patch.constraints == ['с соблюдением нормативных отступов']


def test_negative_and_numeric_constraints_are_preserved_when_model_omits_them(monkeypatch):
    respond(monkeypatch, [], {}, 'new_task')
    patch, _ = interpret_task('Посади деревья, соблюдая нормативные отступы и не ближе 10 метров', TaskState(), [], CONTEXT, 'local')
    assert patch.constraints == ['соблюдая нормативные отступы', 'не ближе 10 метров']

    patch, _ = interpret_task('Не соблюдать нормативные отступы', TaskState(), [], CONTEXT, 'new_task')
    assert patch.constraints == ['Не соблюдать нормативные отступы']

    patch, _ = interpret_task('Не соблюдая нормативные отступы', TaskState(), [], CONTEXT, 'new_task')
    assert patch.constraints == ['Не соблюдая нормативные отступы']


def test_negative_distance_does_not_hide_the_positive_zone_reference(monkeypatch):
    respond(monkeypatch, [], {}, response={'zone_numbers': [6]})
    context = {**CONTEXT, 'zones': [{'id': 'six', 'label': 'Допустимая область 6', 'number': 6}]}
    patch, _ = interpret_task(
        'На участке 6 посадите 60 деревьев вдоль зданий, не ближе 10 метров',
        TaskState(), [], context, 'local'
    )
    assert patch.zone_ids == ['six']
    assert patch.constraints == ['не ближе 10 метров']


def test_generic_plant_kind_is_not_misclassified_as_specified_species(monkeypatch):
    respond(monkeypatch, ['species_mode'], {'species_mode': 'specified'})
    patch, _ = interpret_task(
        'На участке 6 посадите 60 деревьев вдоль зданий, не ближе 10 метров',
        TaskState(), [], {**CONTEXT, 'zones': [{'id': 'six', 'label': 'Допустимая область 6', 'number': 6}]}, 'local'
    )
    assert patch.species_mode == 'automatic'
    assert patch.species_revision_ids is None
    assert patch.constraints == ['не ближе 10 метров']


def test_named_species_is_recovered_when_model_omits_its_id(monkeypatch):
    respond(monkeypatch, ['species_mode'], {'species_mode': 'specified'})
    patch, _ = interpret_task('Посадите дуб', TaskState(), [], CONTEXT, 'local')
    assert patch.species_mode == 'specified'
    assert patch.species_revision_ids == ['oak@1']


def test_model_cannot_forge_or_remove_constraint_evidence(monkeypatch):
    state = TaskState().amended(TaskPatch(operation='place', scope='zones', zone_ids=['six'],
                                          plant_kind='tree', quantity=20, arrangement='area',
                                          species_mode='automatic', constraints=['нормативные отступы']), 'first')
    respond(monkeypatch, ['constraints'], {'constraints': []})
    patch, _ = interpret_task('Породы подберите сами', state, [], CONTEXT, 'local')
    assert 'constraints' not in patch.model_fields_set
    assert state.amended(patch, 'amend').values.constraints == ['нормативные отступы']

    respond(monkeypatch, ['constraints'], {'constraints': ['отступ 9 метров']})
    with pytest.raises(ValueError, match='source message'):
        interpret_task('Породы подберите сами', state, [], CONTEXT, 'local')


def test_multiple_zone_references_are_not_narrowed_to_one(monkeypatch):
    respond(monkeypatch, ['zone_ids'], {'zone_ids': ['six']}, response={'zone_numbers': [6, 5], 'zone_selection': 'all'})
    context = {**CONTEXT, 'zones': [
        {'id': 'five', 'label': 'Допустимая область 5', 'number': 5},
        {'id': 'six', 'label': 'Допустимая область 6', 'number': 6},
    ]}
    patch, _ = interpret_task('Для посадки: Допустимая область 6 и участок 5', TaskState(), [], context, 'local')
    assert patch.zone_ids == ['six', 'five']


def test_compact_multiple_zone_references_are_not_narrowed_to_one(monkeypatch):
    respond(monkeypatch, ['zone_ids'], {'zone_ids': ['six']}, response={'zone_numbers': [6, 5], 'zone_selection': 'all'})
    context = {**CONTEXT, 'zones': [
        {'id': 'five', 'label': 'Допустимая область 5', 'number': 5},
        {'id': 'six', 'label': 'Допустимая область 6', 'number': 6},
    ]}
    patch, _ = interpret_task('Посади на участке 6 и 5', TaskState(), [], context, 'local')
    assert patch.zone_ids == ['six', 'five']


def test_compact_reference_after_exact_label_is_not_narrowed(monkeypatch):
    respond(monkeypatch, ['zone_ids'], {'zone_ids': ['six']}, response={'zone_numbers': [6, 5], 'zone_selection': 'all'})
    context = {**CONTEXT, 'zones': [
        {'id': 'five', 'label': 'Допустимая область 5', 'number': 5},
        {'id': 'six', 'label': 'Допустимая область 6', 'number': 6},
    ]}
    patch, _ = interpret_task('Посади на Допустимая область 6 и 5', TaskState(), [], context, 'local')
    assert patch.zone_ids == ['six', 'five']


def test_or_zone_reference_is_not_silently_expanded(monkeypatch):
    respond(monkeypatch, ['zone_ids'], {'zone_ids': ['six']}, response={'zone_numbers': [6, 5], 'zone_selection': 'either'})
    context = {**CONTEXT, 'zones': [
        {'id': 'five', 'label': 'Допустимая область 5', 'number': 5},
        {'id': 'six', 'label': 'Допустимая область 6', 'number': 6},
    ]}
    patch, _ = interpret_task('Посади на участке 6 или 5', TaskState(), [], context, 'local')
    assert patch.scope == 'zones' and patch.zone_ids == []

    patch, _ = interpret_task('Посади на Допустимая область 6 или 5', TaskState(), [], context, 'local')
    assert patch.scope == 'zones' and patch.zone_ids == []

    patch, _ = interpret_task('Посади на участке 6 или участок 5', TaskState(), [], context, 'local')
    assert patch.scope == 'zones' and patch.zone_ids == []


def test_invented_constraint_evidence_is_not_accepted(monkeypatch):
    def output(*args, **kwargs):
        return {'message': {'content': json.dumps({'intent': 'amend', 'stated_constraints': ['отступ 9 метров'], 'changes': []})}}
    monkeypatch.setattr(local, 'local_json', output)
    with pytest.raises(ValueError, match='source message'):
        interpret_task('Посадите деревья', TaskState(), [], CONTEXT, 'local')


def test_number_alias_never_overrides_negated_reference(monkeypatch):
    respond(monkeypatch, ['zone_ids'], {'zone_ids': ['five']})
    context = {**CONTEXT, 'zones': [{'id': 'five', 'label': 'Допустимая область 5', 'number': 5}, {'id': 'six', 'label': 'Допустимая область 6', 'number': 6}]}
    patch, _ = interpret_task('Не участок 6', TaskState(), [], context, 'local')
    assert patch.zone_ids == ['five']


def respond(monkeypatch, fields, values, intent="amend", response=None):
    seen = []
    def output(path, body, **kwargs):
        seen.append(body)
        payload = {"intent": intent, "changes": [{"field": field, "value": values.get(field)} for field in fields], "question": None}
        payload.update(response or {})
        return {"message": {"content": json.dumps(payload)}}
    monkeypatch.setattr(local, "local_json", output)
    return seen


def test_only_explicit_amendment_fields_apply_and_state_is_sent(monkeypatch):
    state = TaskState().amended(TaskPatch(zone_ids=["six"], quantity=100, arrangement="building_contour"), "first")
    seen = respond(monkeypatch, ["species_mode", "species_revision_ids"], {"species_mode": "specified", "species_revision_ids": ["oak@1"], "quantity": None, "zone_ids": None, "arrangement": "area"})
    patch, _ = interpret_task("Дуб", state, [], CONTEXT, "local")
    updated = state.amended(patch, "oak")
    assert updated.values.quantity == 100
    assert updated.values.zone_ids == ["six"]
    assert updated.values.arrangement == "building_contour"
    assert updated.values.species_revision_ids == ["oak@1"]
    assert '"quantity": 100' in seen[0]["messages"][0]["content"]
    assert seen[0]["messages"][-1]["content"] == "Дуб"


def test_history_uses_original_message_not_tool_status(monkeypatch):
    seen = respond(monkeypatch, [], {}, "discuss")
    records = [{"kind": "message", "payload": {"content": {"role": "assistant", "text": "Вдоль зданий"}}},
               {"kind": "tool_event", "payload": {"content": {"status": "declined"}}}]
    interpret_task("Почему?", TaskState(), records, CONTEXT, "local")
    assert seen[0]["messages"][1]["content"] == "Вдоль зданий"
    assert len(seen[0]["messages"]) == 3


@pytest.mark.parametrize("fields,values", [(["zone_ids"], {"zone_ids": ["invented"]}), (["species_revision_ids"], {"species_revision_ids": ["invented"]}), (["unknown"], {})])
def test_invalid_references_never_enter_task(monkeypatch, fields, values):
    respond(monkeypatch, fields, values)
    with pytest.raises(ValueError):
        interpret_task("Уточнение", TaskState(), [], CONTEXT, "local")


def test_discussion_does_not_rewrite_task_even_if_model_proposes_fields(monkeypatch):
    respond(monkeypatch, ["quantity"], {"quantity": 10}, "discuss")
    patch, _ = interpret_task("Почему не 10?", TaskState(), [], CONTEXT, "local")
    assert patch.model_fields_set == set()


def test_exact_zone_name_beats_wrong_list_index(monkeypatch):
    respond(monkeypatch, ["zone_ids"], {"zone_ids": ["fifth"]})
    context = {**CONTEXT, "zones": [{"id": "fifth", "label": "Допустимая область 5", "number": 6}, {"id": "six", "label": "Допустимая область 6", "number": 7}]}
    patch, _ = interpret_task("Ну, видимо, допустимая область 6", TaskState(), [], context, "local")
    assert patch.zone_ids == ["six"]
    assert patch.scope == "zones"


def test_explicit_selection_classification_is_not_lost_outside_change_list(monkeypatch):
    def output(*args, **kwargs):
        return {"message": {"content": json.dumps({"intent": "amend", "operation": "edit", "plant_kind": "tree", "scope": "selection", "changes": [], "question": None})}}
    monkeypatch.setattr(local, "local_json", output)
    patch, _ = interpret_task("Измените эти деревья", TaskState(), [], CONTEXT, "local")
    assert patch.scope == "selection" and patch.operation == "edit"


def test_explicit_edit_action_replaces_previous_action(monkeypatch):
    def output(*args, **kwargs):
        return {"message": {"content": json.dumps({"intent": "amend", "operation": "edit", "edit_action": "move", "changes": [{"field": "move_dx_m", "value": 2.5}], "question": None})}}
    monkeypatch.setattr(local, "local_json", output)
    state = TaskState().amended(TaskPatch(operation="edit", edit_action="lock", scope="objects", object_ids=["one"]), "lock")
    patch, _ = interpret_task("Переместите на 2.5 метра по X", state, [], CONTEXT, "local")
    state = state.amended(patch, "move")
    assert state.values.edit_action == "move"
    assert state.values.move_dx_m == 2.5
    assert state.values.object_ids == ["one"]


def test_delegated_spatial_fill_road_and_focus_intent_is_preserved(monkeypatch):
    respond(monkeypatch, [], {}, "new_task", response={
        "operation": "place", "plant_kind": "tree", "species_mode": "automatic",
    })
    patch, _ = interpret_task(
        "С краю зону сам выбери любую для теста и засей там на твоё усмотрение деревья вдоль улицы, потом приблизь меня",
        TaskState(), [], CONTEXT, "local"
    )
    assert patch.scope == "zones"
    assert patch.zone_ids == []
    assert patch.spatial_anchor == "edge"
    assert patch.alignment_target == "road"
    assert patch.arrangement == "road_edges"
    assert patch.quantity is None
    assert patch.quantity_mode == "fill_available"
    assert patch.species_mode == "automatic"
    assert patch.post_action == "focus_map"


def test_delegated_spatial_fill_does_not_create_readiness_question(monkeypatch):
    respond(monkeypatch, [], {}, "new_task", response={
        "operation": "place", "plant_kind": "tree", "species_mode": "automatic",
    })
    patch, _ = interpret_task(
        "С краю зону сам выбери и засей деревья вдоль улицы",
        TaskState(), [], CONTEXT, "local"
    )
    from app.agent_readiness import next_question
    assert next_question(TaskState().amended(patch, "request")) is None
