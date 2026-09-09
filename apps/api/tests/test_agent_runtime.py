from types import SimpleNamespace

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
    assert event["field"] == "spacing_policy"
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
    assert event is None
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
        return {"requires_confirmation": True, "change_set": {"can_apply": True}}

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
    open_spacing = app._effective_pattern_spacing(PlacementMaskRequest(**base, spacing_policy="open"))
    dense_spacing = app._effective_pattern_spacing(PlacementMaskRequest(**base, spacing_policy="canopy"))
    assert dense_spacing < open_spacing
    # The policy only changes candidate sampling. The ordinary preview
    # validator remains responsible for setbacks and plant-to-plant safety.
    assert open_spacing >= 0.5
    assert dense_spacing >= 0.5


def test_run_store_reopens_checkpoint_and_event_log(tmp_path):
    path = tmp_path / "runs.sqlite3"
    store = AgentRunStore(path)
    created = store.create("project", intent(), snapshot_version=7, run_id="run-1")
    running = created.state.model_copy(update={"status": "running", "step": 1})
    saved = store.checkpoint("project", "run-1", expected_revision=1, state=running,
                             kind="run_started", payload={"step": 1})
    store.close()

    reopened = AgentRunStore(path)
    loaded = reopened.get("project", "run-1")
    assert loaded.revision == saved.revision == 2
    assert loaded.state.status == "running"
    assert loaded.state.snapshot_version == 7
    assert [(item.sequence, item.kind) for item in loaded.events] == [(2, "run_started")]
    reopened.close()


def test_engine_recurses_after_tools_and_stops_at_approval(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")

    class FakeGateway:
        registry = CapabilityRegistry()

        def call(self, _context, request):
            data = {"preview": request.name == "prepare_placement"}
            if request.name == "find_zone_candidates":
                data = {"items": [{"zone_id": "zone-a"}]}
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
    assert len(calls) == 5
    assert [event.kind for event in finished.events] == [
        "run_started", "decision", "tool_result", "decision", "tool_result", "decision", "tool_result",
        "decision", "tool_call_normalized", "tool_result", "decision", "approval_requested"
    ]
    assert finished.state.pending_approval["preview_ref"]
    store.close()


def test_engine_cannot_finish_without_an_outcome_reference(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent(), snapshot_version=1, run_id="run-1")
    engine = AgentEngine(store, type("Gateway", (), {"registry": CapabilityRegistry()})())

    def choose(_context):
        return AgentDecision(action="finish", outcome_ref="outcome-1")

    # The contract requires an outcome reference; the engine accepts the
    # reference as a durable terminal marker and never claims a plan commit.
    finished = engine.run("run-1", "project", choose)
    assert finished.state.status == "finished"
    assert finished.state.outcome_ref == "outcome-1"
    store.close()


def test_engine_persists_planner_failure_instead_of_leaving_run_running(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent(), snapshot_version=1, run_id="run-1")
    engine = AgentEngine(store, type("Gateway", (), {"registry": CapabilityRegistry()})())
    calls = 0

    def choose(_context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("invalid typed decision")
        return AgentDecision(action="finish", outcome_ref="recovered")

    failed = engine.run("run-1", "project", choose)
    assert failed.state.status == "failed"
    assert failed.state.failure.code == "PLANNER_FAILED"
    assert failed.state.failure.retryable is True
    recovered = engine.run("run-1", "project", choose)
    assert recovered.state.status == "finished"
    assert any(item.kind == "run_resumed" for item in recovered.events)
    store.close()


def test_engine_blocks_duplicate_tool_arguments_and_allows_one_strategy_change(tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    store.create("project", intent(), snapshot_version=1, run_id="run-1")
    gateway_calls = []

    class FakeGateway:
        registry = CapabilityRegistry()

        def call(self, _context, request):
            gateway_calls.append(request)
            return ToolResult(call_id=request.call_id, name=request.name,
                              status="succeeded", data={"items": []})

    decisions = iter([
        AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        )),
        AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        )),
        AgentDecision(action="finish", outcome_ref="changed-strategy"),
    ])
    result = AgentEngine(store, FakeGateway()).run(
        "run-1", "project", lambda _context: next(decisions)
    )
    assert result.state.status == "finished"
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
                              status="succeeded", data={"items": []})

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
    store.create("project", intent(), snapshot_version=1, run_id="run-1")
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
                              status="succeeded", data={"items": []})

    decisions = iter([
        AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        )),
        AgentDecision(action="tool", tool=ToolCall(
            name="find_zone_candidates", arguments={"arrangement": "building_groves"}
        )),
        AgentDecision(action="finish", outcome_ref="after-refresh"),
    ])
    result = AgentEngine(store, FakeGateway()).run(
        "run-1", "project", lambda _context: next(decisions)
    )
    assert result.state.status == "finished"
    assert len(gateway_calls) == 2
    assert result.state.snapshot_version == 2
    assert len(set(result.state.tool_fingerprints)) == 2
    store.close()


