"""Adversarial requests bypass the planner and exercise the real domain boundary."""

from application_factory import recompose_application
from app.composition import get_runtime
from contextlib import closing
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException
from shapely.geometry import box, mapping

from app import api, planning_assistant as local
from app.agent_runtime import routes, preview_routes
from app.agent_runtime.contracts import AgentDecision, AgentIntent, EditIntent, Goal, ToolCall, ToolResult
from app.agent_runtime.engine import AgentEngine, _summarize_data
from app.agent_runtime.gateway import GatewayContext, GatewayPolicy, GatewayRejected, ToolGateway
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.policy import assess_requirements
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.store import AgentRunStore
from app.agent_runtime.verifier import verify_preview_data
from app.contracts import PlanObject
from app.history.adapters import InMemoryProjectHistory
from test_placement_allocation import application

TILIA = "tilia-cordata@2026-08-28.1"
ACER = "acer-platanoides@2026-08-28.1"


def place_intent(count=3, **overrides):
    return AgentIntent(raw_text=f"Посади {count} деревьев по площади", goal=Goal(operation="place", target_count=count),
        scope_mode="explicit", explicit_zone_ids=["west"], plant_kind="tree", arrangement="area", **overrides)


def placement_arguments(project, count=3):
    return {"base_plan_version": project.plan.version, "zone_ids": ["west"], "plant_kind": "tree",
            "arrangement": "area", "target_count": count, "quantity_mode": "target"}


def seeded():
    app, project = application()
    app = recompose_application(app, history=InMemoryProjectHistory())
    project.plan.objects = [PlanObject(id="tree-a", kind="tree", x=30, y=30, radius=2,
                                     species_revision_id=TILIA, planting_zone_id="west")]
    app.repository.save(project)
    return app, app.get(project.id)


def existing_intent(action="lock", operation="edit", **overrides):
    return AgentIntent(raw_text="Измени 1 дерево", goal=Goal(operation=operation, target_count=1),
        scope_mode="explicit", explicit_object_ids=["tree-a"], plant_kind="tree",
        edit=EditIntent(action=action) if operation == "edit" else None, **overrides)


def existing_arguments(project, action="lock", operation="edit"):
    return {"base_plan_version": project.plan.version, "operation": operation, "object_ids": ["tree-a"],
        "plant_kind": "tree", "quantity": 1, "quantity_mode": "target",
        "edit_action": action if operation == "edit" else None}


@pytest.mark.parametrize("operation", ["place", "edit", "delete", "inspect", "zones", "release"])
def test_planner_discovery_only_exposes_the_intended_plan_preview(tmp_path, operation):
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        intent = AgentIntent(raw_text="Test", goal=Goal(operation=operation), scope_mode="project")
        record = store.create("project", intent)
        engine = AgentEngine(store, type("Gateway", (), {"registry": CapabilityRegistry()})())
        previews = {item["name"] for item in engine._planner_capabilities(record.state) if item["effect"] == "preview"}
        expected = {"place": {"prepare_placement"}, "edit": {"prepare_existing_change"},
                    "delete": {"prepare_existing_change"}, "zones": {"prepare_zone_change"}}.get(operation, set())
        assert previews == expected


@pytest.mark.parametrize("operation", ["inspect", "zones", "release"])
@pytest.mark.parametrize("tool", ["prepare_placement", "prepare_existing_change", "preview_changes", "commit_change_set"])
def test_read_and_control_goals_cannot_prepare_or_commit_plan_changes(operation, tool):
    class NeverApplication:
        def get(self, *_args, **_kwargs):
            raise AssertionError("Intent mismatch must be rejected before domain execution")
    intent = AgentIntent(raw_text="Test", goal=Goal(operation=operation), scope_mode="project")
    context = GatewayContext(project_id="project", intent=intent, approved_change_set_id="x", approved_digest="y",
        policy=GatewayPolicy(allowed_effects=frozenset({"read", "preview", "write"})))
    with pytest.raises(GatewayRejected, match="operation"):
        ToolGateway(NeverApplication()).call(context, ToolCall(name=tool, arguments={"preview_id": "x", "digest": "y"}))


