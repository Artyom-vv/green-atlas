
from application_factory import recompose_application
from app.planning.rules import effective_pattern_spacing
from app.composition import get_application, get_runtime
from types import SimpleNamespace

import pytest

from shapely.geometry import box, mapping, Polygon

from app import agent_tools
from app.agent_runtime.contracts import (
    AgentDecision,
    AgentIntent,
    AgentRunState,
    Goal,
    Preference,
    ResolvedScope,
    ToolCall,
    ToolResult,
)
from app.agent_runtime.engine import AgentEngine
from app.agent_runtime.gateway import GatewayContext, GatewayPolicy, GatewayRejected, ToolGateway
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.registry import ExistingChangePrepareQuery, _prepare_existing_change
from app.agent_runtime import routes as runtime_routes
from app.agent_runtime.store import AgentRunStore
from app.agent_runtime.verifier import verify_preview_data
from app.agent_runtime.placement import placement_outcome


def placement_data(found=70, requested=70, zones=None):
    zones = zones or ["zone-a"]
    proposal = {
        "requested": requested, "found": found, "shortfall": max(0, requested - found),
        "resolved_zone_ids": zones, "quantity_mode": "target",
        "change_set": {"id": "preview-1", "digest": "digest-1", "base_plan_version": 3,
                       "can_apply": bool(found), "candidate_results": [{"status": "allowed"} for _ in range(found)], "additions": [
                           {"kind": "tree", "planting_zone_id": zones[0]} for _ in range(found)
                       ]},
        "requires_confirmation": found == requested,
    }
    return {"proposal": proposal, "arrangement": "building_groves", "plant_kind": "tree", "requires_confirmation": found == requested,
            "placement_outcome": placement_outcome(proposal, requested=requested).model_dump(mode="json")}


def intent(text="Плотно рассадить 70 деревьев, участок и породу выбрать самостоятельно"):
    return AgentIntent(
        raw_text=text,
        goal=Goal(operation="place", target_count=70, acceptance=["norms_verified", "preview_before_commit"]),
        scope_mode="delegated",
        plant_kind="tree",
        arrangement="building_groves",
        preferences=[Preference(key="density", value="high", source_text="плотно")],
        delegations=[
            {"slot": "scope", "strategy": "best_evidence"},
            {"slot": "species", "strategy": "agent"},
        ],
    )


def test_intent_keeps_density_as_preference_not_constraint():
    value = intent()
    assert value.preferences[0].key == "density"
    assert value.hard_constraints == []
    assert {item.slot for item in value.delegations} == {"scope", "species"}


def test_registry_discovers_existing_capabilities_without_exposing_callables():
    registry = CapabilityRegistry()
    names = {item["name"] for item in registry.discover()}
    assert {"project_context", "find_zone_candidates", "preview_changes", "growth_objects"}.issubset(names)
    assert "prepare_existing_change" in names
    descriptor = registry.describe("preview_changes")
    assert descriptor["effect"] == "preview"
    assert "execute" not in descriptor
    assert "parameters" in descriptor
    commit = registry.describe("commit_change_set")
    assert commit["effect"] == "write"
    assert commit["approval_required"] is True


