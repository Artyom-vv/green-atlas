from contextlib import closing

import pytest

from app.agent_runtime.contracts import AgentIntent, EditIntent, Goal
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.existing_workflow import existing_arguments
from app.agent_runtime.gateway import ToolGateway
from app.agent_runtime.store import AgentRunStore
from test_agent_intent_safety import seeded


@pytest.mark.parametrize("action", ["move", "species", "lock", "unlock", "delete"])
def test_resolved_existing_actions_prepare_once_from_typed_intent_without_model(tmp_path, action):
    app, project = seeded()
    if action == "unlock":
        project.plan.objects[0].locked = True
        app.repository.save(project)
        project = app.get(project.id)
    original = project.model_dump(mode="json")
    edit = None if action == "delete" else EditIntent(action=action, **({"move_dx_m": 0.1, "move_dy_m": 0} if action == "move" else {}))
    intent = AgentIntent(raw_text="Typed source instruction", goal=Goal(operation="delete" if action == "delete" else "edit", target_count=1),
        scope_mode="explicit", explicit_object_ids=["tree-a"], edit=edit, plant_kind="tree",
        species_ids=["sorbus-aucuparia@2026-08-28.1"] if action == "species" else [])
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id, lambda _: pytest.fail("No model decision required"))
    assert result.state.status == "waiting_approval" and result.state.step == 1
    data = result.state.last_result.data
    preview = app.get_change_set_preview(project.id, data["change_set"]["id"], data["change_set"]["digest"])
    assert result.state.last_result.verification.status == "verified"
    if action == "delete":
        assert preview.deletion_ids == ["tree-a"] and not preview.updates
    else:
        changed = preview.updates[0]
        assert changed.id == "tree-a"
        if action == "move":
            assert changed.x == pytest.approx(30.1) and changed.y == 30
        if action in {"lock", "unlock"}:
            assert changed.locked is (action == "lock")
        if action == "species":
            assert changed.species_revision_id == intent.species_ids[0]
    assert app.get(project.id).model_dump(mode="json") == original


@pytest.mark.parametrize("change", [{"scope_mode": "delegated"}, {"edit": {"action": "species"}, "species_ids": []},
                                  {"edit": {"action": "move"}}, {"explicit_object_ids": []}])
def test_unresolved_decisions_are_not_filled_by_existing_workflow(change):
    intent = AgentIntent.model_validate({"raw_text": "Typed source", "goal": {"operation": "edit"},
        "scope_mode": "explicit", "explicit_object_ids": ["tree-a"], "edit": {"action": "lock"}, **change})
    assert existing_arguments(intent, 1) is None


def test_unsupported_explicit_species_gives_one_question_without_retries(tmp_path):
    app, project = seeded()
    intent = AgentIntent(raw_text="Замени породу на unknown@1", goal=Goal(operation="edit", target_count=1),
        scope_mode="explicit", explicit_object_ids=["tree-a"], edit=EditIntent(action="species"), species_ids=["unknown@1"])
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id, lambda _: pytest.fail("No guessing"))
    assert result.state.status == "waiting_question" and result.state.step == 1
    assert not result.state.pending_approval and result.state.intent.species_ids == ["unknown@1"]