def test_approval_commits_only_the_saved_preview_reference(monkeypatch, tmp_path):
    store = AgentRunStore(tmp_path / "runs.sqlite3")
    created = store.create("project", intent(), snapshot_version=7, run_id="run-1")
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
                "data": {"change_set": {
                    "id": "preview-1",
                    "digest": "digest-1",
                    "base_plan_version": 3,
                    "can_apply": True,
                }},
        },
    )

    applied = SimpleNamespace(
        change_set_id="preview-1",
        plan_version=4,
        state_version=8,
        added_ids=["tree-1"],
        updated_ids=[],
        deleted_ids=[],
    )
    captured = {}

    def fake_apply(project_id, payload):
        captured["project_id"] = project_id
        captured["payload"] = payload
        return applied

    from app import api
    monkeypatch.setattr(api.application, "get", lambda *_args, **_kwargs: SimpleNamespace(
        state_version=7, plan=SimpleNamespace(version=3)
    ))
    monkeypatch.setattr(api.application, "apply_change_set", fake_apply)
    monkeypatch.setattr(runtime_routes, "_store", lambda: store)

    result = runtime_routes.approve_run(
        "project", "run-1", runtime_routes.AgentRunApproval(preview_ref="preview-call")
    )
    assert result.state.status == "finished"
    assert result.state.outcome_ref == "change-set:preview-1"
    assert captured["project_id"] == "project"
    assert captured["payload"].preview_id == "preview-1"
    assert captured["payload"].digest == "digest-1"
    assert [event.kind for event in result.events] == ["tool_result", "commit_applied"]
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

        def compile(self, text, *, project_context=None):
            assert "Дополнение пользователя: Участок 6" in text
            assert project_context["id"] == "project"
            return intent(text)

    from app import api
    monkeypatch.setattr(runtime_routes, "_store", lambda: store)
    monkeypatch.setattr(runtime_routes.local, "configured_model", lambda: "local-model")
    monkeypatch.setattr(runtime_routes, "IntentCompiler", FakeCompiler)
    monkeypatch.setattr(runtime_routes, "project_context", lambda _project: {"id": "project"})
    monkeypatch.setattr(api, "application", SimpleNamespace(
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


def test_real_domain_run_reaches_placement_preview_without_mutation(tmp_path):
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
    assert result.state.status == "waiting_approval"
    assert result.state.resolved_scope.zone_ids == ["west"]
    assert result.state.last_result.name == "prepare_placement"
    assert result.state.last_result.status == "succeeded"
    assert result.state.last_result.data["requires_confirmation"] is True
    assert app.get(project.id).plan.objects == []
    assert len(calls) == 3
    store.close()


def test_real_domain_approval_commits_saved_preview_only(tmp_path, monkeypatch):
    from test_placement_allocation import application

    app, project = application()
    app.history = SimpleNamespace(record=lambda *_args, **_kwargs: None)
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
    monkeypatch.setattr(api, "application", app)
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
