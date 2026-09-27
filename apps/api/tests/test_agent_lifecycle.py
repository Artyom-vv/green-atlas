from app.composition import get_runtime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.agent_runtime.contracts import AgentDecision, AgentIntent, Goal, ResolvedScope, ToolCall, ToolResult
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.routes import cancel_run, list_runs, resume_run
from app.agent_runtime.store import AgentRunStore
from app.agent_runtime import routes


@pytest.fixture
def lifecycle(monkeypatch, tmp_path):
    from app import api

    store = AgentRunStore(tmp_path / "runs.sqlite3")
    user_intent = AgentIntent(raw_text="Посади 6 деревьев на участке west", goal=Goal(operation="place", target_count=6),
                              scope_mode="explicit", explicit_zone_ids=["west"])
    project = SimpleNamespace(state_version=8, plan=SimpleNamespace(version=4, objects=[]),
                              planting_zones=[SimpleNamespace(id="west")])
    monkeypatch.setattr(get_runtime(), "application", SimpleNamespace(get=lambda *_args, **_kwargs: project))
    monkeypatch.setattr(routes, "_store", lambda: store)
    yield store, user_intent, project
    store.close()


@pytest.mark.parametrize("status", ["queued", "running", "waiting_question", "waiting_approval", "failed"])
def test_cancel_is_idempotent_and_clears_pending_actions(lifecycle, status):
    store, user_intent, _ = lifecycle
    record = store.create("project", user_intent, run_id="run")
    store.checkpoint("project", "run", expected_revision=record.revision, state=record.state.model_copy(update={
        "status": status, "pending_approval": {"preview_ref": "preview"}, "pending_question": {"slot": "capacity"},
        "placement_retry": ToolCall(name="prepare_placement"),
    }), kind="test_state", payload={})
    cancelled = cancel_run("project", "run")
    assert cancelled.state.status == "cancelled"
    assert cancelled.state.pending_approval is None
    assert cancelled.state.pending_question is None
    assert cancelled.state.placement_retry is None
    assert cancel_run("project", "run").revision == cancelled.revision


def test_cancel_during_planning_discards_decision_without_executing_it(lifecycle):
    store, user_intent, _ = lifecycle
    store.create("project", user_intent, run_id="run")

    class Gateway:
        registry = CapabilityRegistry()
        def call(self, *_args):
            raise AssertionError("Cancelled decision must not execute")

    def choose(_context):
        cancel_run("project", "run")
        return AgentDecision(action="tool", tool=ToolCall(name="project_context"))

    result = AgentEngine(store, Gateway()).run("run", "project", choose)
    assert result.state.status == "cancelled"
    assert not result.state.tool_calls


def test_cancel_during_preview_discards_result_and_cannot_offer_approval(lifecycle):
    store, user_intent, _ = lifecycle
    store.create("project", user_intent, run_id="run")

    class Gateway:
        registry = CapabilityRegistry()
        def call(self, _context, request):
            cancel_run("project", "run")
            return ToolResult(call_id=request.call_id, name=request.name, status="succeeded", data={})

    result = AgentEngine(store, Gateway()).run("run", "project", lambda _: AgentDecision(
        action="tool", tool=ToolCall(name="prepare_placement")))
    assert result.state.status == "cancelled"
    assert result.state.pending_approval is None


def test_resume_uses_fresh_project_and_keeps_audit_but_discards_old_preview(lifecycle):
    store, user_intent, project = lifecycle
    created = store.create("project", user_intent, run_id="run", snapshot_version=1, plan_version=1)
    store.checkpoint("project", "run", expected_revision=created.revision, state=created.state.model_copy(update={
        "status": "failed", "step": 64, "pending_approval": {"preview_ref": "old"}, "tool_fingerprints": ["old"],
    }), kind="run_failed", payload={"code": "STALE_PROJECT"})
    resumed = resume_run("project", "run")
    assert resumed.state.status == "queued"
    assert resumed.state.step == 0
    assert resumed.state.snapshot_version == project.state_version
    assert resumed.state.plan_version == project.plan.version
    assert resumed.state.resolved_scope.zone_ids == ["west"]
    assert resumed.state.resolved_scope.source_revision == project.state_version
    assert resumed.state.last_result is None
    assert resumed.state.pending_approval is None
    assert not resumed.state.tool_fingerprints
    assert [event.kind for event in resumed.events] == ["run_failed", "run_restarted"]


