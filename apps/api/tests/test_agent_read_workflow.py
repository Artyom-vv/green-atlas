"""Read completion requires the requested, complete authoritative result."""
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from app import agent_tools, planning_assistant as local
from app.agent_runtime.contracts import AgentDecision, AgentIntent, Goal, ReadIntent, ToolCall, ToolResult
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.gateway import GatewayContext, GatewayRejected, ToolGateway
from app.agent_runtime.intent import IntentCompiler
from app.agent_runtime.read_workflow import compile_read_intent, read_arguments, read_progress
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.store import AgentRunStore
from app.contracts import ValidationIssue
from test_agent_intent_safety import seeded
from test_placement_allocation import application


def intent(capability="plan_issues", **overrides):
    return AgentIntent.model_validate({"raw_text": "Проверь текущий план посадок", "goal": {"operation": "inspect"},
        "scope_mode": "explicit", "explicit_zone_ids": ["west"], "read": {"capability": capability}, **overrides})


def never_plan(_context):
    raise AssertionError("A supported read has a bounded domain workflow")


def run_read(tmp_path, app, project, value, gateway=None):
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, value, snapshot_version=project.state_version, plan_version=project.plan.version)
        return AgentEngine(store, gateway or ToolGateway(app)).run(record.state.run_id, project.id, never_plan)


def mock_draft(monkeypatch, **changes):
    draft = {"operation": "inspect", "scope_mode": "project", "zone_labels": [], "object_ids": [],
             "target_count": None, "plant_kind": None, "arrangement": None, "species_ids": [],
             "delegations": [], "preferences": [], "hard_constraints": [], "unresolved_requirements": [], **changes}
    monkeypatch.setattr(local, "local_json", lambda *_args, **_kwargs: {"message": {"content": json.dumps(draft)}})


def test_actual_inspect_starter_discards_model_invented_requirements(monkeypatch, tmp_path):
    text = "Проверь текущий план посадок и покажи найденные ограничения."
    mock_draft(monkeypatch, hard_constraints=[{"rule_id": "pp743-3.6.3-building", "source_text": text, "policy_owned": True}],
               unresolved_requirements=["показать найденные ограничения"], preferences=[{"key": "density", "value": "high"}])
    value = IntentCompiler("test").compile(text)
    assert value.read == ReadIntent(capability="plan_issues")
    assert not value.hard_constraints and not value.unresolved_requirements and not value.preferences
    app, project = application()
    final = run_read(tmp_path, app, project, value)
    assert final.state.status == "finished" and final.state.step == 1
    assert final.state.read_outcome.total == 0
    assert final.state.read_outcome.source == "saved_plan_validation"
    assert "не новая полная проверка" in final.state.read_outcome.caveat
    assert not final.state.pending_approval


@pytest.mark.parametrize("text,kind", [
    ("Подбери подходящие породы деревьев для участка Допустимая область 2.", "tree"),
    ("Подбери подходящие породы деревьев и кустарников для участка Допустимая область 2.", "mixed"),
])
def test_actual_species_request_pins_source_zone_and_kind(monkeypatch, text, kind):
    mock_draft(monkeypatch, operation="zones", scope_mode="delegated", zone_labels=["West"])
    value = IntentCompiler("test").compile(text, project_context={"zones": [
        {"id": "west", "label": "West", "number": 1}, {"id": "east", "label": "Допустимая область 2", "number": 2}]})
    assert value.read == ReadIntent(capability="species_shortlist")
    assert value.goal.operation == "inspect" and value.explicit_zone_ids == ["east"]
    assert value.plant_kind == kind
    assert read_arguments(value, None) == {"zone_ids": ["east"], "object_ids": [], "kind": kind if kind != "mixed" else None}


@pytest.mark.parametrize("text,scope,zones", [
    ("Проверь текущий план посадок", "project", []),
    ("Проверь текущий план посадок на участке West", "explicit", ["west"]),
    ("Проверь текущий план посадок на выбранном участке", "selection", []),
])
def test_model_scope_cannot_narrow_source_read(monkeypatch, text, scope, zones):
    mock_draft(monkeypatch, scope_mode="explicit", zone_labels=["East"])
    value = IntentCompiler("test").compile(text, project_context={"zones": [
        {"id": "west", "label": "West", "number": 1}, {"id": "east", "label": "East", "number": 2}]})
    assert value.scope_mode == scope and value.explicit_zone_ids == zones
    if scope == "selection":
        with pytest.raises(ValueError):
            read_arguments(value, None)


