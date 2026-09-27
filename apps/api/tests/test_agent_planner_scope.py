"""Model-selected object lists must not replace the user's explicit scope."""

from contextlib import closing
import json

import pytest

from app import planning_assistant as local
from app.agent_runtime.contracts import AgentIntent, EditIntent, Goal, ToolCall
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.gateway import GatewayContext, GatewayRejected, ToolGateway
from app.agent_runtime.planner import LocalPlanner
from app.agent_runtime.store import AgentRunStore
from app.contracts import PlanObject
from test_placement_allocation import application


TILIA = "tilia-cordata@2026-08-28.1"


def intent_for_scope(*, operation="delete", count=3, zones=None, objects=None):
    return AgentIntent(
        raw_text="Удали все 3 дерева на участке West.",
        goal=Goal(operation=operation, target_count=count), scope_mode="explicit",
        explicit_zone_ids=["west"] if zones is None else zones,
        explicit_object_ids=objects or [], plant_kind="tree",
        edit=EditIntent(action="lock") if operation == "edit" else None,
    )


def model_arguments(*, operation="delete", count=3):
    return {
        "base_plan_version": 1, "operation": operation, "zone_ids": [],
        "object_ids": ["model-selected-tree"], "plant_kind": "tree",
        "quantity": count, "quantity_mode": "target", "species_revision_ids": [],
        "edit_action": "lock" if operation == "edit" else None,
    }


def respond_with(monkeypatch, arguments, *, needs_repair=False):
    responses = [json.dumps({"action": "tool", "tool": {
        "name": "prepare_existing_change", "arguments": arguments,
    }})]
    if needs_repair:
        responses.insert(0, '{"action":"tool"}')
    calls = []

    def respond(*_args, **_kwargs):
        calls.append(1)
        assert responses, "A fully specified change must not loop through model calls"
        return {"message": {"content": responses.pop(0)}}

    monkeypatch.setattr(local, "local_json", respond)
    return calls


@pytest.mark.parametrize("zones,objects", [(["west"], []), ([], ["tree-a", "tree-b", "tree-c"])])
@pytest.mark.parametrize("needs_repair", [False, True])
def test_planner_binds_both_scope_fields_after_initial_or_repaired_decision(monkeypatch, zones, objects, needs_repair):
    intent = intent_for_scope(zones=zones, objects=objects)
    arguments = {**model_arguments(), "species_revision_ids": [TILIA], "move_dx_m": 2.5}
    respond_with(monkeypatch, arguments, needs_repair=needs_repair)
    context = {"run": {"intent": intent.model_dump(mode="json")}}
    decision = LocalPlanner("test")(context)
    assert decision.tool.arguments == {**arguments, "zone_ids": zones, "object_ids": objects}
    # Only source scope is bound here; action, count, species and other fields
    # remain subject to independent gateway validation.
    assert context["run"]["intent"] == intent.model_dump(mode="json")


@pytest.mark.parametrize("update", [
    {"scope_mode": "delegated"},
    {"scope_mode": "selection"},
    {"scope_mode": "project"},
    {"explicit_zone_ids": []},
    {"explicit_object_ids": ["tree-a"]},
    {"goal": {"operation": "inspect"}},
])
def test_planner_never_invents_an_explicit_scope_for_unsupported_or_ambiguous_intent(monkeypatch, update):
    source = {**intent_for_scope().model_dump(mode="json"), **update}
    arguments = model_arguments()
    respond_with(monkeypatch, arguments)
    result = LocalPlanner("test")({"run": {"intent": source}})
    assert result.tool.arguments == arguments


def populated_project():
    app, project = application()
    project.plan.objects = [
        PlanObject(id=f"west-tree-{index}", kind="tree", x=30 + index * 15, y=30,
                   radius=2, species_revision_id=TILIA, planting_zone_id="west")
        for index in range(3)
    ] + [PlanObject(id="east-tree", kind="tree", x=440, y=30, radius=2,
                    species_revision_id=TILIA, planting_zone_id="east")]
    app.repository.save(project)
    return app, app.get(project.id)


@pytest.mark.parametrize("operation", ["delete", "edit"])
def test_resolved_existing_scope_reaches_exact_preview_without_another_model_decision(monkeypatch, tmp_path, operation):
    app, project = populated_project()
    intent = intent_for_scope(operation=operation)
    arguments = {**model_arguments(operation=operation), "object_ids": ["east-tree"]}
    calls = respond_with(monkeypatch, arguments)
    before = project.model_dump(mode="json")
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        store.create(project.id, intent, run_id="run", snapshot_version=project.state_version,
                     plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run("run", project.id, LocalPlanner("test"))
    assert len(calls) == 0
    assert result.state.status == "waiting_approval"
    data = result.state.last_result.data
    assert data["resolved_zone_ids"] == ["west"] and data["resolved_object_ids"] == []
    assert data["target_ids"] == ["west-tree-0", "west-tree-1", "west-tree-2"]
    preview = app.get_change_set_preview(project.id, data["change_set"]["id"], data["change_set"]["digest"])
    assert not preview.additions
    if operation == "delete":
        assert set(preview.deletion_ids) == set(data["target_ids"])
        assert not preview.updates
    else:
        assert not preview.deletion_ids
        assert {obj.id for obj in preview.updates} == set(data["target_ids"])
        assert all(obj.locked for obj in preview.updates)
    assert app.get(project.id).model_dump(mode="json") == before


def test_model_cannot_select_one_arbitrary_tree_from_a_source_zone(monkeypatch, tmp_path):
    app, project = populated_project()
    intent = intent_for_scope(count=1)
    respond_with(monkeypatch, {**model_arguments(count=1), "object_ids": ["west-tree-0"]})
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        store.create(project.id, intent, run_id="run", snapshot_version=project.state_version,
                     plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run("run", project.id, LocalPlanner("test"))
    assert result.state.status == "waiting_question"
    assert result.state.last_result.error.code == "OBJECT_SELECTION_REQUIRED"
    assert result.state.last_result.data["object_selection"] == {"requested_count": 1, "found_count": 3}
    assert result.state.pending_approval is None and not app.changes._previews


def test_direct_gateway_still_rejects_model_objects_substituted_for_the_source_zone():
    app, project = populated_project()
    arguments = {**model_arguments(), "object_ids": [f"west-tree-{index}" for index in range(3)]}
    with pytest.raises(GatewayRejected, match="scope"):
        ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent_for_scope()),
                              ToolCall(name="prepare_existing_change", arguments=arguments))
    assert not app.changes._previews


def test_unknown_source_zone_is_still_rejected_by_the_domain(monkeypatch):
    app, project = populated_project()
    intent = intent_for_scope(zones=["missing-zone"])
    respond_with(monkeypatch, model_arguments())
    decision = LocalPlanner("test")({"run": {"intent": intent.model_dump(mode="json")}})
    result = ToolGateway(app).call(GatewayContext(project_id=project.id, intent=intent), decision.tool)
    assert result.status == "failed"
    assert result.error.code == "TOOL_REJECTED"
    assert not app.changes._previews
