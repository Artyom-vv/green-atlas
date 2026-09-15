import json
from contextlib import closing

import pytest

from app import planning_assistant as local
from app.agent_runtime.contracts import AgentDecision, AgentIntent, EditIntent, Goal, ToolCall
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.gateway import ToolGateway
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.intent_sources import is_task_argument_reference
from app.agent_runtime.policy import assess_requirements
from app.agent_runtime.store import AgentRunStore
from test_agent_intent_safety import seeded


TEXT = "Перемести все 3 дерева на участке Допустимая область 8: смещение X +0.1 м, Y 0 м."
CONTEXT = {"zones": [{"id": "west", "label": "Допустимая область 8", "number": 8}]}


def test_real_move_command_and_zone_are_not_model_authored_requirements(monkeypatch):
    source = TEXT.split(":")[0]
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["Допустимая область 8"],
        "edit": {"action": "move", "move_dx_m": 0.1, "move_dy_m": 0}, "target_count": 3,
        "hard_constraints": [{"rule_id": rule, "source_text": source, "policy_owned": True}
            for rule in ["pp743-3.6.3-building", "pp743-3.6.3-road-edge"]],
        "unresolved_requirements": ["Допустимая область 8"]}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(TEXT, project_context=CONTEXT)
    assert intent.goal.target_count == 3 and intent.explicit_zone_ids == ["west"]
    assert intent.edit == EditIntent(action="move", move_dx_m=0.1, move_dy_m=0)
    assert not intent.hard_constraints and not intent.unresolved_requirements
    assert assess_requirements(intent).status == "supported"


def test_real_move_isolated_vector_clause_is_a_typed_parameter_not_a_requirement(monkeypatch):
    text = "Перемести все 3 дерева на участке Допустимая область 1: смещение X +0.1 м, Y 0 м."
    source = "смещение X +0.1 м, Y 0 м"
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["Допустимая область 1"],
        "edit": {"action": "move", "move_dx_m": 0.1, "move_dy_m": 0}, "target_count": 3,
        "hard_constraints": [{"rule_id": "pp743-3.6.3-building", "source_text": source, "policy_owned": True}],
        "unresolved_requirements": [source]}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(text, project_context={"zones": [
        {"id": "west", "label": "Допустимая область 1", "number": 1}]})
    assert intent.edit == EditIntent(action="move", move_dx_m=0.1, move_dy_m=0)
    assert intent.goal.target_count == 3 and intent.explicit_zone_ids == ["west"]
    assert not intent.hard_constraints and not intent.unresolved_requirements
    assert assess_requirements(intent).status == "supported"


@pytest.mark.parametrize("fragment", ["смещение X +0.1 м, Y 0 м", "смещение X -0.2 м и Y +0.3 м",
    "смещение по оси X = 2 м", "по осям X: 1 м и Y: 2 м"])
def test_isolated_source_vector_uses_only_closed_parameter_labels(fragment):
    assert is_task_argument_reference(fragment, text=f"Перемести дерево: {fragment}.", operation="edit", zone_labels=[], object_ids=[])


@pytest.mark.parametrize("fragment", ["не смещение X +0.1 м, Y 0 м", "смещение X +0.1 м, Y 0 м кроме дороги",
    "смещение X +0.1 м, Y 0 м, отступ 10 м", "смещение X +0.1 м, Y 0 м, радиус 3 м",
    "смещение X +0.1 м, Y 0 м, 3", "смещение приблизительно X +0.1 м, Y 0 м"])
def test_vector_clause_does_not_hide_negation_qualifiers_or_extra_numbers(fragment):
    assert not is_task_argument_reference(fragment, text=f"Перемести 3 дерева: {fragment}.", operation="edit", zone_labels=[], object_ids=[])


def test_non_move_action_and_values_not_in_source_do_not_authorize_vector_clause():
    assert not is_task_argument_reference("смещение X +0.1 м", text="Закрепи дерево: смещение X +0.1 м.",
        operation="edit", zone_labels=[], object_ids=[])
    assert not is_task_argument_reference("смещение X +0.2 м", text="Перемести дерево: смещение X +0.1 м.",
        operation="edit", zone_labels=[], object_ids=[])


@pytest.mark.parametrize("fragment", ["Допустимая область 8", TEXT.split(":")[0], TEXT])
def test_closed_task_argument_phrases_are_recognized_whole(fragment):
    assert is_task_argument_reference(fragment, text=TEXT, operation="edit", zone_labels=["Допустимая область 8"], object_ids=[])


