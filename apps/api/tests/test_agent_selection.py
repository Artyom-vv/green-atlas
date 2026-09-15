"""Map selection is a versioned source binding, not mutable planner context."""
from app.composition import get_runtime
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException

from app import api, planning_assistant as local
from app.agent_runtime import routes, preview_routes
from app.agent_runtime.contracts import AgentDecision, AgentIntent, Goal, EditIntent, SelectionContext, ToolCall
from app.agent_runtime.engine import AgentEngine, _summarize_data
from app.agent_runtime.gateway import GatewayContext, GatewayPolicy, GatewayRejected, ToolGateway
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.planner import _bind_existing_change_scope
from app.agent_runtime.read_workflow import read_arguments
from app.agent_runtime.selection import bind_selection, selection_reference, selection_problem
from app.agent_runtime.store import AgentRunStore
from app.agent_runtime.verifier import verify_preview_data
from app.contracts import PlanObject
from test_agent_intent_safety import seeded, existing_arguments, placement_arguments


@pytest.fixture
def selected(monkeypatch, tmp_path):
    app, project = seeded()
    project.plan.objects.extend([
        PlanObject(id="tree-b", kind="tree", x=50, y=50, radius=2, planting_zone_id="west"),
        PlanObject(id="shrub", kind="shrub", x=70, y=70, radius=1, planting_zone_id="west")])
    app.repository.save(project)
    project = app.get(project.id)
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    monkeypatch.setattr(get_runtime(), "application", app)
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr(preview_routes, "_store", lambda: store)
    monkeypatch.setattr(local, "configured_model", lambda: "test")
    yield app, project, store
    store.close()


def snapshot(project, **changes):
    return SelectionContext.model_validate({"project_id": project.id, "state_version": project.state_version,
        "plan_version": project.plan.version, "zone_ids": ["west", "east"], "object_ids": ["tree-a"], **changes})


def user_intent(text="Закрепи выделенное дерево", **changes):
    return AgentIntent.model_validate({"raw_text": text, "goal": {"operation": "edit"}, "scope_mode": "selection",
        "edit": {"action": "lock"}, "plant_kind": "tree", **changes})


def bound(project, **changes):
    return bind_selection(user_intent(**changes), snapshot(project), project)


def run(store, app, project, intent):
    record = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
    def choose(context):
        return AgentDecision(action="tool", tool=ToolCall(name="prepare_existing_change", arguments={
            "base_plan_version": context["run"]["plan_version"], "operation": "edit", "edit_action": "lock",
            "quantity": intent.goal.target_count, "quantity_mode": "target", "plant_kind": intent.plant_kind,
            "zone_ids": ["east"], "object_ids": ["tree-b"]}))
    return AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id, choose)


def mock_model(monkeypatch, **changes):
    draft = {"operation": "edit", "scope_mode": "explicit", "zone_labels": ["East"], "object_ids": ["tree-b"],
        "edit": {"action": "lock"}, "plant_kind": "tree", "target_count": None, **changes}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})


@pytest.mark.parametrize("text,target", [
    ("Закрепи выделенное дерево", "objects"), ("Удали выделенные посадки", "objects"),
    ("На выбранном участке посади деревья", "zones"),
    ("Используй текущее выделение посадок", "objects"), ("Используй текущее выделение участков", "zones"),
    ("Используй текущее выделение", None), ("Дерево выделенное закрепи", "objects"),
])
def test_reference_uses_referred_noun_not_background_nouns(text, target):
    assert selection_reference(text).target == target


@pytest.mark.parametrize("text", ["Посади 3 дерева по выбранной схеме", "Посади деревья выбранной породы",
    "Посади деревья выбранной породы на участке West", "Используй выбранный способ размещения",
    "Проверь выбранный план посадок"])
def test_chosen_non_map_noun_is_not_a_selection_instruction(text):
    assert selection_reference(text) is None


@pytest.mark.parametrize("text", ["Посади деревья не на выделенном участке", "Закрепи все кроме выделенных деревьев",
    "Используй участок без выделенных посадок", "Удали посадки, исключая выделенные деревья"])