@pytest.mark.parametrize("changed", [
    {"target_count": 1}, {"quantity_mode": "maximum"}, {"plant_kind": "shrub"},
    {"arrangement": "building_contour"}, {"zone_ids": ["east"]}, {"species_revision_ids": [ACER]},
])
def test_placement_arguments_cannot_change_user_choices(changed):
    app, project = application()
    args = {**placement_arguments(project), "species_revision_ids": [TILIA], **changed}
    with pytest.raises(GatewayRejected):
        ToolGateway(app).call(GatewayContext(project_id=project.id, intent=place_intent(species_ids=[TILIA])),
                              ToolCall(name="prepare_placement", arguments=args))
    assert not app.changes._previews and not app.get(project.id).plan.objects


@pytest.mark.parametrize("changed", [
    {"operation": "delete", "edit_action": None}, {"quantity": None}, {"quantity": 2},
    {"quantity_mode": "maximum"}, {"object_ids": ["tree-b"]}, {"object_ids": [], "zone_ids": ["west"]},
    {"plant_kind": "shrub"}, {"edit_action": "unlock"}, {"edit_action": "move", "move_dx_m": 2},
])
def test_existing_arguments_cannot_change_user_effect_or_scope(changed):
    app, project = seeded()
    with pytest.raises(GatewayRejected):
        ToolGateway(app).call(GatewayContext(project_id=project.id, intent=existing_intent()),
                              ToolCall(name="prepare_existing_change", arguments={**existing_arguments(project), **changed}))
    assert not app.changes._previews and app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize("requirements", [
    {"unresolved_requirements": ["Сохрани вид на памятник"]},
    {"hard_constraints": [{"rule_id": "invented", "policy_owned": True}]},
    {"hard_constraints": [{"rule_id": "pp743-3.6.3-building", "source_text": "не ближе 10 метров"}]},
])
def test_unbound_requirements_block_direct_preview_and_existing_approval(requirements):
    app, project = application()
    original = place_intent()
    gateway = ToolGateway(app)
    result = gateway.call(GatewayContext(project_id=project.id, intent=original), ToolCall(
        name="prepare_placement", arguments=placement_arguments(project)))
    assert result.status == "succeeded"
    intent = AgentIntent.model_validate({**original.model_dump(), **requirements})
    denied = gateway.call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
        name="prepare_placement", arguments=placement_arguments(project)))
    assert denied.status == "blocked" and denied.error.code == "REQUIREMENTS_UNRESOLVED"
    assert verify_preview_data(result.data, tool_name=result.name, intent=intent).status == "rejected"
    full = result.data["proposal"]["change_set"]
    denied = gateway.call(GatewayContext(project_id=project.id, intent=intent,
        approved_change_set_id=full["id"], approved_digest=full["digest"], approved_preview_result=result,
        policy=GatewayPolicy(allowed_effects=frozenset({"write"}))), ToolCall(name="commit_change_set", arguments={
            "preview_id": full["id"], "digest": full["digest"], "base_plan_version": project.plan.version}))
    assert denied.status == "blocked" and app.get(project.id).plan.objects == []


@pytest.mark.parametrize("field,value", [("arrangement", "building_contour"), ("plant_kind", "shrub"),
                                        ("species_ids", [ACER])])
def test_full_and_compact_verifier_reject_wrong_placement_choices(field, value):
    app, project = application()
    original = place_intent(species_ids=[TILIA])
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=original), ToolCall(
        name="prepare_placement", arguments={**placement_arguments(project), "species_revision_ids": [TILIA]}))
    assert result.status == "succeeded"
    changed = original.model_copy(update={field: value})
    for data in [result.data, _summarize_data(result.name, result.data)]:
        assert verify_preview_data(data, tool_name=result.name, intent=changed).status == "rejected"