@pytest.mark.parametrize("condition", [
    "не ближе 10 метров", "сохрани вид на памятник", "не соблюдай отступы", "соблюдай отступы кроме дороги",
    "соблюдай отступы 3 метра", "не перемещай деревья из охранной зоны", "оставь Допустимая область 8 свободной",
])
def test_real_unknown_numeric_and_negative_conditions_cannot_be_erased(monkeypatch, condition):
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["Допустимая область 8"],
        "hard_constraints": [{"rule_id": "pp743-3.6.3-building", "source_text": condition, "policy_owned": True}],
        "unresolved_requirements": [condition]}
    source = TEXT + " " + condition
    assert not is_task_argument_reference(condition, text=source, operation="edit", zone_labels=["Допустимая область 8"], object_ids=[])
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    try:
        intent = IntentCompiler("test").compile(source, project_context=CONTEXT)
    except ValueError:
        return  # An explicit negative command is rejected even earlier.
    assert assess_requirements(intent).status == "unsupported"
    assert condition in intent.unresolved_requirements or any(item.source_text == condition for item in intent.hard_constraints)


def test_domain_species_refusal_becomes_one_question_and_keeps_intent(tmp_path):
    app, project = seeded()
    project.plan.objects[0].x = 2
    project.plan.objects[0].y = 2
    app.repository.save(project)
    project = app.get(project.id)
    species = "betula-pendula@2026-08-28.1"
    intent = AgentIntent(raw_text="Замени породу выделенного дерева на берёзу", goal=Goal(operation="edit", target_count=1),
        scope_mode="explicit", explicit_object_ids=["tree-a"], edit=EditIntent(action="species"), plant_kind="tree", species_ids=[species])
    calls = []
    def choose(_context):
        calls.append(1)
        assert len(calls) == 1, "A concrete domain refusal must ask rather than loop"
        return AgentDecision(action="tool", tool=ToolCall(name="prepare_existing_change", arguments={
            "base_plan_version": project.plan.version, "object_ids": ["tree-a"], "operation": "edit", "edit_action": "species",
            "species_revision_ids": [species], "quantity": 1, "plant_kind": "tree"}))
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id, choose)
        assert result.state.status == "waiting_question" and result.state.pending_question["slot"] == "preview"
        assert result.state.last_result.error.code == "DOMAIN_PREVIEW_BLOCKED"
        assert result.state.last_result.preview_refusal.blocked_count == 1
        assert result.state.last_result.preview_refusal.reason_codes
        assert result.state.intent == intent and result.state.pending_approval is None
        assert result.state.last_result.verification.status == "rejected"
        assert app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize("text,expected", [
    ("Сними закрепление всех 3 деревьев на участке Допустимая область 8.", []),
    ("Закрепи все 3 берёзы на участке Допустимая область 8.", ["betula-pendula@2026-08-28.1"]),
    ("Замени породу 3 деревьев на рябину на участке Допустимая область 8.", ["sorbus-aucuparia@2026-08-28.1"]),
    ("Посади 3 липы на участке Допустимая область 8.", ["tilia-cordata@2026-08-28.1"]),
    ("Посади 3 дерева на участке Допустимая область 8, породу выбери сам.", []),
    ("Закрепи Betula pendula на участке Допустимая область 8.", ["betula-pendula@2026-08-28.1"]),
    ("Закрепи betula-pendula@2026-08-28.1 на участке Допустимая область 8.", ["betula-pendula@2026-08-28.1"]),
])
def test_species_filters_are_source_bound_and_hallucinated_ids_do_not_survive(monkeypatch, text, expected):
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["Допустимая область 8"],
        "species_ids": ["tilia-cordata@2026-08-28.1", "acer-platanoides@2026-08-28.1", "quercus-robur@2026-08-28.1"]}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(text, project_context=CONTEXT)
    assert intent.species_ids == expected
    if "выбери сам" in text:
        assert any(item.slot == "species" for item in intent.delegations)


def test_species_named_zone_does_not_create_an_unrequested_plant_filter(monkeypatch):
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["Липа"], "species_ids": ["tilia-cordata@2026-08-28.1"]}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile("Закрепи деревья на участке Липа", project_context={"zones": [{"id": "west", "label": "Липа"}]})
    assert intent.species_ids == []


@pytest.mark.parametrize("exclusion", ["кроме берёзы", "исключая берёзу", "без берёзы", "за исключением взрослых берёз",
    "только не берёзу", "не Betula pendula", "кроме дубов и берёз"])