def test_negated_or_excluded_selection_never_authorizes_selected_set(selected, text):
    app, project, store = selected
    value = bind_selection(user_intent(text), snapshot(project), project)
    assert value.selection_issue.code == "SELECTION_CONFLICT"
    final = run(store, app, project, value)
    assert final.state.status == "waiting_question" and final.state.step == 0


def test_create_binds_actual_snapshot_ignoring_hallucinated_zone_and_ids(selected, monkeypatch):
    app, project, store = selected
    mock_model(monkeypatch)
    created = routes.create_run(project.id, routes.AgentRunCreate(text="Закрепи выделенное дерево", selection_context=snapshot(project)))
    assert created.state.intent.scope_mode == "selection"
    assert not created.state.intent.explicit_zone_ids and not created.state.intent.explicit_object_ids
    assert created.state.intent.selection_context.zone_ids == ["west", "east"]
    assert created.state.resolved_scope.object_ids == ["tree-a"] and created.state.resolved_scope.zone_ids == []
    assert created.state.resolved_scope.basis == "selection"


def test_attached_context_does_not_override_explicit_source(selected, monkeypatch):
    app, project, store = selected
    mock_model(monkeypatch, zone_labels=["West"], object_ids=[])
    record = routes.create_run(project.id, routes.AgentRunCreate(text="Закрепи деревья на участке West", selection_context=snapshot(project)))
    assert record.state.intent.scope_mode == "explicit" and record.state.intent.selection_context is None
    assert record.state.resolved_scope.zone_ids == ["west"]


@pytest.mark.parametrize("changes,code", [
    ({"object_ids": []}, "SELECTION_EMPTY"), ({"object_ids": ["missing"]}, "SELECTION_UNKNOWN"),
    ({"project_id": "other"}, "SELECTION_PROJECT_MISMATCH"), ({"state_version": 1}, "SELECTION_STALE"),
    ({"plan_version": 100}, "SELECTION_STALE"), ({"plan_version": None}, "SELECTION_STALE"),
])
def test_bad_requested_snapshot_questions_before_any_tool(selected, changes, code):
    app, project, store = selected
    value = bind_selection(user_intent(), snapshot(project, **changes), project)
    final = run(store, app, project, value)
    assert final.state.status == "waiting_question" and final.state.pending_question["slot"] == "selection"
    assert final.state.intent.selection_issue.code == code and final.state.step == 0
    assert not final.state.pending_approval and final.state.resolved_scope is None
    assert app.get(project.id).plan.objects == project.plan.objects


def test_missing_and_generic_mixed_selection_are_questions(selected):
    app, project, store = selected
    for context, text, code in [(None, "Закрепи выделенное дерево", "SELECTION_MISSING"),
                               (snapshot(project), "Закрепи выделенное", "SELECTION_AMBIGUOUS")]:
        value = bind_selection(user_intent(text), context, project)
        final = run(store, app, project, value)
        assert final.state.intent.selection_issue.code == code and not final.state.tool_calls


def test_source_conflict_cannot_intersect_or_combine_explicit_and_selection(selected):
    app, project, store = selected
    value = bind_selection(user_intent("Закрепи выделенные деревья на участке West", explicit_zone_ids=["west"]), snapshot(project), project)
    final = run(store, app, project, value)
    assert final.state.intent.selection_issue.code == "SELECTION_CONFLICT" and final.state.step == 0


def test_tree_filter_uses_only_selected_objects_and_preserves_target(selected):
    app, project, store = selected
    value = bind_selection(user_intent("Закрепи 2 выделенных дерева", goal={"operation": "edit", "target_count": 2}),
                           snapshot(project, object_ids=["tree-a", "shrub"]), project)
    assert value.selection_binding.object_ids == ["tree-a"]
    final = run(store, app, project, value)
    assert final.state.status == "waiting_question" and final.state.pending_question["slot"] == "objects"
    assert final.state.intent.goal.target_count == 2 and not final.state.pending_approval
    assert final.state.last_result.data["object_selection"]["found_count"] == 1
    assert app.get(project.id).plan.objects == project.plan.objects