@pytest.mark.parametrize("operation", ["edit", "delete"])
def test_existing_full_and_compact_preview_are_verified_with_exact_target_facts(operation):
    app, project = seeded()
    intent = existing_intent(operation=operation)
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
        name="prepare_existing_change", arguments=existing_arguments(project, operation=operation)))
    assert result.status == "succeeded", result
    for data in [result.data, _summarize_data(result.name, result.data)]:
        assert verify_preview_data(data, tool_name=result.name, intent=intent).status == "verified"
        changed = deepcopy(data)
        changed["target_ids"] = ["tree-b"]
        assert verify_preview_data(changed, tool_name=result.name, intent=intent).status == "rejected"
    assert not app.get(project.id).plan.objects[0].locked


@pytest.mark.parametrize("change", ["move", "species", "unlock", "delete", "extra_update"])
def test_verifier_rejects_hidden_effect_even_when_tool_metadata_claims_lock(change):
    app, project = seeded()
    intent = existing_intent()
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
        name="prepare_existing_change", arguments=existing_arguments(project)))
    assert result.status == "succeeded", result
    forged = deepcopy(result.data)
    preview = forged["change_set"]
    if change == "move":
        preview["updates"][0]["x"] += 2
    elif change == "species":
        preview["updates"][0]["species_revision_id"] = ACER
    elif change == "unlock":
        preview["updates"][0]["locked"] = False
    elif change == "delete":
        preview["deletion_ids"] = ["tree-a"]
    else:
        preview["updates"].append({**preview["updates"][0], "id": "tree-b"})
    for data in [forged, _summarize_data(result.name, forged)]:
        assert verify_preview_data(data, tool_name=result.name, intent=intent).status == "rejected"


def save_pending(store, project, intent, result):
    record = store.create(project.id, intent, run_id="run", snapshot_version=project.state_version, plan_version=project.plan.version)
    store.checkpoint(project.id, "run", expected_revision=record.revision,
        state=record.state.model_copy(update={"status": "waiting_approval", "pending_approval": {"preview_ref": result.call_id}}),
        kind="tool_result", payload={**result.model_dump(mode="json"), "data": _summarize_data(result.name, result.data)})


def test_ten_actual_trees_survive_durable_summary_map_review_and_approval(tmp_path, monkeypatch):
    app, project = application()
    app = recompose_application(app, history=InMemoryProjectHistory())
    intent = place_intent(10)
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
        name="prepare_placement", arguments=placement_arguments(project, 10)))
    assert result.status == "succeeded", result
    summary = _summarize_data(result.name, result.data)
    assert summary["change_set"]["candidate_summary"] == {"total": 10, "allowed": 10}
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        save_pending(store, project, intent, result)
        monkeypatch.setattr(get_runtime(), "application", app)
        monkeypatch.setattr(routes, "_store", lambda: store)
        monkeypatch.setattr(preview_routes, "_store", lambda: store)
        shown = preview_routes.get_run_preview(project.id, "run", result.call_id)
        assert len(shown.additions) == 10 and not app.get(project.id).plan.objects
        finished = routes.approve_run(project.id, "run", routes.AgentRunApproval(preview_ref=result.call_id))
        assert finished.state.status == "finished", finished.state.failure
        assert len(app.get(project.id).plan.objects) == 10
        assert {item.id for item in app.get(project.id).plan.objects} == {item.id for item in shown.additions}


def test_authoritative_effect_cannot_hide_behind_a_valid_saved_summary(tmp_path, monkeypatch):
    app, project = seeded()
    intent = existing_intent()
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
        name="prepare_existing_change", arguments=existing_arguments(project)))
    assert result.status == "succeeded", result
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        save_pending(store, project, intent, result)
        monkeypatch.setattr(get_runtime(), "application", app)
        monkeypatch.setattr(routes, "_store", lambda: store)
        monkeypatch.setattr(preview_routes, "_store", lambda: store)
        preview = app.changes._previews[result.data["change_set"]["id"]].preview
        preview.updates[0].x += 5
        with pytest.raises(HTTPException) as error:
            preview_routes.get_run_preview(project.id, "run", result.call_id)
        assert error.value.status_code == 409
        with pytest.raises(HTTPException) as error:
            routes.approve_run(project.id, "run", routes.AgentRunApproval(preview_ref=result.call_id))
        assert error.value.status_code == 409
        assert app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize("operation", ["zones", "release"])