def test_resume_rejects_changed_explicit_scope_and_cross_project_access(lifecycle):
    store, user_intent, project = lifecycle
    store.create("project", user_intent, run_id="run")
    cancel_run("project", "run")
    project.planting_zones = []
    with pytest.raises(HTTPException) as missing:
        resume_run("project", "run")
    assert missing.value.status_code == 409
    with pytest.raises(HTTPException) as cross_project:
        cancel_run("other", "run")
    assert cross_project.value.status_code == 404


def test_list_runs_is_project_scoped_and_bounded(lifecycle):
    store, user_intent, _ = lifecycle
    for index in range(3):
        store.create("project", user_intent, run_id=f"run-{index}")
    store.create("other", user_intent, run_id="other-run")
    records = list_runs("project", limit=2)
    assert len(records) == 2
    assert all(record.state.project_id == "project" for record in records)


def test_quantity_only_remedy_preserves_selected_zone_species_and_arrangement(lifecycle, monkeypatch):
    store, user_intent, project = lifecycle
    delegated = user_intent.model_copy(update={
        "scope_mode": "delegated", "explicit_zone_ids": [], "goal": Goal(operation="place", target_count=70),
    })
    created = store.create("project", delegated, run_id="run")
    result = ToolResult(call_id="capacity", name="prepare_placement", status="partial", data={
        "placement_outcome": {"status": "partial", "requested": 70, "found": 3, "shortfall": 67},
        "resolved_zone_ids": ["west"], "species_revision_ids": ["oak@1"],
        "arrangement": "building_contour", "plant_kind": "tree",
    })
    waiting = created.state.model_copy(update={
        "status": "waiting_question", "pending_question": {"slot": "capacity", "question": "Как изменить задание?"},
        "last_result": result,
        "resolved_scope": ResolvedScope(project_id="project", zone_ids=["west"], basis="agent", source_revision=1),
    })
    store.checkpoint("project", "run", expected_revision=created.revision, state=waiting, kind="tool_result", payload=result.model_dump(mode="json"))
    monkeypatch.setattr(routes.local, "configured_model", lambda: "model")

    class NoCompiler:
        def __init__(self, _model):
            raise AssertionError("A quantity-only typed amendment must preserve saved facts without recompilation")

    monkeypatch.setattr(routes, "IntentCompiler", NoCompiler)
    resumed = routes.answer_run("project", "run", routes.AgentRunAnswer(
        text="Измени количество растений на 3. Сохрани участок, схему и породы."))
    assert resumed.state.status == "queued"
    assert resumed.state.intent.goal.target_count == 3
    assert resumed.state.intent.scope_mode == "explicit"
    assert resumed.state.intent.explicit_zone_ids == ["west"]
    assert resumed.state.intent.species_ids == ["oak@1"]
    assert resumed.state.intent.arrangement == "building_contour"
    assert resumed.state.resolved_scope.zone_ids == ["west"]
    assert resumed.state.snapshot_version == project.state_version
    assert resumed.state.pending_approval is None
    assert resumed.state.last_result is None
    call, _ = AgentEngine._normalize_tool_call(resumed.state, ToolCall(name="prepare_placement", arguments={
        "target_count": 70, "species_revision_ids": ["other"], "arrangement": "area",
    }))
    assert call.arguments["target_count"] == 3
    assert call.arguments["species_revision_ids"] == ["oak@1"]
    assert call.arguments["arrangement"] == "building_contour"


def test_quantity_amendment_does_not_swallow_extra_instructions():
    from app.agent_runtime.semantic_guardrails import preserving_quantity_amendment

    assert preserving_quantity_amendment("Измени количество растений на 3. Сохрани участок, схему и породы.") == 3
    assert preserving_quantity_amendment("Измени количество растений на 3. Сохрани участок, схему и породы. Ещё перенеси их на участок 2.") is None
    assert preserving_quantity_amendment("Измени количество растений на 3. Сохрани участок, но измени породы.") is None