def test_selected_existing_preview_and_commit_exactly_one_original_object(selected):
    app, project, store = selected
    value = bound(project)
    final = run(store, app, project, value)
    assert final.state.status == "waiting_approval"
    ref = final.state.pending_approval["preview_ref"]
    preview = preview_routes.get_run_preview(project.id, final.state.run_id, ref)
    assert [item.id for item in preview.updates] == ["tree-a"]
    assert app.get(project.id).plan.objects == project.plan.objects
    # Changing external UI arrays cannot mutate the durable accepted snapshot.
    value.selection_context.object_ids[:] = ["tree-b"]
    approved = routes.approve_run(project.id, final.state.run_id, routes.AgentRunApproval(preview_ref=ref))
    assert approved.state.status == "finished"
    by_id = {item.id: item for item in app.get(project.id).plan.objects}
    assert by_id["tree-a"].locked and not by_id["tree-b"].locked and not by_id["shrub"].locked


@pytest.mark.parametrize("args", [{"object_ids": ["tree-b"]}, {"object_ids": []}, {"zone_ids": ["west"], "object_ids": []}])
def test_direct_gateway_rejects_altered_selected_scope(selected, args):
    app, project, store = selected
    if args == {"object_ids": []}:
        result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=bound(project)), ToolCall(
            name="prepare_existing_change", arguments={**existing_arguments(project), "quantity": None, **args}))
        assert result.status == "failed" and result.error.code == "INVALID_TOOL_ARGUMENTS"
        assert not app.changes._previews
        return
    with pytest.raises(GatewayRejected):
        ToolGateway(app).call(GatewayContext(project_id=project.id, intent=bound(project)), ToolCall(
            name="prepare_existing_change", arguments={**existing_arguments(project), "quantity": None, **args}))


def test_verifier_rejects_other_selected_object_even_with_plausible_summary(selected):
    app, project, store = selected
    final = run(store, app, project, bound(project))
    wrong = bind_selection(user_intent(), snapshot(project, object_ids=["tree-b"]), project)
    assert verify_preview_data(final.state.last_result.data, tool_name="prepare_existing_change", intent=wrong).status == "rejected"


def test_stale_preview_cannot_get_or_commit_and_explicit_resume_keeps_ids(selected):
    app, project, store = selected
    final = run(store, app, project, bound(project))
    origin = final.state.intent.selection_context.model_dump()
    changed = app.get(project.id)
    changed.name = "Changed"
    app.repository.save(changed)
    ref = final.state.pending_approval["preview_ref"]
    with pytest.raises(HTTPException) as denied:
        preview_routes.get_run_preview(project.id, final.state.run_id, ref)
    assert denied.value.status_code == 409
    failed = routes.approve_run(project.id, final.state.run_id, routes.AgentRunApproval(preview_ref=ref))
    assert failed.state.status == "failed" and app.get(project.id).plan.objects == project.plan.objects
    resumed = routes.resume_run(project.id, final.state.run_id)
    assert resumed.state.intent.selection_context.model_dump() == origin
    assert resumed.state.resolved_scope.object_ids == ["tree-a"]
    assert resumed.state.resolved_scope.source_revision == app.get(project.id).state_version
    assert resumed.state.last_result is None and resumed.state.pending_approval is None
    with pytest.raises(HTTPException):
        preview_routes.get_run_preview(project.id, final.state.run_id, ref)


def wait_for_question(store, record, slot="objects"):
    return store.checkpoint(record.state.project_id, record.state.run_id, expected_revision=record.revision,
        state=record.state.model_copy(update={"status": "waiting_question", "pending_question": {"slot": slot}, "pending_approval": None}),
        kind="question", payload={"slot": slot})


def test_answer_ignores_new_ui_selection_without_explicit_refresh(selected, monkeypatch):
    app, project, store = selected
    original = bound(project)
    record = store.create(project.id, original, snapshot_version=project.state_version, plan_version=project.plan.version)
    wait_for_question(store, record)
    mock_model(monkeypatch, zone_labels=[], object_ids=[])
    answered = routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text="Количество 1",
        selection_context=snapshot(project, object_ids=["tree-b"])))
    assert answered.state.intent.selection_context == original.selection_context
    assert answered.state.resolved_scope.object_ids == ["tree-a"]