def test_unsupported_control_cannot_finish_from_read_facts(tmp_path, operation):
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        store.create("project", AgentIntent(raw_text="Test", goal=Goal(operation=operation), scope_mode="project"), run_id="run")
        engine = AgentEngine(store, type("Gateway", (), {"registry": CapabilityRegistry()})())
        result = engine.run("run", "project", lambda _context: AgentDecision(action="finish", outcome_ref="pretend-complete"))
        if operation == "zones":
            assert result.state.status == "waiting_question"
            assert result.state.pending_question["code"] == "ZONE_INTENT_UNRESOLVED"
            assert result.state.pending_approval is None
        else:
            assert result.state.status == "failed" and result.state.failure.code == "UNSUPPORTED_OPERATION"


@pytest.mark.parametrize("text,expected", [
    ("Закрепи 1 дерево", EditIntent(action="lock")),
    ("Открепи 1 дерево", EditIntent(action="unlock")),
    ("Перемести 1 дерево на 2 метра по оси X и Y=-1", EditIntent(action="move", move_dx_m=2, move_dy_m=-1)),
    ("Перемести 1 дерево на 2 метра на восток", EditIntent(action="move")),
])
def test_edit_parameters_are_bound_to_user_source_not_model_guess(monkeypatch, text, expected):
    draft = {"operation": "edit", "scope_mode": "explicit", "object_ids": ["tree-a"],
             "edit": {"action": "move", "move_dx_m": 999, "move_dy_m": 999}}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: {"message": {"content": json.dumps(draft)}})
    compiled = IntentCompiler("test").compile(text)
    assert compiled.edit == expected
    if expected.action == "move" and expected.move_dx_m is None:
        assert assess_requirements(compiled).status == "unsupported"


def test_source_numeric_constraint_survives_model_omission(monkeypatch):
    draft = {"operation": "place", "scope_mode": "explicit", "hard_constraints": [], "unresolved_requirements": []}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: {"message": {"content": json.dumps(draft)}})
    compiled = IntentCompiler("test").compile("Посади 3 дерева не ближе 10 метров от зданий")
    assert "не ближе 10 метров" in compiled.unresolved_requirements
    assert assess_requirements(compiled).status == "unsupported"


def test_required_policy_needs_both_binding_and_available_source_geometry(tmp_path):
    app, project = application()
    rules = ["pp743-3.6.3-building", "pp743-3.6.3-road-edge"]
    intent = place_intent(hard_constraints=[{"rule_id": rule, "source_text": "нормативные отступы"} for rule in rules])
    arguments = {**placement_arguments(project), "condition_rule_ids": rules}
    gateway = ToolGateway(app)
    missing = gateway.call(GatewayContext(project_id=project.id, intent=intent), ToolCall(name="prepare_placement", arguments=arguments))
    assert missing.status == "blocked" and missing.error.code == "REQUIREMENTS_UNRESOLVED"
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        store.create(project.id, intent, run_id="run", snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, gateway).run("run", project.id, lambda _ctx: AgentDecision(
            action="tool", tool=ToolCall(name="prepare_placement", arguments=arguments)))
        assert result.state.status == "waiting_question" and result.state.pending_question["slot"] == "requirements"
        assert "building" in result.state.pending_question["question"]
    project.geometry.feature_collection["features"].extend([
        {"type": "Feature", "properties": {"kind": kind}, "geometry": mapping(box(300, 150, 310, 160))}
        for kind in ("building", "road")
    ])
    app.repository.save(project)
    accepted = gateway.call(GatewayContext(project_id=project.id, intent=intent), ToolCall(name="prepare_placement", arguments=arguments))
    assert accepted.status == "succeeded", accepted
    for data in (accepted.data, _summarize_data(accepted.name, accepted.data)):
        assert verify_preview_data(data, tool_name=accepted.name, intent=intent).status == "verified"
        forged = deepcopy(data)
        forged["condition_rule_ids"] = []
        assert verify_preview_data(forged, tool_name=accepted.name, intent=intent).status == "rejected"