def test_edit_delete_workflow_exposes_one_canonical_preview_capability(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    record = store.create("project", intent(), snapshot_version=1, run_id="run-1")
    delete_state = record.state.model_copy(update={
        "intent": record.state.intent.model_copy(update={
            "goal": record.state.intent.goal.model_copy(update={"operation": "delete"}),
        }),
    })
    engine = AgentEngine(store, type("Gateway", (), {"registry": CapabilityRegistry()})())
    names = {item["name"] for item in engine._planner_capabilities(delete_state)}
    assert "prepare_existing_change" in names
    assert "preview_changes" not in names
    store.close()


def test_existing_change_adapter_builds_typed_task_without_mutating(monkeypatch):
    captured = {}

    def fake_prepare(app, project_id, task):
        captured["project_id"] = project_id
        captured["task"] = task
        return {"change_set": {"can_apply": True}, "requires_confirmation": True}

    monkeypatch.setattr("app.agent_planning.prepare_task", fake_prepare)
    app = SimpleNamespace(get=lambda _project_id, lightweight=False: SimpleNamespace(
        plan=SimpleNamespace(version=13),
    ))
    result = _prepare_existing_change(
        app,
        "project",
        ExistingChangePrepareQuery(
            base_plan_version=13,
            operation="edit",
            object_ids=["tree-1"],
            edit_action="move",
            move_dx_m=2.5,
        ),
    )
    assert result["operation"] == "edit"
    assert result["requires_confirmation"] is True
    assert captured["project_id"] == "project"
    assert captured["task"].values.scope == "objects"
    assert captured["task"].values.object_ids == ["tree-1"]
    assert captured["task"].values.edit_action == "move"
    assert captured["task"].values.move_dx_m == 2.5


def test_engine_context_keeps_typed_argument_contracts_for_planner(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    record = store.create("project", intent(), snapshot_version=1, run_id="run-1")
    engine = AgentEngine(store, type("Gateway", (), {"registry": CapabilityRegistry()})())
    placement = next(item for item in engine._context(record)["available_capabilities"]
                     if item["name"] == "prepare_placement")
    assert placement["argument_contract"]["arrangement"]["enum"] == [
        "area", "building_contour", "building_groves", "road_edges"
    ]
    assert placement["argument_contract"]["quantity_mode"]["enum"] == [
        "target", "maximum", "fill_available"
    ]
    store.close()


def test_engine_normalizes_delegated_species_index_to_domain_automatic_mode(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    record = store.create("project", intent(), snapshot_version=1, run_id="run-1")
    request = ToolCall(
        name="prepare_placement",
        arguments={"species_revision_ids": [0], "zone_ids": ["zone-a"]},
    )
    normalized, event = AgentEngine._normalize_tool_call(record.state, request)
    assert normalized.arguments["species_revision_ids"] == []
    assert event["to"] == "automatic"
    explicit = record.state.model_copy(update={
        "intent": record.state.intent.model_copy(update={"delegations": []})
    })
    unchanged, event = AgentEngine._normalize_tool_call(explicit, request)
    assert unchanged.arguments["species_revision_ids"] == [0]
    assert unchanged.arguments["spacing_policy"] == "canopy"
    assert event["changes"][-1]["field"] == "spacing_policy"
    store.close()


def test_engine_fills_explicit_scope_for_inspect_zones(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    record = store.create("project", intent(), snapshot_version=1, run_id="run-1")
    explicit = record.state.model_copy(update={
        "intent": record.state.intent.model_copy(update={"scope_mode": "explicit"}),
        "resolved_scope": ResolvedScope(
            project_id="project", zone_ids=["zone-a"], basis="user",
            criteria=["explicit_user_scope"], source_revision=1,
        ),
    })
    normalized, event = AgentEngine._normalize_tool_call(
        explicit, ToolCall(name="inspect_zones", arguments={})
    )
    assert normalized.arguments == {"zone_ids": ["zone-a"]}
    assert event["changes"][0]["field"] == "zone_ids"
    store.close()


def test_engine_maps_typed_density_preference_without_overriding_explicit_policy(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    record = store.create("project", intent(), snapshot_version=1, run_id="run-1")
    request = ToolCall(name="prepare_placement", arguments={"zone_ids": ["zone-a"]})
    normalized, event = AgentEngine._normalize_tool_call(record.state, request)
    assert normalized.arguments["spacing_policy"] == "canopy"
    assert event["changes"][-1] == {
        "field": "spacing_policy",
        "from": "density_preference",
        "to": "canopy",
    }

    russian = record.state.model_copy(update={
        "intent": record.state.intent.model_copy(update={
            "preferences": [Preference(key="density", value="плотно")],
        })
    })
    russian_request, _ = AgentEngine._normalize_tool_call(
        russian, ToolCall(name="prepare_placement", arguments={"zone_ids": ["zone-a"]})
    )
    assert russian_request.arguments["spacing_policy"] == "canopy"

    explicit = ToolCall(name="prepare_placement", arguments={
        "zone_ids": ["zone-a"], "spacing_policy": "open",
    })
    unchanged, event = AgentEngine._normalize_tool_call(record.state, explicit)
    assert unchanged.arguments["spacing_policy"] == "open"
    assert all(item["field"] != "spacing_policy" for item in event["changes"])
    store.close()


def test_engine_keeps_delegated_mixed_composition_complete(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    record = store.create("project", intent(), snapshot_version=1, run_id="run-1")
    mixed = record.state.model_copy(update={
        "intent": record.state.intent.model_copy(update={"plant_kind": "mixed"}),
    })
    placement, placement_event = AgentEngine._normalize_tool_call(
        mixed,
        ToolCall(name="prepare_placement", arguments={
            "species_revision_ids": ["tree-only"],
        }),
    )
    assert placement.arguments["species_revision_ids"] == []
    assert any(item["to"] == "automatic_tree_and_shrub" for item in placement_event["changes"])

    shortlist, shortlist_event = AgentEngine._normalize_tool_call(
        mixed,
        ToolCall(name="species_shortlist", arguments={"zone_ids": ["zone-a"], "kind": "tree"}),
    )
    assert shortlist.arguments["kind"] is None
    assert shortlist_event["changes"][-1]["to"] == "all_plant_kinds"
    store.close()


def test_verifier_rejects_preview_that_domain_did_not_allow():
    verification = verify_preview_data({
        "change_set": {
            "id": "preview-1",
            "digest": "digest-1",
            "base_plan_version": 3,
            "can_apply": False,
        }
    })
    assert verification.status == "rejected"
    assert "can_apply" not in verification.checks


def test_gateway_rejects_effect_outside_run_policy_before_domain_call():
    class NeverApplication:
        def get(self, *_args, **_kwargs):
            raise AssertionError("domain must not be called")

    gateway = ToolGateway(NeverApplication())
    try:
        gateway.call(
            GatewayContext(
                project_id="project",
                policy=GatewayPolicy(allowed_effects=frozenset({"read"})),
            ),
            ToolCall(name="preview_changes", arguments={}),
        )
    except GatewayRejected as error:
        assert "not allowed" in str(error)
    else:
        raise AssertionError("preview must be blocked by the policy")


def test_gateway_returns_structured_typed_argument_error():
    class ReadOnlyApplication:
        def get(self, *_args, **_kwargs):
            return SimpleNamespace(state_version=1, plan=None)

    result = ToolGateway(ReadOnlyApplication()).call(
        GatewayContext(project_id="project"),
        ToolCall(name="inspect_zones", arguments={}),
    )
    assert result.status == "failed"
    assert result.error.code == "INVALID_TOOL_ARGUMENTS"
    assert result.data["invalid_arguments"][0]["path"] == "zone_ids"


def test_gateway_rejects_commit_when_approval_digest_does_not_match():
    class NeverApplication:
        def get(self, *_args, **_kwargs):
            raise AssertionError("domain must not be called")

    gateway = ToolGateway(NeverApplication())
    try:
        gateway.call(
            GatewayContext(
                project_id="project",
                approved_change_set_id="preview-1",
                approved_digest="digest-1",
                policy=GatewayPolicy(allowed_effects=frozenset({"write"})),
            ),
            ToolCall(name="commit_change_set", arguments={
                "preview_id": "preview-1", "digest": "wrong", "base_plan_version": 1,
            }),
        )
    except GatewayRejected as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("commit must reject a mismatched digest")


def test_gateway_rejects_commit_without_approval_token_before_domain_call():
    class NeverApplication:
        def get(self, *_args, **_kwargs):
            raise AssertionError("domain must not be called")

    gateway = ToolGateway(NeverApplication())
    try:
        gateway.call(
            GatewayContext(
                project_id="project",
                policy=GatewayPolicy(allowed_effects=frozenset({"write"})),
            ),
            ToolCall(name="commit_change_set", arguments={
                "preview_id": "preview-1", "digest": "digest-1", "base_plan_version": 1,
            }),
        )
    except GatewayRejected as error:
        assert "approved preview" in str(error)
    else:
        raise AssertionError("write must require an approval token")


def test_gateway_rejects_zone_outside_verified_candidate_set():
    class NeverApplication:
        def get(self, *_args, **_kwargs):
            raise AssertionError("domain must not be called")

    gateway = ToolGateway(NeverApplication())
    try:
        gateway.call(
            GatewayContext(project_id="project", allowed_zone_ids=frozenset({"zone-a"})),
            ToolCall(name="inspect_zones", arguments={"zone_ids": ["zone-b"]}),
        )
    except GatewayRejected as error:
        assert "candidate set" in str(error)
    else:
        raise AssertionError("unverified zone must be blocked")


def test_zone_candidates_are_read_only_and_rank_building_context():
    building = Polygon([(8, 8), (12, 8), (12, 12), (8, 12)])
    zone_near = SimpleNamespace(id="near", label="Участок рядом", geometry=mapping(Polygon([(0, 0), (20, 0), (20, 20), (0, 20)])))
    zone_far = SimpleNamespace(id="far", label="Участок далеко", geometry=mapping(Polygon([(100, 100), (110, 100), (110, 110), (100, 110)])))
    project = SimpleNamespace(
        planting_zones=[zone_far, zone_near],
        plan=SimpleNamespace(objects=[]),
        geometry=SimpleNamespace(feature_collection={"features": [{"properties": {"kind": "building"}, "geometry": mapping(building)}]}),
    )
    app = SimpleNamespace(get=lambda _project_id: project)
    result = agent_tools._zone_candidates(app, "project", agent_tools.ZoneCandidateQuery(arrangement="building_groves", limit=2))
    assert result["selection_status"] == "not_resolved"
    assert result["items"][0]["zone_id"] == "near"
    assert result["items"][0]["nearby_buildings"] is True


def test_prepare_placement_bridge_keeps_model_at_decision_boundary(monkeypatch):
    captured = {}

    def fake_prepare(app, project_id, task, species_ids):
        captured["project_id"] = project_id
        captured["task"] = task
        captured["species_ids"] = species_ids
        return placement_data()["proposal"]

    monkeypatch.setattr("app.agent_planning.prepare_task", fake_prepare)
    app = SimpleNamespace(
        get=lambda _project_id, lightweight=False: SimpleNamespace(
            plan=SimpleNamespace(version=13)
        )
    )
    result = agent_tools._prepare_placement(
        app,
        "project",
        agent_tools.PlacementPrepareQuery(
            base_plan_version=13,
            zone_ids=["zone-a"],
            plant_kind="tree",
            arrangement="building_groves",
            target_count=70,
            species_revision_ids=["tilia-cordata@2026"],
        ),
    )
    assert result["requires_confirmation"] is True
    assert captured["project_id"] == "project"
    assert captured["species_ids"] == ["tilia-cordata@2026"]
    assert captured["task"].values.scope == "zones"
    assert captured["task"].values.zone_ids == ["zone-a"]
    assert captured["task"].values.species_mode == "specified"
    assert captured["task"].values.spacing_policy == "balanced"


def test_density_policy_changes_domain_sampling_spacing_without_changing_normative_gate(tmp_path):
    from app.contracts import PlacementMaskRequest
    from test_placement_allocation import application

    app, project = application()
    base = dict(
        mask_id="regular_grid",
        base_plan_version=project.plan.version,
        zone_ids=["west"],
        plant_kind="tree",
        placement_mode="count",
        target_count=20,
        species_revision_id="quercus-robur@2026-08-28.1",
    )
    open_spacing = effective_pattern_spacing(PlacementMaskRequest(**base, spacing_policy="open"))
    dense_spacing = effective_pattern_spacing(PlacementMaskRequest(**base, spacing_policy="canopy"))
    assert dense_spacing < open_spacing
    # The policy only changes candidate sampling. The ordinary preview
    # validator remains responsible for setbacks and plant-to-plant safety.
    assert open_spacing >= 0.5
    assert dense_spacing >= 0.5


def test_run_store_reopens_checkpoint_and_event_log(tmp_path):
    path = tmp_path / "runs.sqlite3"
    store = AgentRunStore(path)
    store.create("project", intent(), snapshot_version=7, run_id="run-1")
    scheduled, claimed = store.claim_execution("project", "run-1")
    assert claimed
    saved, started = store.start_execution("project", "run-1", scheduled.state.execution_attempt_id)
    assert started
    store.close()

    reopened = AgentRunStore(path)
    loaded = reopened.get("project", "run-1")
    assert loaded == saved
    assert loaded.revision == 3
    assert loaded.state.status == "running"
    assert loaded.state.snapshot_version == 7
    assert [(item.sequence, item.kind) for item in loaded.events] == [(2, "execution_scheduled"), (3, "run_started")]
    reopened.close()


def test_engine_recurses_after_tools_and_stops_at_approval(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")

    class FakeGateway:
        registry = CapabilityRegistry()

        def call(self, _context, request):
            data = {"preview": request.name == "prepare_placement"}
            if request.name == "find_zone_candidates":
                data = {"items": [{"zone_id": "zone-a"}]}
            if request.name == "prepare_placement":
                data = placement_data()
            return ToolResult(call_id=request.call_id, name=request.name, status="succeeded",
                              data=data,
                              evidence_refs=[request.call_id])

    record = store.create("project", intent(), snapshot_version=1, run_id="run-1")
    calls = []

    def choose(context):
        calls.append(context)
        results = [item for item in context["events"] if item["kind"] == "tool_result"]
        if not results:
            return AgentDecision(action="tool", tool=ToolCall(name="project_context"))
        if len(results) == 1:
            return AgentDecision(action="tool", tool=ToolCall(name="find_zone_candidates"))
        if len(results) == 2:
            return AgentDecision(action="tool", tool=ToolCall(name="inspect_zones", arguments={"zone_ids": ["zone-a"]}))
        if len(results) == 3:
            return AgentDecision(action="tool", tool=ToolCall(name="prepare_placement"))
        return AgentDecision(action="approval", preview_ref=results[-1]["payload"]["call_id"])

    engine = AgentEngine(store, FakeGateway())
    finished = engine.run("run-1", "project", choose)
    assert finished.state.status == "waiting_approval"
    assert len(calls) == 4
    assert [event.kind for event in finished.events] == [
        "execution_scheduled", "run_started", "decision", "tool_result", "decision", "tool_result", "decision", "tool_result",
        "decision", "tool_call_normalized", "tool_result", "approval_requested"
    ]
    assert finished.state.pending_approval["preview_ref"]
    store.close()


def test_engine_cannot_finish_without_an_outcome_reference(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent(), snapshot_version=1, run_id="run-1")
    engine = AgentEngine(store, type("Gateway", (), {"registry": CapabilityRegistry()})())

    def choose(_context):
        return AgentDecision(action="finish", outcome_ref="outcome-1")

    # A planner-authored reference cannot prove a requested plan mutation.
    finished = engine.run("run-1", "project", choose)
    assert finished.state.status == "failed"
    assert finished.state.failure.code == "FINISH_WITHOUT_VERIFIED_CHANGE"
    store.close()


def test_engine_persists_planner_failure_instead_of_leaving_run_running(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent().model_copy(update={"goal": Goal(operation="inspect")}), snapshot_version=1, run_id="run-1")
    engine = AgentEngine(store, type("Gateway", (), {"registry": CapabilityRegistry()})())
    calls = 0

    def choose(_context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("invalid typed decision")
        return AgentDecision(action="ask", missing_slot="scope", question="Уточните область.")

    failed = engine.run("run-1", "project", choose)
    assert failed.state.status == "failed"
    assert failed.state.failure.code == "PLANNER_FAILED"
    assert failed.state.failure.retryable is True
    # Re-entering the engine is not an explicit user restart. /resume owns
    # the new snapshot/attempt transition, covered through HTTP lifecycle.
    assert engine.run("run-1", "project", choose) == failed
    assert calls == 1
    store.close()


def test_engine_blocks_duplicate_tool_arguments_and_allows_one_strategy_change(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent().model_copy(update={"goal": Goal(operation="inspect")}), snapshot_version=1, run_id="run-1")
    gateway_calls = []

    class FakeGateway:
        registry = CapabilityRegistry()

        def call(self, _context, request):
            gateway_calls.append(request)
            return ToolResult(call_id=request.call_id, name=request.name,
                              status="succeeded", data={"items": [{"zone_id": "zone-a"}]})

    decisions = iter([
        AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        )),
        AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        )),
        AgentDecision(action="ask", missing_slot="scope", question="Уточните область."),
    ])
    result = AgentEngine(store, FakeGateway()).run(
        "run-1", "project", lambda _context: next(decisions)
    )
    assert result.state.status == "waiting_question"
    assert len(gateway_calls) == 1
    assert result.state.last_result.error.code == "REPEATED_TOOL_CALL"
    assert len(result.state.tool_fingerprints) == 2
    assert any(item.kind == "tool_result" and item.payload["status"] == "blocked"
               for item in result.events)
    store.close()


def test_engine_stops_a_model_that_repeats_the_same_tool_without_progress(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent(), snapshot_version=1, run_id="run-1")
    gateway_calls = []

    class FakeGateway:
        registry = CapabilityRegistry()

        def call(self, _context, request):
            gateway_calls.append(request)
            return ToolResult(call_id=request.call_id, name=request.name,
                              status="succeeded", data={"items": [{"zone_id": "zone-a"}]})

    def choose(_context):
        return AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        ))

    result = AgentEngine(store, FakeGateway()).run("run-1", "project", choose)
    assert result.state.status == "failed"
    assert result.state.failure.code == "AGENT_NO_PROGRESS"
    assert len(gateway_calls) == 1
    assert result.state.tool_fingerprints.count(result.state.tool_fingerprints[0]) == 3
    assert result.events[-1].kind == "run_failed"
    store.close()


def test_engine_retries_same_tool_after_stale_snapshot(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent().model_copy(update={"goal": Goal(operation="inspect")}), snapshot_version=1, run_id="run-1")
    gateway_calls = []

    class FakeGateway:
        registry = CapabilityRegistry()

        def call(self, _context, request):
            gateway_calls.append(request)
            if len(gateway_calls) == 1:
                return ToolResult(
                    call_id=request.call_id,
                    name=request.name,
                    status="stale",
                    resource_versions={"project": 2, "plan": 1},
                    error={"code": "STALE_PROJECT", "message": "stale", "retryable": True},
                )
            return ToolResult(call_id=request.call_id, name=request.name,
                              status="succeeded", data={"items": [{"zone_id": "zone-a"}]})

    decisions = iter([
        AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        )),
        AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        )),
        AgentDecision(action="ask", missing_slot="scope", question="Уточните область."),
    ])
    result = AgentEngine(store, FakeGateway()).run(
        "run-1", "project", lambda _context: next(decisions)
    )
    assert result.state.status == "waiting_question"
    assert len(gateway_calls) == 2
    assert result.state.snapshot_version == 2
    assert len(set(result.state.tool_fingerprints)) == 2
    store.close()


