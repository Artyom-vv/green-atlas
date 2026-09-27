"""Direct calls cannot replace source-bound zone effects or approved receipts."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.agent_runtime.contracts import AgentIntent, Goal, ToolCall
from app.agent_runtime.gateway import GatewayContext, GatewayPolicy, GatewayRejected, ToolGateway
from app.agent_runtime.policy import capability_allowed
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.zone_workflow import ZoneIntent, bind_zone_intent, zone_prepare_arguments
from app.planting_zone_changes import ZoneChangePreview, _preview_digest, get_zone_change_service
from test_planting_zone_changes import seeded
from test_zone_workflow import project_with_contour


def scenario(operation="update", target="east"):
    if operation == "create":
        app, project, reference = project_with_contour()
        source = "Создай участок «Сад» из контура source-area."
        proposed = ZoneIntent(operation="create", label="Сад", geometry_reference=reference)
    else:
        app, project = seeded()
        source = f"Переименуй участок {target} в «Сад»." if operation == "update" else f"Удали участок {target}."
        proposed = ZoneIntent(operation=operation, target_zone_id=target, label="Сад" if operation == "update" else None)
    bound = bind_zone_intent(source, project, proposed)
    ids = [bound.draft.zone_id] if bound.draft.zone_id else []
    intent = AgentIntent(raw_text=source, goal=Goal(operation="zones"),
        scope_mode="explicit" if ids else "project", explicit_zone_ids=ids, zone=bound)
    context = GatewayContext(project_id=project.id, expected_state_version=project.state_version,
        run_id="zone-run", allowed_zone_ids=frozenset(ids), intent=intent)
    request = ToolCall(name="prepare_zone_change", arguments=zone_prepare_arguments(bound))
    return app, project, context, request


def approved(context, result):
    data = result.data
    return replace(context, approved_preview_kind="planting_zones", approved_change_set_id=data["id"],
        approved_digest=data["digest"], approved_preview_result=result,
        policy=GatewayPolicy(allowed_effects=frozenset({"write"}))), ToolCall(name="commit_zone_change",
            arguments={"preview_id": data["id"], "digest": data["digest"], "base_state_version": data["base_state_version"]})


def compact(result):
    keys = {"id", "digest", "project_id", "operation", "target_zone_id", "base_state_version",
            "base_geometry_version", "base_plan_version", "can_apply", "before_area_m2", "after_area_m2",
            "blockers", "expires_at", "affected_planting_ids"}
    return result.model_copy(update={"data": {**{key: value for key, value in result.data.items() if key in keys},
                                             "affected_planting_count": len(result.data["affected_planting_ids"])}})


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
@pytest.mark.parametrize("compact_event", [False, True])
def test_real_gateway_exact_zone_population_and_commit(operation, compact_event):
    app, project, context, request = scenario(operation)
    gateway = ToolGateway(app)
    result = gateway.call(context, request)
    assert result.status == "succeeded" and result.verification.status == "verified"
    assert app.get(project.id).model_dump(mode="json") == project.model_dump(mode="json")
    full = ZoneChangePreview.model_validate(result.data)
    commit_context, commit_request = approved(context, compact(result) if compact_event else result)
    receipt = gateway.call(commit_context, commit_request)
    assert receipt.status == "succeeded", receipt.error
    saved = app.get(project.id)
    assert saved.state_version == project.state_version + 1
    assert saved.plan.objects == project.plan.objects
    assert [zone.model_dump(mode="json") for zone in saved.planting_zones] == result.data["after_zones"]
    assert receipt.data["operation"] == operation and receipt.data["preview_id"] == full.id


@pytest.mark.parametrize("changes", [
    {"source_text": "Переименуй участок west в «Сад»."},
    {"base_state_version": 999},
    {"intent": {"operation": "delete", "target_zone_id": "east"}},
    {"intent": {"operation": "update", "target_zone_id": "west", "label": "Сад"}},
    {"intent": {"operation": "update", "target_zone_id": "east", "label": "Лес"}},
])
def test_direct_prepare_cannot_replace_typed_source_slots(changes):
    app, project, context, request = scenario()
    with pytest.raises(GatewayRejected):
        ToolGateway(app).call(context, request.model_copy(update={"arguments": {**request.arguments, **changes}}))
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("changes", [
    {"geometry": {"type": "Polygon", "coordinates": []}},
    {"after_zones": []}, {"base_state_version": True},
])
def test_tool_contract_has_no_arbitrary_polygon_population_or_boolean_version(changes):
    app, project, context, request = scenario()
    result = ToolGateway(app).call(context, request.model_copy(update={"arguments": {**request.arguments, **changes}}))
    assert result.status == "failed" and result.error.code == "INVALID_TOOL_ARGUMENTS"
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("kind", ["missing_intent", "other_operation", "wrong_scope", "wrong_source", "mutated_bound"])
def test_zone_gateway_requires_independent_source_and_scope_evidence(kind):
    app, project, context, request = scenario()
    if kind == "missing_intent":
        context = replace(context, intent=None)
    elif kind == "other_operation":
        context = replace(context, intent=context.intent.model_copy(update={"goal": Goal(operation="delete")}))
    elif kind == "wrong_scope":
        context = replace(context, allowed_zone_ids=frozenset({"west"}))
    elif kind == "wrong_source":
        context = replace(context, intent=context.intent.model_copy(update={"raw_text": "Удали участок west."}))
    else:
        forged = context.intent.zone.model_copy(update={"before_zones_digest": "a" * 64})
        context = replace(context, intent=context.intent.model_copy(update={"zone": forged}))
    with pytest.raises(GatewayRejected):
        ToolGateway(app).call(context, request)
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("changes", [
    {"unresolved_requirements": ["кроме восточной половины"]},
    {"species_ids": ["tilia-cordata@2026-08-28.1"]},
    {"goal": Goal(operation="zones", target_count=2)},
    {"scope_mode": "delegated"},
])
def test_zone_requirements_cannot_be_ignored_to_make_a_preview(changes):
    app, project, context, request = scenario()
    context = replace(context, intent=context.intent.model_copy(update=changes))
    result = ToolGateway(app).call(context, request)
    assert result.status == "blocked" and result.error.code == "REQUIREMENTS_UNRESOLVED"
    assert app.get(project.id).state_version == project.state_version


def test_zone_policy_only_exposes_the_saved_zone_preview_and_commit_pair():
    assert capability_allowed("zones", "prepare_zone_change", "preview")
    assert capability_allowed("zones", "commit_zone_change", "write")
    for operation in ("place", "edit", "delete", "inspect"):
        assert not capability_allowed(operation, "prepare_zone_change", "preview")
        assert not capability_allowed(operation, "commit_zone_change", "write")
    assert not capability_allowed("zones", "preview_zone", "preview")
    assert not capability_allowed("zones", "commit_change_set", "write")


@pytest.mark.parametrize("kind", ["preview_kind", "evidence_name", "missing_evidence", "effect", "area", "extra", "count", "missing_project", "wrong_reference", "base_version", "boolean_count"])
def test_approval_rejects_changed_kind_reference_compact_effects_and_versions(kind):
    app, project, context, request = scenario()
    gateway = ToolGateway(app)
    result = compact(gateway.call(context, request))
    commit_context, commit_request = approved(context, result)
    if kind == "preview_kind":
        commit_context = replace(commit_context, approved_preview_kind="plantings")
    elif kind == "missing_evidence":
        commit_context = replace(commit_context, approved_preview_result=None)
    elif kind == "evidence_name":
        commit_context = replace(commit_context, approved_preview_result=result.model_copy(update={"name": "prepare_existing_change"}))
    elif kind == "base_version":
        commit_request = commit_request.model_copy(update={"arguments": {**commit_request.arguments, "base_state_version": 999}})
    else:
        data = dict(result.data)
        if kind == "effect":
            data["operation"] = "delete"
        elif kind == "area":
            data["after_area_m2"] += 1
        elif kind == "extra":
            data["unrequested_species"] = "another-species"
        elif kind == "count":
            data["affected_planting_count"] = 1
        elif kind == "boolean_count":
            data["affected_planting_count"] = False
        elif kind == "missing_project":
            del data["project_id"]
        else:
            data["id"] = "other-preview"
        commit_context = replace(commit_context, approved_preview_result=result.model_copy(update={"data": data}))
    with pytest.raises(GatewayRejected):
        gateway.call(commit_context, commit_request)
    assert app.get(project.id).state_version == project.state_version


def test_another_full_preview_cannot_replace_the_approved_reference():
    app, project, context, request = scenario()
    gateway = ToolGateway(app)
    first = gateway.call(context, request)
    second = gateway.call(context, request)
    commit_context, commit_request = approved(context, first)
    with pytest.raises(GatewayRejected):
        gateway.call(replace(commit_context, approved_preview_result=second), commit_request)
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("change", ["another_run", "another_call", "missing_token"])
def test_approval_evidence_is_bound_to_project_run_and_call(change):
    app, project, context, request = scenario()
    gateway = ToolGateway(app)
    result = gateway.call(context, request)
    commit_context, commit_request = approved(context, compact(result))
    if change == "another_run":
        commit_context = replace(commit_context, run_id="other-run")
    else:
        updates = {"call_id": "another-call"} if change == "another_call" else {"evidence_refs": []}
        commit_context = replace(commit_context, approved_preview_result=compact(result).model_copy(update=updates))
    with pytest.raises(GatewayRejected):
        gateway.call(commit_context, commit_request)
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("mode", ["expired", "missing", "changed_project"])
def test_approval_requires_current_authoritative_saved_preview(mode):
    app, project, context, request = scenario()
    gateway = ToolGateway(app)
    result = gateway.call(context, request)
    commit_context, commit_request = approved(context, compact(result))
    service = get_zone_change_service(app)
    if mode == "expired":
        service._clock = lambda: datetime.now(UTC) + timedelta(hours=1)
    elif mode == "missing":
        service._previews.clear()
    else:
        project.name = "Changed project"
        app.repository.save(project)
    if mode == "changed_project":
        denied = gateway.call(commit_context, commit_request)
        assert denied.status == "stale"
    else:
        with pytest.raises(GatewayRejected):
            gateway.call(commit_context, commit_request)
    assert app.get(project.id).planting_zones == project.planting_zones


def test_occupied_deletion_reports_a_domain_refusal_and_cannot_be_approved():
    app, project, context, request = scenario("delete", "west")
    gateway = ToolGateway(app)
    result = gateway.call(context, request)
    assert result.status == "blocked" and result.error.code == "ZONE_CHANGE_BLOCKED"
    assert result.data["affected_planting_ids"] == ["tree-west"]
    commit_context, commit_request = approved(context, result.model_copy(update={"status": "succeeded"}))
    with pytest.raises(GatewayRejected):
        gateway.call(commit_context, commit_request)
    assert app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize("mode", ["changed_after", "compact_only"])
def test_untrusted_executor_result_cannot_invent_an_authoritative_proposal(mode):
    app, project, context, request = scenario()
    gateway = ToolGateway(app)
    result = gateway.call(context, request)
    forged = dict(result.data)
    if mode == "changed_after":
        forged["after_zones"][0]["label"] = "Unrequested change"
        forged["digest"] = _preview_digest(ZoneChangePreview.model_validate(forged))
    else:
        forged = compact(result).data
    capability = replace(gateway.registry.get("prepare_zone_change"), executor=lambda *_: forged)
    denied = ToolGateway(app, CapabilityRegistry((capability,))).call(context, request)
    assert denied.status == "failed" and denied.error.code == "PREVIEW_NOT_VERIFIED"
    assert app.get(project.id).state_version == project.state_version