def test_explicit_selection_answer_rebinds_and_discards_prior_preview(selected, monkeypatch):
    app, project, store = selected
    record = run(store, app, project, bound(project))
    old_ref = record.state.pending_approval["preview_ref"]
    wait_for_question(store, record, "selection")
    mock_model(monkeypatch, zone_labels=[], object_ids=[])
    answered = routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text="Используй текущее выделение посадок",
        selection_context=snapshot(project, object_ids=["tree-b"])))
    assert answered.state.resolved_scope.object_ids == ["tree-b"] and answered.state.last_result is None
    assert answered.state.pending_approval is None
    with pytest.raises(HTTPException):
        routes.approve_run(project.id, record.state.run_id, routes.AgentRunApproval(preview_ref=old_ref))


def test_cancel_preserves_snapshot_but_forbids_preview_and_approval(selected):
    app, project, store = selected
    record = run(store, app, project, bound(project))
    cancelled = routes.cancel_run(project.id, record.state.run_id)
    assert cancelled.state.intent.selection_context == record.state.intent.selection_context
    ref = record.state.pending_approval["preview_ref"]
    with pytest.raises(HTTPException):
        preview_routes.get_run_preview(project.id, record.state.run_id, ref)
    with pytest.raises(HTTPException):
        routes.approve_run(project.id, record.state.run_id, routes.AgentRunApproval(preview_ref=ref))


def test_resume_deleted_original_id_questions_without_selecting_neighbour(selected):
    app, project, store = selected
    record = run(store, app, project, bound(project))
    routes.cancel_run(project.id, record.state.run_id)
    changed = app.get(project.id)
    changed.plan.objects = [item for item in changed.plan.objects if item.id != "tree-a"]
    changed.plan.version += 1
    app.repository.save(changed)
    resumed = routes.resume_run(project.id, record.state.run_id)
    assert resumed.state.intent.selection_issue.code == "SELECTION_UNKNOWN"
    final = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id, lambda _: pytest.fail("No model"))
    assert final.state.pending_question["slot"] == "selection" and final.state.step == 0


def test_read_uses_selection_binding_not_caller_scope(selected):
    app, project, store = selected
    value = bind_selection(user_intent("Подбери породы для выделенных посадок", goal={"operation": "inspect"},
        read={"capability": "species_shortlist"}, edit=None, plant_kind=None), snapshot(project), project)
    assert read_arguments(value, None) == {"zone_ids": [], "object_ids": ["tree-a"], "kind": None}
    record = store.create(project.id, value, snapshot_version=project.state_version, plan_version=project.plan.version)
    final = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id, lambda _: pytest.fail("No model"))
    assert final.state.status == "finished" and final.state.read_outcome.object_ids == ["tree-a"]


def test_selected_zone_placement_uses_zone_not_default_object(selected):
    app, project, store = selected
    value = bind_selection(user_intent("Посади 3 дерева на выделенном участке", goal={"operation": "place", "target_count": 3},
        arrangement="area", edit=None), snapshot(project, zone_ids=["east"]), project)
    assert value.selection_binding.zone_ids == ["east"] and not value.selection_binding.object_ids
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=value), ToolCall(name="prepare_placement",
        arguments={**placement_arguments(project), "zone_ids": ["east"]}))
    assert result.status == "succeeded" and result.verification.status == "verified"
    assert {item["planting_zone_id"] for item in result.data["proposal"]["change_set"]["additions"]} == {"east"}


def test_generic_refresh_keeps_source_tree_filter_on_mixed_new_objects(selected, monkeypatch):
    app, project, store = selected
    record = store.create(project.id, bound(project), snapshot_version=project.state_version, plan_version=project.plan.version)
    wait_for_question(store, record, "selection")
    mock_model(monkeypatch, zone_labels=[], object_ids=[])
    answered = routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text="Используй текущее выделение посадок",
        selection_context=snapshot(project, object_ids=["tree-b", "shrub"])))
    assert answered.state.resolved_scope.object_ids == ["tree-b"]
    assert answered.state.intent.plant_kind == "tree"


def test_answer_can_explicitly_replace_old_selection_with_named_zone(selected, monkeypatch):
    app, project, store = selected
    record = store.create(project.id, bound(project), snapshot_version=project.state_version, plan_version=project.plan.version)
    wait_for_question(store, record)
    mock_model(monkeypatch, zone_labels=["West"], object_ids=[])
    answered = routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text="Примени к участку West",
        selection_context=snapshot(project, object_ids=["tree-b"])))
    assert answered.state.intent.scope_mode == "explicit"
    assert answered.state.resolved_scope.zone_ids == ["west"]
    assert answered.state.intent.selection_context is None and answered.state.intent.selection_binding is None


