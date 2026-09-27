"""Complete explicit placement briefs do not rediscover unrelated scope."""
from contextlib import closing

import pytest
from shapely.geometry import box, mapping

from app.agent_runtime.contracts import AgentIntent, Goal, SelectionContext, ToolError, ToolResult
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.gateway import ToolGateway
from app.agent_runtime.selection import bind_selection
from app.agent_runtime.store import AgentRunStore
from test_placement_allocation import application


def bound_intent(count, zone="west"):
    text = f"На участке {zone.title()} посади {count} деревьев вдоль зданий. Породу выбери сам, отступы соблюдай."
    return AgentIntent(raw_text=text, source_turns=[text], goal=Goal(operation="place", target_count=count),
        scope_mode="explicit", explicit_zone_ids=[zone], plant_kind="tree", arrangement="building_contour",
        delegations=[{"slot": "species", "strategy": "agent"}], hard_constraints=[
            {"rule_id": "pp743-3.6.3-building", "source_text": "отступы соблюдай", "policy_owned": True},
            {"rule_id": "pp743-3.6.3-road-edge", "source_text": "отступы соблюдай", "policy_owned": True}])


def no_scope_planning(_context):
    raise AssertionError("The user already supplied the exact zone and placement choices")


@pytest.mark.parametrize("count,zone,expected", [(6, "west", "exact"), (70, "west", "partial"), (70, "east", "impossible")])
def test_complete_explicit_brief_reaches_real_capacity_without_scope_loop(tmp_path, monkeypatch, count, zone, expected):
    app, project = application()
    project.geometry.feature_collection["features"].extend([
        {"type": "Feature", "properties": {"kind": "building"}, "geometry": mapping(box(75, 75, 125, 125))},
        {"type": "Feature", "properties": {"kind": "road"}, "geometry": mapping(box(5, 150, 15, 200))}])
    if expected == "impossible":
        project.geometry.feature_collection["features"].extend([
            {"type": "Feature", "properties": {"kind": "building"}, "geometry": mapping(box(425, 25, 475, 75))},
            {"type": "Feature", "properties": {"kind": "water"}, "geometry": mapping(box(400, 0, 500, 100))}])
    app.repository.save(project)
    project = app.get(project.id)
    before = project.model_dump(mode="json")
    calls = []
    gateway = ToolGateway(app)
    execute = gateway.call

    def record_call(context, call):
        calls.append(call)
        return execute(context, call)

    monkeypatch.setattr(gateway, "call", record_call)
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        run = store.create(project.id, bound_intent(count, zone), snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, gateway).run(run.state.run_id, project.id, no_scope_planning)
        assert [call.name for call in calls] == ["prepare_placement"]
        assert calls[0].arguments["zone_ids"] == [zone]
        assert calls[0].arguments["target_count"] == count
        assert calls[0].arguments["quantity_mode"] == "target"
        assert len(calls[0].arguments["condition_rule_ids"]) == 2
        assert result.state.resolved_scope.zone_ids == [zone]
        assert result.state.candidate_zone_ids == []
        outcome = result.state.last_result.data["placement_outcome"]
        assert outcome["status"] == expected
        assert outcome["requested"] == count
        assert outcome["shortfall"] == count - outcome["found"]
        assert outcome["search_exhaustive"] is False
        if expected == "exact":
            assert result.state.status == "waiting_approval"
            assert result.state.last_result.verification.status == "verified"
        else:
            assert result.state.status == "waiting_question"
            assert result.state.pending_question["slot"] == "capacity"
            assert result.state.pending_approval is None
            assert outcome["reason"] and outcome["remedy_options"]
        assert app.get(project.id).model_dump(mode="json") == before


@pytest.mark.parametrize("scope,count,zone", [("explicit", 3, "west"), ("selection", 11, "east")])
@pytest.mark.parametrize("species", ["delegated", "specified"])
def test_ready_area_placement_preserves_selection_or_explicit_scope_and_species(tmp_path, scope, count, zone, species):
    app, project = application()
    scope_text = "выделенном участке" if scope == "selection" else f"участке {zone.title()}"
    species_text = "Породу выбери сам." if species == "delegated" else "Используй рябину."
    source = f"На {scope_text} посади {count} деревьев по площади. {species_text}"
    intent = AgentIntent(raw_text=source, source_turns=[source], goal=Goal(operation="place", target_count=count),
        scope_mode="explicit", explicit_zone_ids=[zone] if scope == "explicit" else [],
        plant_kind="tree", arrangement="area",
        species_ids=["sorbus-aucuparia@2026-08-28.1"] if species == "specified" else [],
        delegations=[{"slot": "species", "strategy": "agent"}] if species == "delegated" else [])
    if scope == "selection":
        intent = bind_selection(intent, SelectionContext(project_id=project.id, state_version=project.state_version,
            plan_version=project.plan.version, zone_ids=[zone], object_ids=[]), project)
        assert intent.selection_binding.zone_ids == [zone]
    before = project.model_dump(mode="json")
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        run = store.create(project.id, intent, snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, ToolGateway(app)).run(run.state.run_id, project.id, no_scope_planning)
        assert result.state.status == "waiting_approval", result.state.model_dump_json()
        assert len(result.state.tool_calls) == 1
        assert result.state.intent == intent
        assert result.state.resolved_scope.zone_ids == [zone]
        data = result.state.last_result.data
        assert data["resolved_zone_ids"] == [zone]
        assert data["placement_outcome"]["found"] == data["placement_outcome"]["requested"] == count
        if species == "specified":
            assert data["species_revision_ids"] == intent.species_ids
        assert app.get(project.id).model_dump(mode="json") == before


def test_failed_bound_preview_is_not_capacity_or_a_scope_retry(tmp_path, monkeypatch):
    app, project = application()
    calls = []
    gateway = ToolGateway(app)

    def unavailable(_context, call):
        calls.append(call)
        return ToolResult(call_id=call.call_id, name=call.name, status="failed",
            error=ToolError(code="DOMAIN_UNAVAILABLE", message="Сервис расчёта недоступен", retryable=True))

    monkeypatch.setattr(gateway, "call", unavailable)
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        run = store.create(project.id, bound_intent(70), snapshot_version=project.state_version, plan_version=project.plan.version)
        result = AgentEngine(store, gateway).run(run.state.run_id, project.id, no_scope_planning)
        assert len(calls) == 1 and calls[0].name == "prepare_placement"
        assert result.state.status == "waiting_question"
        assert result.state.pending_question["slot"] == "preview"
        assert result.state.pending_question["code"] == "DOMAIN_UNAVAILABLE"
        assert result.state.pending_approval is None
        assert result.state.intent.goal.target_count == 70
        assert result.state.last_result.data is None
        assert app.get(project.id).state_version == project.state_version
