from app.agent_runtime.contracts import Preference
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.planner import LocalPlanner
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.semantic_guardrails import explicit_target_count, infer_semantic_signals
from app import planning_assistant as local
import json
import pytest


PROJECT_ZONES = {
    "zones": [
        {"id": "manual", "label": "Аудит Зарядье"},
        {"id": "area-1", "label": "Допустимая область 1", "number": 1},
        {"id": "area-2", "label": "Допустимая область 2", "number": 2},
        {"id": "area-3", "label": "Допустимая область 3", "number": 3},
        {"id": "area-4", "label": "Допустимая область 4", "number": 4},
        {"id": "area-5", "label": "Допустимая область 5", "number": 5},
    ]
}


def test_compiler_preserves_delegated_scope_and_density_preference(monkeypatch):
    response = {
        "message": {"content": '{"operation":"place","target_count":70,"plant_kind":"tree",'
        '"arrangement":"building_groves","scope_mode":"delegated","zone_labels":[],"object_ids":[],'
        '"species_ids":[],"delegations":[{"slot":"species","strategy":"agent","reason":"сам выбери"}],'
        '"preferences":[{"key":"density","value":"high","source_text":"плотно"}],'
        '"hard_constraints":[],"unresolved_requirements":[],"post_action":null}'}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    result = IntentCompiler("qwen3.5:4b").compile(
        "Выбери участок, где получится плотно рассадить 70 деревьев. Породу выбери сам. Посади группами вдоль зданий."
    )
    assert result.scope_mode == "delegated"
    assert result.explicit_zone_ids == []
    assert result.goal.target_count == 70
    assert Preference(key="density", value="high", source_text="плотно") in result.preferences
    assert result.hard_constraints == []
    assert {item.slot for item in result.delegations} == {"scope", "species"}


def test_compiler_does_not_turn_unknown_model_rule_into_a_hard_constraint(monkeypatch):
    response = {
        "message": {"content": '{"operation":"place","target_count":20,"plant_kind":"tree",'
        '"arrangement":"building_contour","scope_mode":"explicit","zone_labels":["Участок 1"],"object_ids":[],'
        '"species_ids":[],"delegations":[],"preferences":[],"hard_constraints":['
        '{"rule_id":"invented-rule","source_text":"не ближе 10 метров","policy_owned":false}],'
        '"unresolved_requirements":[],"post_action":null}'}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    result = IntentCompiler("qwen3.5:4b").compile(
        "На участке 1 посадить 20 деревьев вдоль зданий, не ближе 10 метров."
    )
    assert result.hard_constraints == []
    assert "не ближе 10 метров" in result.unresolved_requirements


def test_semantic_guardrail_ignores_zone_words_when_placement_is_explicit():
    signals = infer_semantic_signals(
        "Выбери подходящий участок сам и подготовь плотную посадку деревьев вдоль зданий."
    )
    assert signals.operations == ("place",)
    assert signals.plant_kind == "tree"
    assert signals.delegations == ("scope",)


def test_compiler_restores_explicit_scope_and_species_delegation(monkeypatch):
    response = {
        "message": {"content": '{"operation":"place","target_count":20,"plant_kind":"tree",'
        '"arrangement":"building_contour","scope_mode":"explicit","zone_labels":[],"object_ids":[],'
        '"species_ids":[],"delegations":[],"preferences":[],"hard_constraints":[],'
        '"unresolved_requirements":[],"post_action":null}'}
    }
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    result = IntentCompiler("qwen3.5:4b").compile(
        "Выбери подходящий участок сам. Породу выбери сам и посади 20 деревьев вдоль зданий."
    )
    assert result.scope_mode == "delegated"
    assert {item.slot for item in result.delegations} == {"scope", "species"}
    assert "scope_mode:explicit->delegated" in result.evidence.corrections


def test_compiler_rejects_nonexistent_explicit_zone_instead_of_using_all_duplicates(monkeypatch):
    response = {
        "message": {"content": '{"operation":"place","target_count":6,"plant_kind":"tree",'
        '"arrangement":"building_contour","scope_mode":"explicit",'
        '"zone_labels":["Допустимая область 1","Допустимая область 2",'
        '"Допустимая область 3","Допустимая область 4","Допустимая область 5"],'
        '"object_ids":[],"species_ids":[],"delegations":[],"preferences":[],'
        '"hard_constraints":[],"unresolved_requirements":[],"post_action":null}'}
    }
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    try:
        IntentCompiler("qwen3.5:4b").compile(
            "На участке Допустимая область 6 посади 6 деревьев вдоль зданий.",
            project_context=PROJECT_ZONES,
        )
    except ValueError as error:
        assert "номер 6 отсутствует" in str(error)
        assert "Доступны" in str(error)
    else:
        raise AssertionError("An invalid explicit zone must never become a multi-zone scope")


def test_compiler_keeps_one_exact_numbered_zone(monkeypatch):
    response = {
        "message": {"content": '{"operation":"place","target_count":6,"plant_kind":"tree",'
        '"arrangement":"building_contour","scope_mode":"explicit",'
        '"zone_labels":["Допустимая область 5"],"object_ids":[],"species_ids":[],'
        '"delegations":[],"preferences":[],"hard_constraints":[],'
        '"unresolved_requirements":[],"post_action":null}'}
    }
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    result = IntentCompiler("qwen3.5:4b").compile(
        "На участке Допустимая область 5 посади 6 деревьев вдоль зданий.",
        project_context=PROJECT_ZONES,
    )
    assert result.explicit_zone_ids == ["area-5"]


def test_compiler_repairs_destructive_model_label_and_preserves_mixed_request(monkeypatch):
    response = {
        "message": {"content": '{"operation":"delete","target_count":10,"plant_kind":"tree",'
        '"arrangement":"area","scope_mode":"delegated","zone_labels":[],"object_ids":[],'
        '"species_ids":[],"delegations":[],"preferences":[{"key":"density","value":"high"}],'
        '"hard_constraints":[],"unresolved_requirements":[],"post_action":null}'}
    }
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    result = IntentCompiler("qwen3.5:4b").compile(
        "Выбери подходящий участок. Подготовь 10 растений: деревья и кустарники, состав выбери сам, размести группами вдоль зданий, плотнее."
    )
    assert result.goal.operation == "place"
    assert result.plant_kind == "mixed"
    assert result.arrangement == "building_groves"
    assert result.evidence.corrections == [
        "operation:delete->place",
        "plant_kind:tree->mixed",
        "arrangement:area->building_groves",
    ]


def test_semantic_guardrail_rejects_conflicting_actions(monkeypatch):
    response = {
        "message": {"content": '{"operation":"place","target_count":10,"plant_kind":"tree",'
        '"arrangement":"area","scope_mode":"delegated","zone_labels":[],"object_ids":[],'
        '"species_ids":[],"delegations":[],"preferences":[],"hard_constraints":[],'
        '"unresolved_requirements":[],"post_action":null}'}
    }
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    try:
        IntentCompiler("qwen3.5:4b").compile("Удалить посадки и добавить новые деревья")
    except ValueError as error:
        assert "смешаны разные действия" in str(error)
    else:
        raise AssertionError("Conflicting actions must not be guessed")


def test_local_planner_returns_only_a_registered_typed_decision(monkeypatch):
    response = {"message": {"content": '{"action":"tool","tool":{"name":"find_zone_candidates", "arguments":{}}}'}}
    captured = {}

    def fake_local_json(*args, **_kwargs):
        captured.update(args[1])
        return response

    monkeypatch.setattr(local, "local_json", fake_local_json)
    decision = LocalPlanner("qwen3.5:4b", CapabilityRegistry())({"run": {"status": "running"}})
    assert decision.action == "tool"
    assert decision.tool.name == "find_zone_candidates"
    assert captured["format"]["required"] == ["action"]
    assert captured["format"]["$defs"]["ToolCall"]["properties"]["name"]["enum"]
    assert "prepare_placement" in captured["format"]["$defs"]["ToolCall"]["properties"]["name"]["enum"]


@pytest.mark.parametrize("model_scope,model_labels", [("explicit", ["Допустимая область 1"]), ("delegated", [])])
def test_source_number_preserves_existing_explicit_zone_despite_model_guess(monkeypatch, model_scope, model_labels):
    response = {"message": {"content": json.dumps({
        "operation": "place", "target_count": 6, "plant_kind": "tree", "arrangement": "area",
        "scope_mode": model_scope, "zone_labels": model_labels,
    })}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    result = IntentCompiler("model").compile("На участке Допустимая область 5 посади 6 деревьев.", project_context=PROJECT_ZONES)
    assert result.explicit_zone_ids == ["area-5"]
    assert result.scope_mode == "explicit"


def test_missing_source_number_cannot_be_hidden_by_delegated_model_scope(monkeypatch):
    response = {"message": {"content": json.dumps({
        "operation": "place", "target_count": 6, "plant_kind": "tree", "scope_mode": "delegated",
    })}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    with pytest.raises(ValueError, match="номер 999 отсутствует"):
        IntentCompiler("model").compile("На участке Допустимая область 999 посади 6 деревьев.", project_context=PROJECT_ZONES)


@pytest.mark.parametrize("model_count", [None, 5, 10])
def test_source_target_survives_model_omission_and_does_not_capture_zone_or_horizon(monkeypatch, model_count):
    response = {"message": {"content": json.dumps({
        "operation": "place", "target_count": model_count, "plant_kind": "tree", "scope_mode": "explicit",
        "zone_labels": ["Допустимая область 5"],
    })}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    result = IntentCompiler("model").compile(
        "На участке Допустимая область 5 посади 70 деревьев, горизонт 10 лет и отступ 6 метров.", project_context=PROJECT_ZONES)
    assert result.goal.target_count == 70
    assert result.explicit_zone_ids == ["area-5"]


def test_latest_capacity_answer_changes_count_but_keeps_placement_operation(monkeypatch):
    response = {"message": {"content": json.dumps({
        "operation": "edit", "target_count": 70, "plant_kind": "tree", "scope_mode": "explicit",
        "zone_labels": ["Допустимая область 5"],
    })}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    turns = ["На участке Допустимая область 5 посади 70 деревьев вдоль зданий.",
        "Измени количество растений на 1. Сохрани участок, схему и породы."]
    result = IntentCompiler("model").compile("\n\nДополнение пользователя: ".join(turns),
        source_turns=turns, project_context=PROJECT_ZONES)
    assert result.goal.operation == "place"
    assert result.goal.target_count == 1
    assert result.arrangement == "building_contour"
    assert result.explicit_zone_ids == ["area-5"]
    assert result.source_turns == turns


def test_ambiguous_component_counts_require_one_total():
    with pytest.raises(ValueError, match="одно общее количество"):
        explicit_target_count("Посади 6 деревьев и 4 кустарника")
    assert explicit_target_count("Посади 10 растений: 6 деревьев и 4 кустарника") == 10
    assert explicit_target_count("Горизонт 10 лет, участок 5, отступ 6 метров") is None


@pytest.mark.parametrize("text", [
    "Проверь текущий план посадок и покажи найденные ограничения.",
    "Покажи историю изменений участка 2.",
    "Посмотри результаты удаления посадок.",
    "Проанализируй размещение деревьев и изменение границ участка.",
    "Изучи отчёт о посадках и закреплении растений.",
    "Подбери подходящие породы деревьев для участка Допустимая область 5.",
    "Выбери подходящие породы кустарников для зоны 2.",
    "Сколько посадок на участке 2?",
])
def test_read_actions_do_not_treat_their_object_nouns_as_mutation_commands(monkeypatch, text):
    response = {"message": {"content": json.dumps({
        "operation": "delete", "scope_mode": "project",
    })}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    assert infer_semantic_signals(text).operations == ("inspect",)
    result = IntentCompiler("model").compile(text, project_context=PROJECT_ZONES)
    assert result.goal.operation == "inspect"


@pytest.mark.parametrize("text,operation", [
    ("Удали участок 2.", "zones"),
    ("Участок 2 удали.", "zones"),
    ("Зону 2 удали.", "zones"),
    ("Участок 2 с деревьями удали.", "zones"),
    ("Удалить выбранную зону с деревьями.", "zones"),
    ("Измени границу участка 2.", "zones"),
    ("Переименуй участок 2 в «Сад».", "zones"),
    ("Переименуйте зону 2 в «Сад».", "zones"),
    ("Переименовать область 2 в «Сад».", "zones"),
    ("Добавь новый участок.", "zones"),
    ("Создай новую зону для кустарников.", "zones"),
    ("На участке 2 удали 1 дерево.", "delete"),
    ("Удали деревья на участке 2.", "delete"),
    ("Деревья на участке 2 удали.", "delete"),
    ("Удалить посадки из зоны 2.", "delete"),
    ("Посади 3 дерева на участке 2.", "place"),
    ("Заполни участок 2 деревьями.", "place"),
    ("Перемести дерево на участке 2 по X на 2 метра.", "edit"),
])
def test_action_is_bound_to_its_direct_object_not_a_zone_qualifier(text, operation):
    assert infer_semantic_signals(text).operations == (operation,)


@pytest.mark.parametrize("text", [
    "Удалить посадки и добавить новые деревья.",
    "Посади дерево и удали кустарник.",
    "Проверь план и удали дерево.",
    "Удали участок 2 и посади дерево на участке 3.",
])
def test_real_mixed_commands_remain_ambiguous(monkeypatch, text):
    response = {"message": {"content": json.dumps({"operation": "place", "scope_mode": "project"})}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    with pytest.raises(ValueError, match="смешаны разные действия"):
        IntentCompiler("model").compile(text, project_context=PROJECT_ZONES)


def test_zone_deletion_cannot_be_compiled_as_deleting_its_plants(monkeypatch):
    response = {"message": {"content": json.dumps({
        "operation": "delete", "scope_mode": "explicit", "zone_labels": ["Допустимая область 2"],
    })}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    result = IntentCompiler("model").compile("Удали участок 2.", project_context=PROJECT_ZONES)
    assert result.goal.operation == "zones"
    # A lightweight label list alone cannot authorize a zone mutation.
    assert result.zone is None and result.unresolved_requirements
    assert not result.explicit_object_ids


@pytest.mark.parametrize("text", [
    "Не удаляй деревья.",
    "Деревья не удаляй.",
    "Не надо удалять деревья.",
    "Не нужно, пожалуйста, удалять деревья.",
    "Удалять не надо.",
    "Удалять нельзя.",
    "Не закрепляй дерево.",
    "Не посади дерево.",
    "Не удаляй деревья и посади кустарник.",
])
def test_negated_commands_cannot_authorize_a_model_supplied_mutation(monkeypatch, text):
    response = {"message": {"content": json.dumps({"operation": "delete", "scope_mode": "project"})}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: response)
    with pytest.raises(ValueError, match="отрицание действия"):
        IntentCompiler("model").compile(text, project_context=PROJECT_ZONES)