def test_answer_can_explicitly_replace_old_zone_with_fresh_selection(selected, monkeypatch):
    app, project, store = selected
    original = user_intent("Закрепи деревья на участке West", scope_mode="explicit", explicit_zone_ids=["west"])
    record = store.create(project.id, original, snapshot_version=project.state_version, plan_version=project.plan.version)
    wait_for_question(store, record)
    mock_model(monkeypatch, zone_labels=["West"], object_ids=[])
    answered = routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text="Используй текущее выделение посадок",
        selection_context=snapshot(project, object_ids=["tree-b"])))
    assert answered.state.intent.scope_mode == "selection" and answered.state.resolved_scope.object_ids == ["tree-b"]


@pytest.mark.parametrize("change", ["subset", "new_object", "source_kind", "version"])
def test_forged_binding_is_not_authoritative_at_direct_gateway(selected, change):
    app, project, store = selected
    value = bind_selection(user_intent("Закрепи выделенные посадки", plant_kind=None),
        snapshot(project, object_ids=["tree-a", "tree-b"]), project)
    if change == "subset":
        value.selection_binding.object_ids = ["tree-a"]
    elif change == "new_object":
        value.selection_binding.object_ids = ["shrub"]
    elif change == "source_kind":
        value.selection_reference.plant_kind = "shrub"
    else:
        value.selection_binding.plan_version = 999
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=value), ToolCall(name="prepare_existing_change",
        arguments={**existing_arguments(project), "quantity": None, "plant_kind": None, "object_ids": value.selection_binding.object_ids}))
    assert result.status == "blocked" and result.error.code.startswith("SELECTION_")
    assert not app.changes._previews


def test_unknown_numbered_scope_answer_does_not_fall_back_to_old_selection(selected, monkeypatch):
    app, project, store = selected
    record = store.create(project.id, bound(project), snapshot_version=project.state_version, plan_version=project.plan.version)
    wait_for_question(store, record)
    mock_model(monkeypatch, zone_labels=[], object_ids=[])
    with pytest.raises(HTTPException) as denied:
        routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text="Используй участок 999"))
    assert denied.value.status_code == 422
    assert store.get(project.id, record.state.run_id).state.status == "waiting_question"


def test_named_scope_to_selection_answer_does_not_reuse_old_missing_number(monkeypatch, selected):
    app, project, store = selected
    mock_model(monkeypatch, zone_labels=["Участок 999"], object_ids=[])
    turns = ["Закрепи деревья на участке 999.", "Используй текущее выделение посадок"]
    text = "\n\nДополнение пользователя: ".join(turns)
    compiled = IntentCompiler("test").compile(text, source_turns=turns,
        project_context={"zones": [{"id": "west", "label": "West", "number": 1}]})
    assert compiled.scope_mode == "selection" and compiled.explicit_zone_ids == []
    accepted = bind_selection(compiled, snapshot(project), project)
    assert accepted.selection_binding.object_ids == ["tree-a"]


def test_read_answer_accepts_explicit_selection_refresh_and_completes(selected, monkeypatch):
    app, project, store = selected
    value = user_intent("Подбери породы для выделенных посадок", goal={"operation": "inspect"}, edit=None, plant_kind=None,
        read={"capability": "species_shortlist"})
    value = bind_selection(value, None, project)
    record = store.create(project.id, value, snapshot_version=project.state_version, plan_version=project.plan.version)
    wait_for_question(store, record, "selection")
    mock_model(monkeypatch, operation="inspect", scope_mode="selection", zone_labels=[], object_ids=[], edit=None, plant_kind=None)
    answered = routes.answer_run(project.id, record.state.run_id, routes.AgentRunAnswer(text="Используй текущее выделение посадок",
        selection_context=snapshot(project)))
    final = AgentEngine(store, ToolGateway(app)).run(answered.state.run_id, project.id, lambda _: pytest.fail("No model"))
    assert final.state.status == "finished" and final.state.read_outcome.object_ids == ["tree-a"]
