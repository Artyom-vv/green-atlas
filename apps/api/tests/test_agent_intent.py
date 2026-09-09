from app.agent_runtime.contracts import Preference
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.planner import LocalPlanner
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.semantic_guardrails import infer_semantic_signals
from app import planning_assistant as local


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