def test_ambiguous_source_label_does_not_read_both_zones(monkeypatch):
    mock_draft(monkeypatch, scope_mode="explicit", zone_labels=["West"])
    with pytest.raises(ValueError, match="неоднозначно"):
        IntentCompiler("test").compile("Проверь план на участке West", project_context={"zones": [
            {"id": "west", "label": "West"}, {"id": "east", "label": "West"}]})


def test_missing_read_scope_without_any_project_zones_asks(monkeypatch, tmp_path):
    mock_draft(monkeypatch, scope_mode="explicit", zone_labels=["Участок 2"])
    value = IntentCompiler("test").compile("Проверь план на участке 2", project_context={"zones": []})
    app, project = application()
    final = run_read(tmp_path, app, project, value)
    assert final.state.status == "waiting_question" and final.state.step == 0


@pytest.mark.parametrize("text", ["Покажи историю изменений", "Покажи каталог пород"])
def test_unrelated_read_does_not_become_saved_diagnostics(text):
    assert compile_read_intent(text, "inspect", zone_labels=[], object_ids=[]) is None


def test_two_read_results_cannot_be_silently_reduced_to_one():
    with pytest.raises(ValueError, match="разные результаты"):
        compile_read_intent("Проверь план и подбери породы", "inspect", zone_labels=[], object_ids=[])


@pytest.mark.parametrize("extra", [
    {"goal": {"operation": "inspect", "target_count": 2}}, {"plant_kind": "shrub"},
    {"preferences": [{"key": "density", "value": "high"}]}, {"species_ids": ["tilia"]},
    {"unresolved_requirements": ["только новые нарушения"]},
    {"read": {"capability": "plan_issues", "unsupported_requirements": ["не ближе 10 метров"]}},
])
def test_unsupported_query_filters_ask_before_any_read(tmp_path, extra):
    app, project = application()
    final = run_read(tmp_path, app, project, intent(**extra))
    assert final.state.status == "waiting_question" and final.state.step == 0
    assert final.state.read_outcome is None and final.state.last_result is None
    assert final.state.intent.goal == intent(**extra).goal


@pytest.mark.parametrize("text", [
    "Проверь план 2 деревьев на участке 2", "Проверь план только кустарников на участке 2",
    "Подбери морозостойкие породы для участка 2", "Проверь план без ошибок дорог",
    "Подбери породы с отступом 10 метров на участке 2",
])
def test_query_qualifiers_cannot_vanish_in_zone_number_or_allowed_words(monkeypatch, tmp_path, text):
    mock_draft(monkeypatch, scope_mode="explicit", zone_labels=["West"])
    value = IntentCompiler("test").compile(text, project_context={"zones": [{"id": "west", "label": "West", "number": 2}]})
    app, project = application()
    final = run_read(tmp_path, app, project, value)
    assert final.state.status == "waiting_question" and final.state.step == 0


def test_pagination_uses_all_250_authoritative_items_not_compact_excerpt(tmp_path):
    app, project = seeded()
    project.plan.issues = [ValidationIssue(id=f"issue-{index}", severity="warning", code="CHECK", title="Check",
        description="Saved fact", object_id="tree-a") for index in range(250)]
    project.plan.issues.append(ValidationIssue(id="global", severity="error", code="GLOBAL", title="Global", description="Global"))
    app.repository.save(project)
    project = app.get(project.id)
    before = project.model_dump(mode="json")
    final = run_read(tmp_path, app, project, intent())
    assert final.state.status == "finished"
    outcome = final.state.read_outcome
    assert outcome.total == 250 and outcome.pages == 3 and len(outcome.evidence_refs) == 3
    assert outcome.zone_ids == ["west"] and outcome.global_issues_excluded
    pages = [item.payload["read_page"] for item in final.events if item.kind == "tool_result"]
    assert [page["offset"] for page in pages] == [0, 100, 200]
    assert [len(page["item_ids"]) for page in pages] == [100, 100, 50]
    assert len(final.state.last_result.data["items"]) < 50  # display excerpt is not completion evidence
    assert app.get(project.id).model_dump(mode="json") == before