@pytest.mark.parametrize("operation,action,parameters", [
    ("edit", "move", {"move_dx_m": 2.5, "move_dy_m": -1}),
    ("edit", "species", {"species_revision_ids": [ACER]}),
    ("edit", "lock", {}),
    ("delete", None, {}),
])
def test_existing_intent_effect_survives_actual_approval(tmp_path, monkeypatch, operation, action, parameters):
    app, project = seeded()
    intent = existing_intent(action=action, operation=operation)
    if action == "move":
        intent = intent.model_copy(update={"edit": EditIntent(action="move", **parameters)})
    if action == "species":
        intent = intent.model_copy(update={"species_ids": [ACER]})
    args = {**existing_arguments(project, action=action, operation=operation), **parameters}
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent), ToolCall(name="prepare_existing_change", arguments=args))
    assert result.status == "succeeded", result
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        save_pending(store, project, intent, result)
        monkeypatch.setattr(get_runtime(), "application", app)
        monkeypatch.setattr(routes, "_store", lambda: store)
        completed = routes.approve_run(project.id, "run", routes.AgentRunApproval(preview_ref=result.call_id))
        assert completed.state.status == "finished", completed.state.failure
        objects = app.get(project.id).plan.objects
        if operation == "delete":
            assert objects == []
        elif action == "move":
            assert (objects[0].x, objects[0].y, objects[0].locked) == (32.5, 29, False)
        elif action == "species":
            assert objects[0].species_revision_id == ACER and not objects[0].locked
        else:
            assert objects[0].locked and (objects[0].x, objects[0].y) == (30, 30)


def test_direct_commit_requires_evidence_and_checks_authoritative_effect():
    app, project = seeded()
    intent = existing_intent()
    gateway = ToolGateway(app)
    result = gateway.call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
        name="prepare_existing_change", arguments=existing_arguments(project)))
    full = result.data["change_set"]
    arguments = {"preview_id": full["id"], "digest": full["digest"], "base_plan_version": project.plan.version}
    values = dict(project_id=project.id, intent=intent, approved_change_set_id=full["id"], approved_digest=full["digest"],
                  policy=GatewayPolicy(allowed_effects=frozenset({"write"})))
    with pytest.raises(GatewayRejected, match="evidence"):
        gateway.call(GatewayContext(**values), ToolCall(name="commit_change_set", arguments=arguments))
    # Change only authoritative geometry; the compact audit still describes the original lock.
    app.changes._previews[full["id"]].preview.updates[0].x += 2
    with pytest.raises(GatewayRejected):
        gateway.call(GatewayContext(**values, approved_preview_result=result), ToolCall(name="commit_change_set", arguments=arguments))
    assert app.get(project.id).plan.objects == project.plan.objects


def test_compact_summary_retains_all_rejected_candidate_evidence_and_is_idempotent():
    app, project = application()
    intent = place_intent(10)
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
        name="prepare_placement", arguments=placement_arguments(project, 10)))
    forged = deepcopy(result.data)
    forged["proposal"]["change_set"]["candidate_results"][9]["status"] = "blocked"
    forged["proposal"]["change_set"]["candidate_summary"] = {"total": 10, "allowed": 10}
    assert verify_preview_data(forged, tool_name=result.name, intent=intent).status == "rejected"
    summary = _summarize_data(result.name, forged)
    assert summary["change_set"]["candidate_summary"] == {"total": 10, "allowed": 9}
    assert verify_preview_data(summary, tool_name=result.name, intent=intent).status == "rejected"
    assert _summarize_data(result.name, summary) == summary