def test_source_species_exclusion_cannot_be_inverted_into_an_include_filter(monkeypatch, tmp_path, exclusion):
    app, project = seeded()
    project.planting_zones[0].label = "West"
    app.repository.save(project)
    project = app.get(project.id)
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["West"], "edit": {"action": "lock"},
        "plant_kind": "tree", "species_ids": [], "hard_constraints": [], "unresolved_requirements": []}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(f"Закрепи все деревья на участке West, {exclusion}.",
        project_context={"zones": [{"id": "west", "label": "West"}]})
    assert intent.unresolved_requirements and assess_requirements(intent).status == "unsupported"
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id, lambda _: pytest.fail("No guessing"))
    assert result.state.status == "waiting_question" and result.state.pending_question["slot"] == "requirements"
    assert result.state.pending_approval is None and not app.changes._previews
    assert app.get(project.id).plan.objects == project.plan.objects


def test_species_exclusion_words_inside_zone_label_are_only_a_scope_reference(monkeypatch):
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["Без берёзы"], "edit": {"action": "lock"}}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile("Закрепи деревья на участке Без берёзы.",
        project_context={"zones": [{"id": "west", "label": "Без берёзы"}]})
    assert intent.species_ids == [] and intent.unresolved_requirements == []


def test_literal_history_marker_in_full_zone_label_cannot_replace_requested_species(monkeypatch, tmp_path):
    app, project = seeded()
    label = "Дополнение пользователя: Берёза"
    project.planting_zones[0].label = label
    app.repository.save(project)
    source = f"Замени породу 1 дерева на клён на участке «{label}»."
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": [label],
        "species_ids": ["acer-platanoides@2026-08-28.1"], "edit": {"action": "species"}}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(source, project_context={"zones": [{"id": "west", "label": label}]})
    assert intent.raw_text == source and intent.source_turns == [source]
    assert intent.species_ids == ["acer-platanoides@2026-08-28.1"]
    assert not intent.unresolved_requirements and intent.explicit_zone_ids == ["west"]
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id,
            lambda _: pytest.fail("Source-bound species edit needs no planning guess"))
        assert result.state.status == "waiting_approval"
        from app.agent_runtime.verifier import extract_change_set
        change_set = extract_change_set(result.state.last_result.data, tool_name="prepare_existing_change")
        preview = app.get_change_set_preview(project.id, change_set["id"], change_set["digest"])
        assert len(preview.updates) == 1
        assert preview.updates[0].species_revision_id == "acer-platanoides@2026-08-28.1"
    assert app.get(project.id).plan.objects == project.plan.objects


def test_only_structured_history_allows_quantity_and_axis_amendments():
    from app.agent_runtime.semantic_guardrails import explicit_move_vector, explicit_target_count
    count_turns = ["Посади 6 деревьев.", "Посади 4 дерева."]
    vector_turns = ["Перемести дерево по X 1 м.", "Смещение X 2 м."]
    counts = "\n\nДополнение пользователя: ".join(count_turns)
    vectors = "\n\nДополнение пользователя: ".join(vector_turns)
    with pytest.raises(ValueError, match="одно общее количество"):
        explicit_target_count(counts)
    with pytest.raises(ValueError, match="разные смещения"):
        explicit_move_vector(vectors)
    assert explicit_target_count(counts, source_turns=count_turns) == 4
    assert explicit_move_vector(vectors, source_turns=vector_turns) == {"move_dx_m": 2}


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("model_label", [None, "Выдуманное название"])
def test_quoted_zone_label_is_recovered_before_strict_draft_validation(monkeypatch, operation, model_label):
    from app.agent_interpreter import project_context
    from test_zone_workflow import project_with_contour
    app, project, reference = project_with_contour()
    label = "Тестовый участок агента"
    source = (f'Создай участок "{label}" по контуру source-area.' if operation == "create"
              else f'Переименуй участок east в "{label}".')
    proposed = {"operation": operation, "label": model_label,
        **({"geometry_reference": reference.model_dump(mode="json")} if operation == "create" else {"target_zone_id": "east"})}
    draft = {"operation": "zones", "scope_mode": "explicit", "zone": proposed}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(source, project_context=project_context(project), full_project=project)
    assert intent.zone and not intent.unresolved_requirements
    assert intent.zone.intent.label == intent.zone.draft.label == label
    assert "zone_label:source_bound" in intent.evidence.corrections
    assert app.get(project.id).planting_zones == project.planting_zones


