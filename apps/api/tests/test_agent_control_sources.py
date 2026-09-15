"""Map targets come from complete source turns, never model scope guesses."""

from dataclasses import replace

import pytest

from app import planning_assistant as local
from app.agent_runtime.contracts import AgentIntent, ControlIntent, Goal, ToolCall
from app.agent_runtime.control_sources import bind_control_source
from app.agent_runtime.gateway import GatewayContext, GatewayPolicy, GatewayRejected, ToolGateway
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.policy import assess_requirements
from app.agent_runtime.registry import CapabilityRegistry
from test_planting_zone_changes import seeded


def compile_control(project, turns):
    return IntentCompiler("unused").compile("\n\n".join(turns), full_project=project, source_turns=turns)


@pytest.mark.parametrize("turns", [
    ["Покажи участок east на карте."],
    ["Покажи на карте участок east."],
    ["Пожалуйста, покажи участок East на карте."],
    ["Покажи участок на карте.", "Участок east"],
    ["Покажи участок на карте.", "east"],
    ["Покажи участок на карте.", "«East»"],
])
def test_exact_target_and_missing_target_answer_do_not_need_model(turns, monkeypatch):
    _app, project = seeded()
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: pytest.fail("A proven control cannot ask the model to choose a target"))
    intent = compile_control(project, turns)
    assert intent.control == ControlIntent(zone_id="east")
    assert intent.explicit_zone_ids == ["east"] and intent.explicit_object_ids == []
    assert intent.goal.operation == "inspect" and intent.post_action is None
    assert intent.selection_reference is None and intent.selection_binding is None
    assert intent.raw_text == "\n\n".join(turns) and intent.source_turns == turns
    assert assess_requirements(intent).status == "supported"


@pytest.mark.parametrize("label", ["Удали", "Выбранный сад", "Сад по берегу", "Сад из сосен", "Участок 9 из сада", "Не показывай", "Покажи"])
def test_command_words_inside_exact_quoted_label_are_only_a_reference(label):
    _app, project = seeded()
    next(zone for zone in project.planting_zones if zone.id == "east").label = label
    next(zone for zone in project.planting_zones if zone.id == "west").label = "Участок 9"
    for turns in [[f"Покажи участок «{label}» на карте."], ["Покажи участок на карте.", f"«{label}»"]]:
        intent = compile_control(project, turns)
        assert intent.control == ControlIntent(zone_id="east")
        assert assess_requirements(intent).status == "supported"


@pytest.mark.parametrize("turns", [
    ["Не показывай, покажи участок east на карте."],
    ["Не покажи участок east на карте."],
    ["Покажи участок east на карте только после одобрения архитектора."],
    ["Покажи участок east на карте и удали участок west."],
    ["Покажи участок east и участок west на карте."],
    ["Покажи участок 99 на карте."],
    ["Покажи участок «Неизвестный сад» на карте."],
    ["Покажи участок на карте только после одобрения архитектора.", "east"],
    ["Покажи участок на карте.", "east и удали west"],
    ["Покажи участок на карте.", "Не выбирай east"],
    ["Покажи участок на карте.", "Покажи замечания проекта"],
    ["Покажи участок на карте.", "Дополнение пользователя: east"],
    ["Покажи участок на карте.", "east и west"],
    ["Покажи участок east на карте и покажи замечания."],
    ["Покажи участок «зона» на карте."],
])
def test_every_turn_is_checked_and_conditions_cannot_disappear(turns):
    _app, project = seeded()
    intent = compile_control(project, turns)
    assert intent.control is not None and intent.control.unsupported_requirements
    assert assess_requirements(intent).status == "unsupported"
    assert intent.source_turns == turns


def test_number_and_numeric_id_are_distinct_source_references():
    _app, project = seeded()
    next(zone for zone in project.planting_zones if zone.id == "east").label = "Допустимая область 8"
    west = next(zone for zone in project.planting_zones if zone.id == "west")
    west.id = "8"
    assert compile_control(project, ["Покажи участок 8 на карте."]).control == ControlIntent(zone_id="east")
    assert compile_control(project, ["Покажи участок ID 8 на карте."]).control == ControlIntent(zone_id="8")


def test_plain_inspection_is_not_implicitly_a_camera_action():
    _app, project = seeded()
    assert bind_control_source("Покажи замечания проекта", project) is None
    with pytest.raises(ValueError, match="соответствует"):
        bind_control_source("Покажи участок east на карте.", project, source_turns=["Покажи участок west на карте."])


def context_for(project, intent):
    return GatewayContext(project_id=project.id, run_id="control-run", expected_state_version=project.state_version,
        allowed_zone_ids=frozenset(intent.explicit_zone_ids), intent=intent,
        policy=GatewayPolicy(allowed_effects=frozenset({"control"}), project_id=project.id))


def test_gateway_prepares_full_facts_but_does_not_execute_the_camera():
    app, project = seeded()
    before = app.get(project.id).model_dump(mode="json")
    intent = compile_control(project, ["Покажи участок east на карте."])
    gateway = ToolGateway(app)
    descriptor = gateway.registry.describe("focus_zone")
    assert descriptor["effect"] == "control" and not descriptor["approval_required"]
    result = gateway.call(context_for(project, intent), ToolCall(name="focus_zone", arguments={"zone_id": "east"}))
    assert result.status == "succeeded" and result.verification.status == "verified"
    assert result.data["zone_id"] == "east" and result.evidence_refs and result.effects == []
    assert app.get(project.id).model_dump(mode="json") == before


def test_gateway_rejects_unproven_scope_and_forged_control_source():
    app, project = seeded()
    intent = compile_control(project, ["Покажи участок east на карте."])
    context = context_for(project, intent)
    gateway = ToolGateway(app)
    call = ToolCall(name="focus_zone", arguments={"zone_id": "east"})
    forged = intent.model_copy(update={"raw_text": "Покажи участок west на карте.", "source_turns": ["Покажи участок west на карте."]})
    for invalid in [replace(context, intent=None), replace(context, allowed_zone_ids=frozenset({"west"})),
                    replace(context, intent=forged), replace(context, policy=GatewayPolicy())]:
        with pytest.raises(GatewayRejected):
            gateway.call(invalid, call)
    with pytest.raises(GatewayRejected):
        gateway.call(context, ToolCall(name="focus_zone", arguments={"zone_id": "west"}))
    assert gateway.call(context, ToolCall(name="focus_zone", arguments={"zone_id": "east", "bounds": [0, 0, 1, 1]})).status == "failed"


def test_gateway_checks_preparation_against_authoritative_full_contour():
    app, project = seeded()
    intent = compile_control(project, ["Покажи участок east на карте."])
    original = CapabilityRegistry().get("focus_zone")
    def forged_executor(application, project_id, request):
        facts = original.executor(application, project_id, request)
        return {**facts, "bounds": [0, 0, 1, 1]}
    gateway = ToolGateway(app, CapabilityRegistry((replace(original, executor=forged_executor),)))
    result = gateway.call(context_for(project, intent), ToolCall(name="focus_zone", arguments={"zone_id": "east"}))
    assert result.status == "failed" and result.error.code == "CONTROL_NOT_VERIFIED"


def test_policy_rejects_compound_typed_effect_even_if_raw_source_looks_valid():
    intent = AgentIntent(raw_text="Покажи участок east на карте.", goal=Goal(operation="place", target_count=3),
        scope_mode="explicit", explicit_zone_ids=["east"], control=ControlIntent(zone_id="east"))
    assert assess_requirements(intent).status == "unsupported"