def test_delegated_preview_requires_verified_scope_and_malformed_scope_is_typed():
    app, project = application()
    intent = place_intent().model_copy(update={"scope_mode": "delegated", "explicit_zone_ids": []})
    gateway = ToolGateway(app)
    with pytest.raises(GatewayRejected, match="scope"):
        gateway.call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
            name="prepare_placement", arguments=placement_arguments(project)))
    result = gateway.call(GatewayContext(project_id=project.id, intent=intent, allowed_zone_ids=frozenset({"west"})),
        ToolCall(name="prepare_placement", arguments={**placement_arguments(project), "zone_ids": [{"id": "west"}]}))
    assert result.status == "failed" and result.error.code == "INVALID_TOOL_ARGUMENTS"


def test_unavailable_map_selection_cannot_be_replaced_with_a_model_chosen_zone():
    app, project = application()
    intent = place_intent().model_copy(update={"scope_mode": "selection", "explicit_zone_ids": []})
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent), ToolCall(
        name="prepare_placement", arguments=placement_arguments(project)))
    assert result.status == "blocked" and result.error.code == "SELECTION_MISSING"
    assert not app.changes._previews


@pytest.mark.parametrize('source', ['соблюдай отступы', 'отступы соблюдай'])
def test_generic_known_policy_source_remains_supported_after_intent_compilation(monkeypatch, source):
    draft = {"operation": "place", "scope_mode": "explicit", "zone_labels": ["West"],
             "hard_constraints": [{"rule_id": "pp743-3.6.3-building", "source_text": source}]}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: {"message": {"content": json.dumps(draft)}})
    compiled = IntentCompiler("test").compile(f"Посади 6 деревьев, {source}.", project_context={"zones": [{"id": "west", "label": "West"}]})
    assessment = assess_requirements(compiled)
    assert assessment.status == "supported" and not assessment.unresolved
    assert set(assessment.rule_ids) == {"pp743-3.6.3-building", "pp743-3.6.3-road-edge"}


@pytest.mark.parametrize('source', ['не соблюдай отступы', 'отступы не соблюдай',
    'соблюдай отступы 10 метров', 'отступы соблюдай кроме дороги'])
def test_model_cannot_drop_setback_negation_or_qualifiers(monkeypatch, source):
    draft = {"operation": "place", "scope_mode": "project", "hard_constraints": [
        {"rule_id": "pp743-3.6.3-building", "source_text": "соблюдай отступы"}], "unresolved_requirements": []}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: {"message": {"content": json.dumps(draft)}})
    compiled = IntentCompiler("test").compile(f"Посади 3 дерева, {source}.")
    assert assess_requirements(compiled).status == "unsupported"
    assert compiled.unresolved_requirements


@pytest.mark.parametrize('operation', ['edit', 'delete'])
def test_one_of_three_existing_targets_yields_typed_question_without_replanning(tmp_path, operation):
    app, project = seeded()
    project.plan.objects.extend([
        project.plan.objects[0].model_copy(update={"id": f"tree-{index}", "x": 30 + index * 30})
        for index in (1, 2)
    ])
    app.repository.save(project)
    intent = existing_intent(operation=operation).model_copy(update={"explicit_zone_ids": ["west"], "explicit_object_ids": []})
    arguments = {**existing_arguments(project, operation=operation), "object_ids": [], "zone_ids": ["west"]}
    calls = []
    def choose(_context):
        calls.append(1)
        assert len(calls) == 1, "Selection ambiguity must return to the user, not the model"
        return AgentDecision(action="tool", tool=ToolCall(name="prepare_existing_change", arguments=arguments))
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        store.create(project.id, intent, run_id="run", snapshot_version=app.get(project.id).state_version)
        result = AgentEngine(store, ToolGateway(app)).run("run", project.id, choose)
        assert result.state.status == "waiting_question"
        assert result.state.pending_question["slot"] == "objects"
        assert result.state.last_result.error.code == "OBJECT_SELECTION_REQUIRED"
        assert result.state.last_result.data == {"object_selection": {"requested_count": 1, "found_count": 3}}
        assert result.state.pending_approval is None and not app.changes._previews
        assert app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize('tool', ['prepare_placement', 'prepare_existing_change'])