def test_public_requests_cannot_forge_server_recorded_source_turns():
    from pydantic import ValidationError
    from app.agent_runtime.routes import AgentRunCreate, AgentRunAnswer
    for request_type in (AgentRunCreate, AgentRunAnswer):
        with pytest.raises(ValidationError, match="source_turns"):
            request_type(text="Удали участок east.", source_turns=["Другой текст"])


def test_live_rename_source_overrides_model_edit_operation_and_prepares_zone(monkeypatch, tmp_path):
    from app.agent_interpreter import project_context
    from test_zone_workflow import project_with_contour
    app, project, _reference = project_with_contour()
    next(zone for zone in project.planting_zones if zone.id == "east").label = "Допустимая область 3"
    app.repository.save(project)
    source = 'Переименуй участок Допустимая область 3 в "Тестовый участок агента".'
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["Допустимая область 3"],
        "zone": {"operation": "update", "target_zone_id": "east", "label": None}}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(source, project_context=project_context(project), full_project=project)
    assert intent.goal.operation == "zones" and intent.zone is not None and intent.edit is None
    assert intent.zone.draft.label == "Тестовый участок агента" and intent.explicit_zone_ids == ["east"]
    assert intent.evidence.operation == ["zones"]
    assert "operation:edit->zones" in intent.evidence.corrections
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id,
            lambda _: pytest.fail("Explicit rename must use the deterministic zone workflow"))
        assert result.state.status == "waiting_approval"
        assert result.state.pending_approval["kind"] == "planting_zones"
        assert result.state.last_result.name == "prepare_zone_change"
    assert app.get(project.id).planting_zones == project.planting_zones


CAPACITY_SOURCE = "На участке Допустимая область 8 посади 70 деревьев вдоль зданий. Породу выбери сам, отступы соблюдай."
INVENTED_SETBACK_REQUIREMENT = "Пользователь не указал конкретные параметры отступов, только требование их соблюдения согласно нормативам."


def test_live_capacity_brief_does_not_invent_missing_normative_parameters(monkeypatch):
    draft = {"operation": "place", "target_count": 70, "plant_kind": "tree", "scope_mode": "explicit",
        "zone_labels": ["Допустимая область 8"], "arrangement": "building_contour",
        "delegations": [{"slot": "species", "strategy": "agent"}],
        "hard_constraints": [{"rule_id": "pp743-3.6.3-building", "source_text": "отступы соблюдай", "policy_owned": True}],
        "unresolved_requirements": [INVENTED_SETBACK_REQUIREMENT]}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(CAPACITY_SOURCE, project_context=CONTEXT)
    assert intent.goal.target_count == 70 and intent.explicit_zone_ids == ["west"]
    assert intent.arrangement == "building_contour" and not intent.unresolved_requirements
    assert "requirements:source_coverage" in intent.evidence.corrections
    assert {item.rule_id for item in intent.hard_constraints} == {"pp743-3.6.3-building", "pp743-3.6.3-road-edge"}
    assert {item.source_text for item in intent.hard_constraints} == {"отступы соблюдай"}
    assert assess_requirements(intent).status == "supported"


@pytest.mark.parametrize("extra", [
    "Сохрани вид на памятник.", "При разрешении архитектора.", "Не ближе 10 метров.",
    "Соблюдай отступы кроме дороги.", "Кроме берёзы.",
    "Дополнение пользователя: только при разрешении архитектора.",
])
def test_source_coverage_never_discards_a_paraphrased_unknown_condition(monkeypatch, extra):
    draft = {"operation": "place", "target_count": 70, "scope_mode": "explicit", "zone_labels": ["Допустимая область 8"],
        "unresolved_requirements": [INVENTED_SETBACK_REQUIREMENT, "Нужно проверить дополнительное условие пользователя"]}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    intent = IntentCompiler("test").compile(CAPACITY_SOURCE + " " + extra, project_context=CONTEXT)
    assert "Нужно проверить дополнительное условие пользователя" in intent.unresolved_requirements
    assert "requirements:source_coverage" not in intent.evidence.corrections
    assert assess_requirements(intent).status == "unsupported"


def test_bound_placement_source_requires_full_label_and_unmodified_policy():
    from app.agent_runtime.intent_sources import has_only_bound_placement_source
    for source in [CAPACITY_SOURCE.replace("область 8", "область 8 Берёза"),
                   CAPACITY_SOURCE.replace("отступы соблюдай", "отступы соблюдай только от зданий"),
                   CAPACITY_SOURCE.replace("отступы соблюдай", "отступы не соблюдай")]:
        assert not has_only_bound_placement_source(source, source_turns=[source],
            zone_labels=["Допустимая область 8"], object_ids=[])