@pytest.mark.parametrize("kind", ["tree", "shrub", "mixed"])
def test_species_completion_preserves_real_statuses_and_capacity_caveat(tmp_path, kind):
    app, project = application()
    before = project.model_dump(mode="json")
    value = intent("species_shortlist", plant_kind=kind)
    final = run_read(tmp_path, app, project, value)
    assert final.state.status == "finished"
    assert final.state.read_outcome.total > 0 and final.state.read_outcome.pages == 1
    assert "не заменяют проверенный preview" in final.state.read_outcome.caveat
    args = read_arguments(value, None)
    raw = agent_tools.REGISTRY["species_shortlist"].execute(app, project.id, agent_tools.REGISTRY["species_shortlist"].arguments(**args))
    by_id = {item.species.id: item for item in raw}
    for item in final.state.last_result.data["items"]:
        assert item["status"] == by_id[item["species"]["id"]].status
        assert item["estimated_capacity"] == by_id[item["species"]["id"]].estimated_capacity
    assert final.state.read_outcome.total == len(raw)
    assert app.get(project.id).model_dump(mode="json") == before


def test_empty_species_list_is_complete_read_not_impossible_placement(tmp_path):
    app, project = application()
    cap = replace(CapabilityRegistry().get("species_shortlist"), executor=lambda *_args: [])
    final = run_read(tmp_path, app, project, intent("species_shortlist"), ToolGateway(app, CapabilityRegistry((cap,))))
    assert final.state.status == "finished" and final.state.read_outcome.total == 0
    assert final.state.read_outcome.source == "species_suitability" and not final.state.pending_approval


@pytest.mark.parametrize("capability,arguments", [
    ("plan_issues", {}), ("plan_issues", {"zone_ids": ["east"]}),
    ("plan_issues", {"zone_ids": ["west"], "object_ids": ["tree-a"]}),
    ("plan_issues", {"zone_ids": ["west"], "severity": "warning"}),
    ("plan_issues", {"zone_ids": ["west"], "rule_ids": ["one-rule"]}),
    ("species_shortlist", {"object_ids": ["tree-a"]}),
    ("species_shortlist", {"zone_ids": ["west"], "object_ids": ["tree-a"], "kind": "tree"}),
    ("species_shortlist", {"zone_ids": ["west"], "kind": "shrub"}),
])
@pytest.mark.parametrize("context_zones", [frozenset(), frozenset({"east"})])
def test_direct_gateway_cannot_override_immutable_read_scope_or_filters(capability, arguments, context_zones):
    app, project = seeded()
    value = intent(capability, plant_kind="tree" if capability == "species_shortlist" else None)
    with pytest.raises(GatewayRejected):
        ToolGateway(app).call(GatewayContext(project_id=project.id, intent=value, allowed_zone_ids=context_zones),
                              ToolCall(name=capability, arguments=arguments))


def issue_data(project, **changes):
    return {"source": "saved_plan_validation", "plan_version": project.plan.version, "total": 1, "offset": 0,
            "items": [{"id": "one"}], "next_offset": None, "global_issues_excluded": True,
            "scope_note": "Saved validation only", **changes}


@pytest.mark.parametrize("changes", [
    {"offset": 1}, {"total": 2}, {"items": []}, {"next_offset": 1},
    {"items": [{"id": "one"}, {"id": "one"}], "total": 2},
    {"items": [{"title": "No id"}]}, {"source": "new_validation"}, {"plan_version": 20},
    {"scope_note": ""}, {"global_issues_excluded": False},
    {"items": [], "total": 2, "next_offset": 1},
])
def test_malformed_authoritative_read_never_completes(tmp_path, changes):
    app, project = application()
    cap = replace(CapabilityRegistry().get("plan_issues"), executor=lambda *_args: issue_data(project, **changes))
    final = run_read(tmp_path, app, project, intent(), ToolGateway(app, CapabilityRegistry((cap,))))
    assert final.state.status == "waiting_question" and final.state.read_outcome is None
    assert final.state.last_result.error.code == "READ_RESULT_NOT_VERIFIED"


@pytest.mark.parametrize("changes", [
    {"items": [{"id": "one"}]}, {"total": 3, "next_offset": 2}, {"scope_note": "Changed source caveat"},
])
def test_pages_cannot_repeat_items_or_change_total_or_caveat(tmp_path, changes):
    app, project = application()
    def execute(_app, _pid, query):
        if query.offset == 0:
            return issue_data(project, total=2, next_offset=1)
        return issue_data(project, **{"total": 2, "offset": 1, "items": [{"id": "two"}], **changes})
    cap = replace(CapabilityRegistry().get("plan_issues"), executor=execute)
    final = run_read(tmp_path, app, project, intent(), ToolGateway(app, CapabilityRegistry((cap,))))
    assert final.state.status == "waiting_question" and final.state.read_outcome is None