def test_invalid_arguments_keep_their_paths_in_durable_events_and_next_planner_context(tmp_path, tool):
    app, project = seeded()
    intent = place_intent() if tool == 'prepare_placement' else existing_intent()
    arguments = placement_arguments(project) if tool == 'prepare_placement' else existing_arguments(project)
    arguments['base_plan_version'] = -1
    calls = []
    def choose(context):
        calls.append(1)
        if len(calls) == 1:
            return AgentDecision(action='tool', tool=ToolCall(name=tool, arguments=arguments))
        result = context['last_result']
        assert result['error']['code'] == 'INVALID_TOOL_ARGUMENTS'
        assert result['data']['invalid_arguments'][0]['path'] == 'base_plan_version'
        assert 'change_set' not in result['data']
        return AgentDecision(action='ask', missing_slot='version', question='Обновите проект.')
    with closing(AgentRunStore(tmp_path / 'runs.sqlite3')) as store:
        store.create(project.id, intent, run_id='run', snapshot_version=project.state_version)
        result = AgentEngine(store, ToolGateway(app)).run('run', project.id, choose)
        assert result.state.status == 'waiting_question'
        saved = next(event.payload for event in result.events if event.kind == 'tool_result')
        assert saved['data']['invalid_arguments'][0]['path'] == 'base_plan_version'


@pytest.mark.parametrize('objects', [['fake-id'], ['3'], ['fake-id', '3']])
def test_numbered_zone_scope_drops_model_invented_object_ids(monkeypatch, objects):
    draft = {'operation': 'edit', 'scope_mode': 'explicit', 'zone_labels': ['Допустимая область 8'],
             'object_ids': objects, 'edit': {'action': 'lock'}, 'target_count': 3}
    monkeypatch.setattr(local, 'local_json', lambda *_args, **_kwargs: {'message': {'content': json.dumps(draft)}})
    compiled = IntentCompiler('test').compile('Закрепи все 3 дерева на участке Допустимая область 8.',
        project_context={'zones': [{'id': 'west', 'label': 'Допустимая область 8', 'number': 8}]})
    assert compiled.explicit_zone_ids == ['west'] and compiled.explicit_object_ids == []
    assert 'explicit_object_ids:source_only' in compiled.evidence.corrections


def test_explicit_object_identifier_is_preserved_when_present_in_source(monkeypatch):
    draft = {'operation': 'edit', 'scope_mode': 'explicit', 'object_ids': ['tree-a', 'other'], 'edit': {'action': 'lock'}}
    monkeypatch.setattr(local, 'local_json', lambda *_args, **_kwargs: {'message': {'content': json.dumps(draft)}})
    compiled = IntentCompiler('test').compile('Закрепи объект tree-a.')
    assert compiled.explicit_object_ids == ['tree-a']


@pytest.mark.parametrize('action', ['lock', 'move'])
def test_existing_edit_species_is_an_explicit_target_filter_not_an_ignored_hint(action):
    app, project = seeded()
    project.plan.objects.append(project.plan.objects[0].model_copy(update={'id': 'acer', 'x': 90, 'species_revision_id': ACER}))
    app.repository.save(project)
    intent = existing_intent(action=action, species_ids=[TILIA]).model_copy(update={
        'explicit_zone_ids': ['west'], 'explicit_object_ids': [],
        'edit': EditIntent(action=action, **({'move_dx_m': 2} if action == 'move' else {}))})
    args = {**existing_arguments(project, action=action), 'zone_ids': ['west'], 'object_ids': [],
            'species_revision_ids': [TILIA], **({'move_dx_m': 2} if action == 'move' else {})}
    gateway = ToolGateway(app)
    accepted = gateway.call(GatewayContext(project_id=project.id, intent=intent), ToolCall(name='prepare_existing_change', arguments=args))
    assert accepted.status == 'succeeded'
    assert accepted.data['target_ids'] == ['tree-a']
    intent_two = intent.model_copy(update={'goal': Goal(operation='edit', target_count=2)})
    denied = gateway.call(GatewayContext(project_id=project.id, intent=intent_two), ToolCall(name='prepare_existing_change', arguments={**args, 'quantity': 2}))
    assert denied.status == 'blocked' and denied.error.code == 'OBJECT_SELECTION_REQUIRED'
    assert denied.data['object_selection'] == {'requested_count': 2, 'found_count': 1}
    unfiltered = gateway.call(GatewayContext(project_id=project.id, intent=intent_two.model_copy(update={'species_ids': []})),
        ToolCall(name='prepare_existing_change', arguments={**args, 'quantity': 2, 'species_revision_ids': []}))
    assert unfiltered.status == 'succeeded'
    for data in (unfiltered.data, _summarize_data(unfiltered.name, unfiltered.data)):
        assert verify_preview_data(data, tool_name=unfiltered.name, intent=intent_two).status == 'rejected'
    assert app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize('operation,requested,found', [('edit', 2, 1), ('delete', 2, 1),
    ('edit', 2, 0), ('delete', 2, 0), ('delete', None, 0)])