def test_approval_commits_only_the_saved_preview_reference(monkeypatch, tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    created = store.create("project", intent(), snapshot_version=7, plan_version=3, run_id="run-1")
    preview_data = placement_data()
    full_preview = preview_data["proposal"]["change_set"]
    for index, addition in enumerate(full_preview["additions"]):
        addition["id"] = f"tree-{index}"
    waiting = created.state.model_copy(update={
        "status": "waiting_approval",
        "pending_approval": {"preview_ref": "preview-call"},
    })
    with_preview = store.checkpoint(
        "project", "run-1", expected_revision=1, state=waiting,
        kind="tool_result",
        payload={
            "call_id": "preview-call",
            "name": "prepare_placement",
            "status": "succeeded",
            "data": preview_data,
        },
    )

    applied = SimpleNamespace(
        change_set_id="preview-1",
        plan_version=4,
        state_version=8,
        added_ids=[item["id"] for item in full_preview["additions"]],
        updated_ids=[],
        deleted_ids=[],
    )
    captured = {}

    def fake_apply(project_id, payload):
        captured["project_id"] = project_id
        captured["payload"] = payload
        captured["receipt"] = {**vars(applied), "project_id": project_id, "digest": payload.digest,
            "base_plan_version": payload.base_plan_version, "base_state_version": 7, "status": "applied"}
        return applied

    from app import api
    monkeypatch.setattr(get_application(), "get", lambda *_args, **_kwargs: SimpleNamespace(
        state_version=7, plan=SimpleNamespace(version=3)
    ))
    monkeypatch.setattr(get_application(), "get_change_set_preview", lambda *_args: SimpleNamespace(
        id=full_preview["id"], digest=full_preview["digest"],
        additions=[SimpleNamespace(**item) for item in full_preview["additions"]], updates=[], deletion_ids=[],
        model_dump=lambda **_kwargs: full_preview))
    monkeypatch.setattr(get_application(), "apply_change_set", fake_apply)
    monkeypatch.setattr(get_application(), "get_applied_change_set_receipt", lambda *_args: captured.get("receipt"))
    monkeypatch.setattr(runtime_routes, "_store", lambda: store)

    result = runtime_routes.approve_run(
        "project", "run-1", runtime_routes.AgentRunApproval(preview_ref="preview-call")
    )
    assert result.state.status == "finished"
    assert result.state.outcome_ref == "change-set:preview-1"
    assert captured["project_id"] == "project"
    assert captured["payload"].preview_id == "preview-1"
    assert captured["payload"].digest == "digest-1"
    assert [event.kind for event in result.events] == ["tool_result", "commit_started", "commit_applied"]
    store.close()


def test_answer_resumes_same_waiting_run_with_new_typed_intent(monkeypatch, tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    created = store.create("project", intent(), snapshot_version=7, run_id="run-1")
    waiting = created.state.model_copy(update={
        "status": "waiting_question",
        "pending_question": {"slot": "scope", "question": "Какой участок использовать?"},
        "tool_fingerprints": ["old-epoch"],
    })
    store.checkpoint(
        "project", "run-1", expected_revision=1, state=waiting,
        kind="question", payload=waiting.pending_question,
    )

    class FakeCompiler:
        def __init__(self, _model):
            pass

        def compile(self, text, *, project_context=None, full_project=None, source_turns=None):
            assert text.endswith("\n\nУчасток 6")
            assert project_context["id"] == "project"
            return intent(text)

    from app import api
    monkeypatch.setattr(runtime_routes, "_store", lambda: store)
    monkeypatch.setattr(runtime_routes.local, "configured_model", lambda: "local-model")
    monkeypatch.setattr(runtime_routes, "IntentCompiler", FakeCompiler)
    monkeypatch.setattr(runtime_routes, "project_context", lambda _project: {"id": "project"})
    monkeypatch.setattr(get_runtime(), "application", SimpleNamespace(
        get=lambda *_args, **_kwargs: SimpleNamespace(
            state_version=8, plan=SimpleNamespace(version=4),
        )
    ))

    resumed = runtime_routes.answer_run(
        "project", "run-1", runtime_routes.AgentRunAnswer(text="Участок 6")
    )
    assert resumed.state.status == "queued"
    assert resumed.state.pending_question is None
    assert resumed.state.snapshot_version == 8
    assert resumed.state.tool_fingerprints == []
    assert resumed.events[-1].kind == "question_answered"
    assert resumed.events[-1].payload["missing_slot"] == "scope"
    store.close()


def test_real_domain_dropped_target_becomes_honest_shortfall_without_mutation(tmp_path):
    from test_placement_allocation import application

    app, project = application()
    project.geometry.feature_collection["features"].append({
        "type": "Feature",
        "properties": {"kind": "building"},
        "geometry": mapping(box(75, 75, 125, 125)),
    })
    app.repository.save(project)
    run_intent = intent().model_copy(update={
        "scope_mode": "explicit",
        "arrangement": "building_contour",
        "explicit_zone_ids": ["west"],
        "delegations": [],
    })
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create(project.id, run_intent, snapshot_version=project.state_version, run_id="run-1")
    calls = []

    def choose(context):
        calls.append(context)
        results = [item for item in context["events"] if item["kind"] == "tool_result"]
        if not results:
            return AgentDecision(action="tool", tool=ToolCall(
                name="building_targets", arguments={"zone_ids": ["west"]}
            ))
        if len(results) == 1:
            return AgentDecision(action="tool", tool=ToolCall(
                name="species_shortlist", arguments={"zone_ids": ["west"], "kind": "tree"}
            ))
        if len(results) == 2:
            return AgentDecision(action="tool", tool=ToolCall(
                name="prepare_placement",
                arguments={
                    "base_plan_version": project.plan.version,
                    "zone_ids": ["west"],
                    "plant_kind": "tree",
                    "arrangement": "building_contour",
                    "target_count": 20,
                    "species_revision_ids": ["quercus-robur@2026-08-28.1"],
                },
            ))
        return AgentDecision(action="approval", preview_ref=results[-1]["payload"]["call_id"])

    engine = AgentEngine(store, ToolGateway(app))
    result = engine.run("run-1", project.id, choose)
    assert result.state.status == "waiting_question"
    assert result.state.resolved_scope.zone_ids == ["west"]
    assert result.state.last_result.name == "prepare_placement"
    assert result.state.last_result.status == "partial"
    assert result.state.last_result.data["requires_confirmation"] is False
    assert result.state.last_result.data["placement_outcome"]["requested"] == 70
    assert result.state.last_result.data["placement_outcome"]["status"] == "partial"
    assert result.state.pending_approval is None
    assert app.get(project.id).plan.objects == []
    assert len(calls) == 3
    store.close()


def test_real_domain_approval_commits_saved_preview_only(tmp_path, monkeypatch):
    from test_placement_allocation import application

    app, project = application()
    app = recompose_application(app, history=SimpleNamespace(record=lambda *_args, **_kwargs: None))
    project.geometry.feature_collection["features"].append({
        "type": "Feature",
        "properties": {"kind": "building"},
        "geometry": mapping(box(75, 75, 125, 125)),
    })
    app.repository.save(project)
    run_intent = intent().model_copy(update={
        "goal": Goal(operation="place", target_count=6, acceptance=["preview_before_commit"]),
        "scope_mode": "explicit",
        "explicit_zone_ids": ["west"],
        "delegations": [],
    })
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create(project.id, run_intent, snapshot_version=project.state_version,
                 plan_version=project.plan.version, run_id="run-1")

    def choose(context):
        results = [item for item in context["events"] if item["kind"] == "tool_result"]
        if not results:
            return AgentDecision(action="tool", tool=ToolCall(
                name="building_targets", arguments={"zone_ids": ["west"]}
            ))
        if len(results) == 1:
            return AgentDecision(action="tool", tool=ToolCall(
                name="species_shortlist", arguments={"zone_ids": ["west"], "kind": "tree"}
            ))
        return AgentDecision(action="tool", tool=ToolCall(
            name="prepare_placement",
            arguments={
                "base_plan_version": project.plan.version,
                "zone_ids": ["west"],
                "plant_kind": "tree",
                "arrangement": "building_contour",
                "target_count": 6,
                "species_revision_ids": ["sorbus-aucuparia@2026-08-28.1"],
            },
        ))

    preview = AgentEngine(store, ToolGateway(app)).run("run-1", project.id, choose)
    assert preview.state.status == "waiting_approval"
    preview_ref = preview.state.pending_approval["preview_ref"]

    from app import api
    monkeypatch.setattr(get_runtime(), "application", app)
    monkeypatch.setattr(runtime_routes, "_store", lambda: store)
    committed = runtime_routes.approve_run(
        project.id, "run-1", runtime_routes.AgentRunApproval(preview_ref=preview_ref)
    )
    assert committed.state.status == "finished"
    assert committed.state.outcome_ref.startswith("change-set:")
    assert len(app.get(project.id).plan.objects) == 6
    assert app.get(project.id).plan.version > project.plan.version
    assert [event.kind for event in committed.events][-1] == "commit_applied"
    store.close()


@pytest.mark.parametrize("found,status,tool_status", [(6, "exact", "succeeded"), (1, "partial", "partial"), (0, "impossible", "blocked")])
def test_capacity_contract_and_approval_boundary(monkeypatch, tmp_path, found, status, tool_status):
    from test_placement_allocation import application

    app, project = application()
    captured = []

    def prepare(_app, _project_id, task, _species):
        captured.append(task.values)
        return placement_data(found, 6, ["west"])["proposal"]

    monkeypatch.setattr("app.agent_planning.prepare_task", prepare)
    user_intent = AgentIntent(raw_text="На западном участке посади 6 деревьев", goal=Goal(operation="place", target_count=6),
                              scope_mode="explicit", explicit_zone_ids=["west"], plant_kind="tree", arrangement="area")
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create(project.id, user_intent, snapshot_version=project.state_version, plan_version=project.plan.version, run_id="run")
    result = AgentEngine(store, ToolGateway(app)).run("run", project.id, lambda _: AgentDecision(action="tool", tool=ToolCall(
        name="prepare_placement", arguments={"base_plan_version": project.plan.version, "target_count": 1,
                                            "quantity_mode": "fill_available"},
    )))
    assert captured[0].quantity == 6
    assert captured[0].quantity_mode == "target"
    assert captured[0].zone_ids == ["west"]
    assert result.state.last_result.status == tool_status
    outcome = result.state.last_result.data["placement_outcome"]
    assert outcome["status"] == status
    assert outcome["requested"] == 6
    assert outcome["shortfall"] == 6 - found
    assert outcome["search_exhaustive"] is False
    if status == "exact":
        assert result.state.status == "waiting_approval"
        assert result.state.last_result.verification.status == "verified"
    else:
        assert result.state.status == "waiting_question"
        assert result.state.pending_approval is None
        assert outcome["reason"] and outcome["remedy_options"]
        with pytest.raises(Exception) as rejected:
            runtime_routes._preview_from_run(result, result.state.last_result.call_id)
        assert rejected.value.status_code == 409
    assert app.get(project.id).state_version == project.state_version
    assert app.get(project.id).plan.objects == []
    store.close()


@pytest.mark.parametrize("counts,expected,final_zone", [([1, 6, 6, 6], "waiting_approval", "zone-1"), ([1, 2, 0, 6], "waiting_question", "zone-1"), ([0, 0, 0, 6], "waiting_question", "zone-0")])
def test_delegated_capacity_fallback_is_ranked_bounded_and_preserves_target(monkeypatch, tmp_path, counts, expected, final_zone):
    from test_placement_allocation import application
    from app.contracts import PlantingZoneAssignment

    app, project = application()
    project.planting_zones = [PlantingZoneAssignment(id=f"zone-{i}", label=f"Zone {i}", geometry=mapping(box(i * 100, 0, i * 100 + 80, 80)))
                              for i in range(4)]
    app.repository.save(project)
    prepared = []

    def prepare(_app, _project_id, task, _species):
        zone = task.values.zone_ids[0]
        prepared.append(task.values)
        return placement_data(counts[int(zone[-1])], 6, [zone])["proposal"]

    monkeypatch.setattr("app.agent_planning.prepare_task", prepare)
    user_intent = intent().model_copy(update={"goal": Goal(operation="place", target_count=6), "arrangement": "area"})
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    created = store.create(project.id, user_intent, snapshot_version=project.state_version, plan_version=project.plan.version, run_id="run")
    store.checkpoint(project.id, "run", expected_revision=created.revision, state=created.state.model_copy(update={
        "candidate_zone_ids": [f"zone-{i}" for i in range(4)],
        "resolved_scope": ResolvedScope(project_id=project.id, zone_ids=["zone-0"], basis="agent", source_revision=project.state_version,
                                         evidence_refs=["ranked-candidates", "initial-inspection"]),
    }), kind="scope_resolved", payload={})
    result = AgentEngine(store, ToolGateway(app)).run("run", project.id, lambda _: AgentDecision(action="tool", tool=ToolCall(
        name="prepare_placement", arguments={"base_plan_version": project.plan.version, "zone_ids": ["zone-0"]},
    )))
    assert result.state.status == expected
    assert result.state.resolved_scope.zone_ids == [final_zone]
    assert len(prepared) <= 3
    assert all(task.quantity == 6 and task.arrangement == "area" for task in prepared)
    fallbacks = [event for event in result.events if event.kind == "scope_fallback_started"]
    assert [event.payload["candidate_rank"] for event in fallbacks] == list(range(2, len(prepared) + 1))
    assert all(event.payload["capacity_evidence_ref"] for event in fallbacks)
    assert result.state.last_result.data["scope_search"]["selected_zone_ids"] == [final_zone]
    assert result.state.placement_retry is None
    if expected == "waiting_question":
        outcome = result.state.last_result.data["placement_outcome"]
        assert outcome["found"] == max(counts[:3])
        assert outcome["status"] == ("partial" if max(counts[:3]) else "impossible")
    assert app.get(project.id).plan.objects == []
    store.close()


def test_verifier_and_saved_approval_reject_forged_or_legacy_target(monkeypatch, tmp_path):
    from fastapi import HTTPException

    user_intent = intent().model_copy(update={"goal": Goal(operation="place", target_count=6)})
    data = placement_data(1, 1)
    assert verify_preview_data(data, tool_name="prepare_placement", intent=user_intent).status == "rejected"
    data["placement_outcome"] = placement_data(6, 6)["placement_outcome"]
    assert verify_preview_data(data, tool_name="prepare_placement", intent=user_intent).status == "rejected"
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    created = store.create("project", user_intent, run_id="run")
    for index, candidate in enumerate([data, {"change_set": placement_data()["proposal"]["change_set"]}]):
        created = store.checkpoint("project", "run", expected_revision=created.revision, state=created.state,
            kind="tool_result", payload={"call_id": f"old-{index}", "name": "prepare_placement", "status": "succeeded",
                                         "data": candidate, "verification": {"status": "verified", "checks": []}})
        with pytest.raises(HTTPException) as rejected:
            runtime_routes._preview_from_run(created, f"old-{index}")
        assert rejected.value.status_code == 409
    store.close()


def test_explicit_scope_cannot_be_replaced_with_other_existing_zone(tmp_path):
    from test_placement_allocation import application

    app, project = application()
    user_intent = AgentIntent(raw_text="Посади 6 деревьев в западной зоне", goal=Goal(operation="place", target_count=6),
                              scope_mode="explicit", explicit_zone_ids=["west"])
    with pytest.raises(GatewayRejected, match="explicit zones"):
        ToolGateway(app).call(GatewayContext(project_id=project.id, intent=user_intent), ToolCall(
            name="prepare_placement", arguments={"base_plan_version": project.plan.version, "arrangement": "area", "plant_kind": "tree", "target_count": 6, "zone_ids": ["east"]},
        ))
    with pytest.raises(GatewayRejected, match="user's target"):
        ToolGateway(app).call(GatewayContext(project_id=project.id, intent=user_intent), ToolCall(
            name="prepare_placement", arguments={"base_plan_version": project.plan.version, "arrangement": "area", "plant_kind": "tree", "target_count": 1, "zone_ids": ["west"]},
        ))


def test_model_cannot_request_approval_for_an_arbitrary_successful_read(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent(), run_id="run")

    class Gateway:
        registry = CapabilityRegistry()
        def call(self, _context, request):
            return ToolResult(call_id="read", name=request.name, status="succeeded", data={})

    decisions = iter([AgentDecision(action="tool", tool=ToolCall(name="project_context")),
                      AgentDecision(action="approval", preview_ref="read")])
    result = AgentEngine(store, Gateway()).run("run", "project", lambda _: next(decisions))
    assert result.state.status == "failed"
    assert result.state.failure.code == "APPROVAL_WITHOUT_PREVIEW"
    store.close()


def test_delegated_empty_scope_stops_with_setup_question(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent(), run_id="run")

    class Gateway:
        registry = CapabilityRegistry()
        def call(self, _context, request):
            return ToolResult(call_id=request.call_id, name=request.name, status="succeeded", data={"items": [], "total": 0})

    result = AgentEngine(store, Gateway()).run("run", "project", lambda _: AgentDecision(
        action="tool", tool=ToolCall(name="find_zone_candidates")))
    assert result.state.status == "waiting_question"
    assert result.state.pending_question["slot"] == "scope"
    assert "Настройка" in result.state.pending_question["question"]
    assert result.state.resolved_scope is None
    assert result.state.step == 1
    store.close()


def test_unranked_delegated_scope_cannot_gain_ranked_evidence(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent(), run_id="run")

    class Gateway:
        registry = CapabilityRegistry()
        def call(self, *_args):
            raise AssertionError("Inspection must first be supported by candidate evidence")

    decisions = iter([AgentDecision(action="tool", tool=ToolCall(name="inspect_zones", arguments={"zone_ids": ["invented"]})),
                      AgentDecision(action="finish", outcome_ref="blocked")])
    result = AgentEngine(store, Gateway()).run("run", "project", lambda _: next(decisions))
    assert result.state.last_result.error.code == "SCOPE_CANDIDATES_REQUIRED"
    assert result.state.resolved_scope is None
    store.close()