@pytest.mark.parametrize("status", ["partial", "blocked", "cancelled", "failed", "in_progress"])
def test_non_successful_read_status_cannot_finish(tmp_path, status):
    app, project = application()
    class Gateway:
        registry = CapabilityRegistry()
        def call(self, _context, request):
            return ToolResult(name=request.name, call_id=request.call_id, status=status, data=issue_data(project))
    final = run_read(tmp_path, app, project, intent(), Gateway())
    assert final.state.status == "waiting_question" and final.state.read_outcome is None


@pytest.mark.parametrize("field,value", [("project_id", "other"), ("run_id", "other"), ("call_id", "other"),
    ("zone_ids", ["east"]), ("plan_version", 42)])
def test_saved_read_evidence_cannot_be_reused_for_another_identity_or_scope(tmp_path, field, value):
    app, project = application()
    record = run_read(tmp_path, app, project, intent())
    events = deepcopy(record.events)
    result = next(item for item in events if item.kind == "tool_result")
    result.payload["read_page"][field] = value
    with pytest.raises(ValueError):
        read_progress(record, events)


@pytest.mark.parametrize("boundary", ["question_answered", "run_restarted"])
def test_previous_epoch_complete_pages_are_not_reused(tmp_path, boundary):
    app, project = application()
    record = run_read(tmp_path, app, project, intent())
    event = record.events[-1].model_copy(update={"kind": boundary, "sequence": record.events[-1].sequence + 1, "payload": {}})
    record.events.append(event)
    assert read_progress(record, AgentEngine._current_epoch_events(record)) == (None, 0)


def test_unknown_read_cannot_finish_on_arbitrary_non_matching_fact(tmp_path):
    app, project = application()
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, intent(read=None), snapshot_version=project.state_version, plan_version=project.plan.version)
        final = AgentEngine(store, ToolGateway(app)).run(record.state.run_id, project.id,
            lambda _context: AgentDecision(action="finish", reason="Done", outcome_ref="invented-history"))
        assert final.state.status == "waiting_question" and final.state.read_outcome is None


def test_project_change_between_pages_restarts_read_from_zero(tmp_path):
    app, project = application()
    real = ToolGateway(app, CapabilityRegistry((replace(CapabilityRegistry().get("plan_issues"),
        executor=lambda _a, _p, q: issue_data(app.get(project.id), total=2, offset=q.offset,
            items=[{"id": "one" if q.offset == 0 else "two"}], next_offset=1 if q.offset == 0 else None)),)))
    class ChangingGateway:
        registry = real.registry
        offsets = []
        def call(self, context, request):
            self.offsets.append(request.arguments["offset"])
            if len(self.offsets) == 2:
                changed = app.get(project.id)
                changed.plan.version += 1
                app.repository.save(changed)
            return real.call(context, request)
    gateway = ChangingGateway()
    final = run_read(tmp_path, app, project, intent(), gateway)
    assert final.state.status == "finished"
    assert gateway.offsets == [0, 1, 0, 1]
    assert final.state.read_outcome.pages == 2 and final.state.read_outcome.total == 2
    current = app.get(project.id)
    assert final.state.read_outcome.snapshot_version == current.state_version
    assert final.state.read_outcome.plan_version == current.plan.version
    pages = [event.payload for event in final.events if event.kind == "tool_result" and event.payload.get("read_page")]
    assert pages[0]["call_id"] not in final.state.read_outcome.evidence_refs


def test_cancel_during_read_does_not_finish_on_late_success(tmp_path):
    app, project = application()
    with closing(AgentRunStore(tmp_path / "runs.sqlite3")) as store:
        record = store.create(project.id, intent(), snapshot_version=project.state_version, plan_version=project.plan.version)
        real = ToolGateway(app)
        class CancellingGateway:
            registry = real.registry
            def call(self, context, request):
                result = real.call(context, request)
                current = store.get(project.id, record.state.run_id)
                store.checkpoint(project.id, record.state.run_id, state=current.state.model_copy(update={"status": "cancelled"}),
                                 expected_revision=current.revision, kind="run_cancelled", payload={})
                return result
        final = AgentEngine(store, CancellingGateway()).run(record.state.run_id, project.id, never_plan)
        assert final.state.status == "cancelled" and final.state.read_outcome is None
        assert not any(event.kind == "read_completed" for event in final.events)