def test_existing_target_shortfall_after_species_filter_stops_at_question(tmp_path, operation, requested, found):
    app, project = seeded()
    if not found:
        project.plan.objects[0].species_revision_id = ACER
        app.repository.save(project)
    intent = existing_intent(operation=operation, species_ids=[TILIA]).model_copy(update={
        'goal': Goal(operation=operation, target_count=requested), 'explicit_zone_ids': ['west'], 'explicit_object_ids': []})
    args = {**existing_arguments(project, operation=operation), 'quantity': requested,
            'zone_ids': ['west'], 'object_ids': [], 'species_revision_ids': [TILIA]}
    calls = []
    def choose(_context):
        calls.append(1)
        assert len(calls) == 1
        return AgentDecision(action='tool', tool=ToolCall(name='prepare_existing_change', arguments=args))
    with closing(AgentRunStore(tmp_path / 'runs.sqlite3')) as store:
        store.create(project.id, intent, run_id='run', snapshot_version=app.get(project.id).state_version)
        result = AgentEngine(store, ToolGateway(app)).run('run', project.id, choose)
        assert result.state.status == 'waiting_question'
        assert result.state.intent.goal.target_count == requested
        assert result.state.last_result.data == {'object_selection': {'requested_count': requested, 'found_count': found}}
        assert not app.changes._previews and app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize('unresolved', ['отступы соблюдай', 'соблюдай отступы', 'нормативные отступы'])
def test_source_backed_policy_resolves_duplicate_model_unresolved_classification(monkeypatch, unresolved):
    draft = {'operation': 'place', 'scope_mode': 'project', 'hard_constraints': [
        {'rule_id': 'pp743-3.6.3-building', 'source_text': 'отступы соблюдай'}], 'unresolved_requirements': [unresolved]}
    monkeypatch.setattr(local, 'local_json', lambda *_args, **_kwargs: {'message': {'content': json.dumps(draft)}})
    compiled = IntentCompiler('test').compile('Посади 70 деревьев вдоль зданий, отступы соблюдай.')
    assert compiled.unresolved_requirements == []
    assert assess_requirements(compiled).status == 'supported'
    assert 'requirements:bound_policy' in compiled.evidence.corrections


@pytest.mark.parametrize('source', ['отступы не соблюдай', 'отступы соблюдай кроме дороги',
    'отступы соблюдай и не ближе 10 метров'])
def test_resolving_known_policy_never_resolves_source_negation_or_extra_requirement(monkeypatch, source):
    draft = {'operation': 'place', 'scope_mode': 'project', 'unresolved_requirements': ['отступы соблюдай']}
    monkeypatch.setattr(local, 'local_json', lambda *_args, **_kwargs: {'message': {'content': json.dumps(draft)}})
    compiled = IntentCompiler('test').compile(f'Посади 3 дерева, {source}.')
    assert compiled.unresolved_requirements
    assert assess_requirements(compiled).status == 'unsupported'
