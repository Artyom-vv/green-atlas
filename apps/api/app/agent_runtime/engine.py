"""Recursive checkpointed execution for capability-driven agent runs."""

from collections.abc import Callable
from hashlib import sha256
import json
from typing import Any, Protocol

from app.agent_runtime.contracts import AgentDecision, AgentRunState, ResolvedScope, ToolError, ToolResult
from app.agent_runtime.gateway import GatewayContext, GatewayRejected, ToolGateway
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.store import AgentRunRecord, AgentRunStore, RunConflict
from app.agent_runtime.verifier import extract_change_set, verify_preview_data


class DecisionMaker(Protocol):
    def __call__(self, context: dict[str, Any]) -> AgentDecision: ...


def _compact(value: Any, *, depth: int = 0, max_depth: int = 3,
             max_items: int = 12, max_text: int = 900) -> Any:
    """Keep planner context factual without replaying DXF-sized payloads."""
    if isinstance(value, str):
        return value if len(value) <= max_text else value[:max_text] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= max_depth:
        if isinstance(value, dict):
            return {"keys": list(value)[:max_items], "truncated": True}
        if isinstance(value, list):
            return {"count": len(value), "truncated": True}
        return str(value)[:max_text]
    if isinstance(value, list):
        result = [_compact(item, depth=depth + 1, max_depth=max_depth,
                           max_items=max_items, max_text=max_text) for item in value[:max_items]]
        if len(value) > max_items:
            result.append({"truncated_items": len(value) - max_items})
        return result
    if isinstance(value, dict):
        result = {str(key): _compact(item, depth=depth + 1, max_depth=max_depth,
                                     max_items=max_items, max_text=max_text)
                  for key, item in list(value.items())[:max_items]}
        if len(value) > max_items:
            result["_truncated_keys"] = len(value) - max_items
        return result
    return str(value)[:max_text]


def _summarize_data(name: str | None, data: Any) -> Any:
    """Keep durable agent facts small while retaining actionable references."""
    if name == "inspect_zones" and isinstance(data, list):
        return {
            "zones": [
                {"id": item.get("id"), "label": item.get("label")}
                for item in data if isinstance(item, dict)
            ],
            "geometry_loaded": True,
        }
    if name == "project_context" and isinstance(data, dict):
        return {
            key: data.get(key)
            for key in ("id", "name", "state_version", "plan_version", "planting_count", "zones")
            if key in data
        }
    if name == "species_shortlist" and isinstance(data, list):
        return {
            "items": [
                {
                    "species": {
                        key: item.get("species", {}).get(key)
                        for key in ("id", "common_name", "scientific_name", "kind",
                                    "growth_rate", "root_architecture", "risk_flags")
                        if item.get("species", {}).get(key) is not None
                    },
                    "status": item.get("status"),
                    "estimated_capacity": item.get("estimated_capacity"),
                    "estimated_mature_diameter_m": item.get("estimated_mature_diameter_m"),
                    "reasons": item.get("reasons", [])[:4],
                }
                for item in data[:12] if isinstance(item, dict)
            ],
            "total": len(data),
        }
    if name == "prepare_placement" and isinstance(data, dict):
        proposal = data.get("proposal") if isinstance(data.get("proposal"), dict) else {}
        change_set = proposal.get("change_set") if isinstance(proposal.get("change_set"), dict) else {}
        additions = change_set.get("additions", []) or []
        kind_counts: dict[str, int] = {}
        species_ids: list[str] = []
        for item in additions:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if isinstance(kind, str):
                kind_counts[kind] = kind_counts.get(kind, 0) + 1
            species_id = item.get("species_revision_id")
            if isinstance(species_id, str) and species_id not in species_ids:
                species_ids.append(species_id)
        requested = data.get("requested", proposal.get("requested"))
        found = data.get("found", proposal.get("found"))
        shortfall = data.get("shortfall", proposal.get("shortfall"))
        return {
            "base_plan_version": data.get("base_plan_version"),
            "requires_confirmation": data.get("requires_confirmation"),
            "requested": requested,
            "found": found,
            "shortfall": shortfall,
            "quantity_mode": data.get("quantity_mode", proposal.get("quantity_mode")),
            "species_revision_ids": data.get("species_revision_ids") or proposal.get("species_revision_ids") or species_ids,
            "resolved_zone_ids": data.get("resolved_zone_ids") or proposal.get("resolved_zone_ids"),
            "condition_check": _compact(data.get("condition_check", proposal.get("condition_check")), max_depth=2, max_items=8, max_text=700),
            "composition_policy": _compact(data.get("composition_policy"), max_depth=2, max_items=8, max_text=700),
            "shortfall_explanation": data.get("shortfall_explanation") or proposal.get("shortfall_explanation"),
            "shortfall_evidence": _compact(data.get("shortfall_evidence") or proposal.get("shortfall_evidence"), max_depth=3, max_items=8, max_text=700),
            "kind_counts": kind_counts,
            "proposal": {
                key: proposal.get(key)
                for key in ("requested", "found", "shortfall", "quantity_mode",
                            "species_revision_id", "resolved_zone_ids",
                            "condition_check", "delegated_quantity_limit",
                            "shortfall_explanation", "shortfall_evidence", "search")
                if key in proposal
            },
            "reason_summary": _compact(
                data.get("reason_summary")
                or proposal.get("reason_summary")
                or proposal.get("shortfall_evidence", []),
                max_depth=3,
                max_items=12,
                max_text=700,
            ),
            "change_set": {
                key: change_set.get(key)
                for key in ("id", "digest", "base_plan_version", "source", "label", "can_apply")
                if key in change_set
            }
            | {
                "additions_count": len(change_set.get("additions", []) or []),
                "updates_count": len(change_set.get("updates", []) or []),
                "deletions_count": len(change_set.get("deletion_ids", []) or []),
                "candidate_results": _compact(change_set.get("candidate_results", []),
                                               max_depth=2, max_items=8, max_text=600),
            },
        }
    if name == "prepare_existing_change" and isinstance(data, dict):
        change_set = data.get("change_set") if isinstance(data.get("change_set"), dict) else {}
        return {
            "operation": data.get("operation"),
            "requested": data.get("requested"),
            "found": data.get("found"),
            "shortfall": data.get("shortfall"),
            "target_ids_count": len(data.get("target_ids", []) or []),
            "change_set": {
                key: change_set.get(key)
                for key in ("id", "digest", "base_plan_version", "source", "label", "can_apply")
                if key in change_set
            } | {
                "additions_count": len(change_set.get("additions", []) or []),
                "updates_count": len(change_set.get("updates", []) or []),
                "deletions_count": len(change_set.get("deletion_ids", []) or []),
                "candidate_results": _compact(change_set.get("candidate_results", []),
                                               max_depth=2, max_items=8, max_text=600),
            },
        }
    if name and name.startswith("preview_") and isinstance(data, dict):
        change_set = data.get("change_set") if isinstance(data.get("change_set"), dict) else data
        if isinstance(change_set, dict) and ("can_apply" in change_set or "digest" in change_set):
            return {
                key: change_set.get(key)
                for key in ("id", "digest", "base_plan_version", "source", "label", "can_apply")
                if key in change_set
            } | {
                "additions_count": len(change_set.get("additions", []) or []),
                "updates_count": len(change_set.get("updates", []) or []),
                "deletions_count": len(change_set.get("deletion_ids", []) or []),
                "candidate_results": _compact(change_set.get("candidate_results", []),
                                               max_depth=2, max_items=8, max_text=600),
            }
    return _compact(data, max_depth=3, max_items=12, max_text=1200)


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project tool output into the facts needed for the next decision."""
    name = result.get("name")
    data = _summarize_data(name, result.get("data"))
    return {
        key: _compact(value, max_depth=3, max_items=12, max_text=1200)
        for key, value in {
            "call_id": result.get("call_id"),
            "name": name,
            "status": result.get("status"),
            "data": data,
            "evidence_refs": result.get("evidence_refs", []),
            "resource_versions": result.get("resource_versions", {}),
            "verification": result.get("verification"),
            "error": result.get("error"),
        }.items()
        if value is not None
    }


def _tool_fingerprint(request, *, snapshot_version: int, plan_version: int | None) -> str:
    """Stable identity for a tool attempt, excluding the volatile call id.

    A changed project snapshot is a new evidence boundary: retrying the same
    read after a stale result is legitimate and must not trip the guard.
    """
    canonical = json.dumps(
        {
            "name": request.name,
            "arguments": request.arguments,
            "snapshot_version": snapshot_version,
            "plan_version": plan_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _compact_capability(capability: dict[str, Any]) -> dict[str, Any]:
    parameters = capability.get("parameters") or {}
    properties = parameters.get("properties") or {}
    argument_contract = {}
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        contract = {}
        for key in ("type", "enum", "default", "minimum", "maximum", "minItems", "maxItems"):
            if key in schema:
                contract[key] = schema[key]
        if isinstance(schema.get("items"), dict) and "enum" in schema["items"]:
            contract["items_enum"] = schema["items"]["enum"]
        if isinstance(schema.get("anyOf"), list):
            contract["types"] = [item.get("type") for item in schema["anyOf"]
                                  if isinstance(item, dict) and item.get("type")]
        argument_contract[name] = contract
    return {
        "name": capability.get("name"),
        "description": capability.get("description"),
        "effect": capability.get("effect"),
        "argument_keys": list(properties)[:24],
        "required": parameters.get("required", [])[:24],
        "argument_contract": argument_contract,
    }


def _safe_decision_event(decision: AgentDecision) -> dict[str, Any]:
    """Persist an audit breadcrumb, never the model's hidden reasoning."""
    payload: dict[str, Any] = {"action": decision.action}
    if decision.tool is not None:
        payload["tool"] = {
            "call_id": decision.tool.call_id,
            "name": decision.tool.name,
        }
    for field in ("preview_ref", "outcome_ref", "job_ref", "missing_slot"):
        value = getattr(decision, field)
        if value is not None:
            payload[field] = value
    return payload


def _density_spacing_policy(state: AgentRunState) -> str | None:
    """Translate a typed density preference into a domain layout policy.

    This is intentionally an enum mapping, not a text parser. The model may
    express a preference in several supported typed values, while the domain
    receives only its closed spacing-policy contract.
    """
    values = {
        "open": "open",
        "sparse": "open",
        "low": "open",
        "редко": "open",
        "разреженно": "open",
        "balanced": "balanced",
        "medium": "balanced",
        "normal": "balanced",
        "равномерно": "balanced",
        "умеренно": "balanced",
        "high": "canopy",
        "dense": "canopy",
        "canopy": "canopy",
        "плотно": "canopy",
        "погуще": "canopy",
        "густо": "canopy",
    }
    for preference in state.intent.preferences:
        if preference.key != "density":
            continue
        value = preference.value
        if isinstance(value, str):
            mapped = values.get(value.casefold().strip())
            if mapped:
                return mapped
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if value <= 0.33:
                return "open"
            if value <= 0.66:
                return "balanced"
            if value <= 1:
                return "canopy"
    return None


class AgentEngine:
    """Run until a tool result changes the next decision or human input is needed.

    The engine is deliberately model-agnostic.  A local Ollama adapter can
    implement ``DecisionMaker``; deterministic verification and permission
    checks remain in this process.
    """

    def __init__(self, store: AgentRunStore, gateway: ToolGateway,
                 registry: CapabilityRegistry | None = None):
        self.store = store
        self.gateway = gateway
        self.registry = registry or gateway.registry

    def _planner_capabilities(self, state: AgentRunState) -> list[dict[str, Any]]:
        capabilities = self.registry.discover(effects={"read", "preview"})
        if state.intent.goal.operation != "place":
            if state.intent.goal.operation in {"edit", "delete"}:
                return [item for item in capabilities
                        if item["effect"] == "read" or item["name"] == "prepare_existing_change"]
            return capabilities
        # Placement has one canonical preview boundary. The older raw preview
        # tools remain available to legacy UI, but exposing them here lets a
        # model bypass the typed placement adapter and invent stale arguments.
        return [item for item in capabilities
                if item["effect"] == "read" or item["name"] == "prepare_placement"]

    def _context(self, record: AgentRunRecord) -> dict[str, Any]:
        state = record.state
        capabilities = self._planner_capabilities(state)
        return {
            "run": state.model_dump(mode="json", exclude={"last_result"}),
            "last_result": _compact_result(state.last_result.model_dump(mode="json")) if state.last_result else None,
            "available_capabilities": [_compact_capability(item) for item in capabilities],
            "allowed_capability_names": [item["name"] for item in capabilities],
            "events": [
                {
                    "sequence": item.sequence,
                    "kind": item.kind,
                    "payload": (
                        _compact_result(item.payload)
                        if item.kind == "tool_result"
                        else _compact(item.payload, max_depth=3, max_items=12, max_text=900)
                    ),
                }
                for item in record.events[-24:]
            ],
            "policy": {"normative_rules": "server_owned", "writes": "approval_only"},
        }

    def _checkpoint(self, record: AgentRunRecord, state: AgentRunState,
                    kind: str, payload: dict) -> AgentRunRecord:
        return self.store.checkpoint(
            record.state.project_id,
            record.state.run_id,
            expected_revision=record.revision,
            state=state,
            kind=kind,
            payload=payload,
        )

    def _record_result(self, record: AgentRunRecord, result: ToolResult,
                       requested_zones: list[str] | None = None,
                       tool_fingerprint: str | None = None) -> AgentRunRecord:
        state = record.state.model_copy(deep=True)
        stored_result = result.model_copy(update={
            "data": _summarize_data(result.name, result.data),
        })
        state.tool_calls.append(result.call_id)
        if tool_fingerprint is not None:
            state.tool_fingerprints.append(tool_fingerprint)
        state.evidence_refs.extend(result.evidence_refs)
        state.last_result = stored_result
        if result.name == "find_zone_candidates" and result.status in {"succeeded", "partial"}:
            items = result.data.get("items", []) if isinstance(result.data, dict) else []
            state.candidate_zone_ids = [
                item["zone_id"] for item in items
                if isinstance(item, dict) and isinstance(item.get("zone_id"), str)
            ][:80]
        if (result.name == "inspect_zones" and result.status == "succeeded"
                and state.intent.scope_mode == "delegated" and state.resolved_scope is None):
            requested_zones = requested_zones or ((result.data or {}).get("zone_ids")
                                                  if isinstance(result.data, dict) else None)
            if isinstance(requested_zones, list) and len(requested_zones) == 1:
                state.resolved_scope = ResolvedScope(
                    project_id=state.project_id,
                    zone_ids=requested_zones,
                    basis="agent",
                    criteria=["candidate_ranked", "single_scope_resolved"],
                    source_revision=state.snapshot_version,
                    evidence_refs=[result.call_id],
                )
        if result.status == "stale":
            state.snapshot_version = int(result.resource_versions.get("project", state.snapshot_version))
            plan_version = result.resource_versions.get("plan")
            if isinstance(plan_version, int):
                state.plan_version = plan_version
        return self._checkpoint(record, state, "tool_result", stored_result.model_dump(mode="json"))

    @staticmethod
    def _preview_change_set(result: ToolResult) -> dict[str, Any] | None:
        if result.status not in {"succeeded", "partial"}:
            return None
        verification = result.verification or verify_preview_data(
            result.data, tool_name=result.name
        )
        if verification.status != "verified":
            return None
        change_set = extract_change_set(result.data, tool_name=result.name)
        if change_set is None:
            return None
        if not all(change_set.get(key) is not None
                   for key in ("id", "digest", "base_plan_version")):
            return None
        return change_set

    @staticmethod
    def _normalize_tool_call(state: AgentRunState, request):
        """Apply typed context defaults while preserving complete choices."""
        arguments = dict(request.arguments)
        changes: list[dict[str, Any]] = []
        if (request.name == "inspect_zones" and not arguments.get("zone_ids")
                and state.intent.scope_mode != "delegated"
                and state.resolved_scope is not None
                and state.resolved_scope.zone_ids):
            arguments["zone_ids"] = list(state.resolved_scope.zone_ids)
            changes.append({
                "field": "zone_ids",
                "from": "resolved_scope",
                "to": "typed_context_default",
            })
        if request.name not in {"prepare_placement", "species_shortlist"}:
            if not changes:
                return request, None
            return request.model_copy(update={"arguments": arguments}), {
                "call_id": request.call_id,
                "name": request.name,
                "changes": changes,
            }
        delegated_species = any(
            item.slot == "species" and item.strategy in {"agent", "best_evidence"}
            for item in state.intent.delegations
        )
        species_values = arguments.get("species_revision_ids")
        if (delegated_species and isinstance(species_values, list)
                and not all(isinstance(item, str) for item in species_values)):
            arguments["species_revision_ids"] = []
            changes.append({
                "field": "species_revision_ids",
                "from": "non_string_index",
                "to": "automatic",
            })
        if (state.intent.plant_kind == "mixed" and delegated_species
                and request.name == "prepare_placement"
                and isinstance(species_values, list)
                and species_values):
            arguments["species_revision_ids"] = []
            changes.append({
                "field": "species_revision_ids",
                "from": "model_selected_mixed_composition",
                "to": "automatic_tree_and_shrub",
            })
        if (state.intent.plant_kind == "mixed" and request.name == "species_shortlist"
                and arguments.get("kind") in {"tree", "shrub"}):
            arguments["kind"] = None
            changes.append({
                "field": "kind",
                "from": request.arguments.get("kind"),
                "to": "all_plant_kinds",
            })

        if request.name == "prepare_placement":
            density_policy = _density_spacing_policy(state)
            # An explicit policy selected by the model or user is
            # authoritative; the intent preference supplies a default only
            # when omitted. This field belongs only to placement preparation.
            if density_policy is not None and "spacing_policy" not in arguments:
                arguments["spacing_policy"] = density_policy
                changes.append({
                    "field": "spacing_policy",
                    "from": "density_preference",
                    "to": density_policy,
                })
        if not changes:
            return request, None
        event = {"call_id": request.call_id, "name": request.name, "changes": changes}
        # Keep the compact legacy shape for the single species repair while
        # retaining a structured list when several defaults are applied.
        species_change = next((item for item in changes if item["field"] == "species_revision_ids"), None)
        if species_change is not None:
            event.update(species_change)
        elif len(changes) == 1:
            event.update(changes[0])
        return request.model_copy(update={"arguments": arguments}), event

    def run(self, run_id: str, project_id: str, choose: DecisionMaker, *,
            max_steps: int | None = None,
            stopped: Callable[[], bool] | None = None) -> AgentRunRecord:
        """Resume a run and persist every decision/result before continuing."""
        stopped = stopped or (lambda: False)
        record = self.store.get(project_id, run_id)
        state = record.state
        if state.status in {"finished", "cancelled"}:
            return record
        if state.status in {"waiting_question", "waiting_approval", "waiting_job"}:
            return record
        if state.status == "failed":
            if not state.failure or not state.failure.retryable:
                return record
            state = state.model_copy(update={"status": "running", "failure": None})
            record = self._checkpoint(record, state, "run_resumed", {
                "reason": "retryable_failure",
                "previous_failure": record.state.failure.code,
            })
        if state.status == "queued":
            state = state.model_copy(update={"status": "running"})
            record = self._checkpoint(record, state, "run_started", {"run_id": run_id})
        limit = min(max_steps or state.max_steps, state.max_steps)

        while record.state.step < limit:
            if stopped():
                state = record.state.model_copy(update={"status": "cancelled"})
                return self._checkpoint(record, state, "run_cancelled", {"reason": "cancelled"})
            record = self.store.get(project_id, run_id)
            state = record.state
            if state.status != "running":
                return record
            try:
                decision = choose(self._context(record))
            except Exception as error:
                failure = ToolError(
                    code="PLANNER_FAILED",
                    message=f"Следующее решение агента не прошло проверку: {str(error)[:600]}",
                    retryable=True,
                    remedy="Повторить шаг после обновления снимка проекта.",
                )
                state = record.state.model_copy(update={
                    "status": "failed",
                    "failure": failure,
                })
                return self._checkpoint(record, state, "run_failed", failure.model_dump(mode="json"))
            record = self._checkpoint(
                record,
                state.model_copy(update={"step": state.step + 1}),
                "decision",
                _safe_decision_event(decision),
            )

            if decision.action == "tool":
                assert decision.tool is not None
                normalized_call, normalization = self._normalize_tool_call(record.state, decision.tool)
                if normalization is not None:
                    record = self._checkpoint(record, record.state, "tool_call_normalized", normalization)
                decision_tool = normalized_call
                allowed_names = {item["name"] for item in self._planner_capabilities(record.state)}
                if decision_tool.name not in allowed_names:
                    result = ToolResult(
                        call_id=decision_tool.call_id,
                        name=decision_tool.name,
                        status="blocked",
                        error=ToolError(
                            code="CAPABILITY_NOT_IN_WORKFLOW",
                            message="Для этой операции доступен другой проверяемый инструмент.",
                            retryable=False,
                            remedy="Выберите capability из available_capabilities.",
                        ),
                    )
                    record = self._record_result(record, result)
                    continue
                fingerprint = _tool_fingerprint(
                    decision_tool,
                    snapshot_version=record.state.snapshot_version,
                    plan_version=record.state.plan_version,
                )
                previous_attempts = record.state.tool_fingerprints.count(fingerprint)
                if previous_attempts:
                    # A weak local model must get one correction turn, but it
                    # must not consume the whole recursive budget on an
                    # unchanged call with a new model-generated call_id.
                    repeat_number = previous_attempts + 1
                    result = ToolResult(
                        call_id=decision_tool.call_id,
                        name=decision_tool.name,
                        status="blocked",
                        data={
                            "fingerprint": fingerprint,
                            "repeat_number": repeat_number,
                            "next_action": "choose_different_arguments_or_capability",
                        },
                        error=ToolError(
                            code="REPEATED_TOOL_CALL",
                            message="Этот инструмент уже вызывался с теми же параметрами и не изменил результат.",
                            retryable=False,
                            remedy="Выберите другой участок, породу, способ размещения или прочитайте новые факты.",
                        ),
                    )
                    record = self._record_result(
                        record, result, tool_fingerprint=fingerprint,
                    )
                    if previous_attempts >= 2:
                        failure = ToolError(
                            code="AGENT_NO_PROGRESS",
                            message="Агент дважды повторил один и тот же шаг без изменения входных данных.",
                            retryable=False,
                            remedy="Измените постановку задачи или выберите другую стратегию размещения.",
                        )
                        state = record.state.model_copy(update={
                            "status": "failed",
                            "failure": failure,
                        })
                        return self._checkpoint(
                            record, state, "run_failed", failure.model_dump(mode="json")
                        )
                    continue
                if (record.state.intent.scope_mode == "delegated"
                        and record.state.resolved_scope is None
                        and decision.tool.name.startswith("preview")):
                    result = ToolResult(
                        call_id=decision_tool.call_id,
                        name=decision_tool.name,
                        status="blocked",
                        error={"code": "SCOPE_UNRESOLVED", "message": "Сначала нужно разрешить делегированный участок.", "retryable": False},
                    )
                else:
                    try:
                        verified_scope = (
                            frozenset(record.state.resolved_scope.zone_ids)
                            if record.state.resolved_scope is not None
                            else frozenset(record.state.candidate_zone_ids)
                        )
                        result = self.gateway.call(
                            GatewayContext(
                                project_id=project_id,
                                expected_state_version=record.state.snapshot_version,
                                run_id=run_id,
                                allowed_zone_ids=verified_scope,
                            ),
                            decision_tool,
                        )
                    except GatewayRejected as error:
                        result = ToolResult(
                            call_id=decision_tool.call_id,
                            name=decision_tool.name,
                            status="blocked",
                            error={"code": "GATEWAY_REJECTED", "message": str(error), "retryable": False},
                        )
                record = self._record_result(
                    record,
                    result,
                    requested_zones=(decision_tool.arguments.get("zone_ids")
                                     if decision_tool.name == "inspect_zones" else None),
                    tool_fingerprint=fingerprint,
                )
                if self._preview_change_set(result) is not None:
                    state = record.state.model_copy(update={
                        "status": "waiting_approval",
                        "pending_approval": {
                            "preview_ref": result.call_id,
                            "reason": "Предложение рассчитано и проверено. Применить его к плану?",
                        },
                    })
                    return self._checkpoint(record, state, "approval_requested", state.pending_approval or {})
                continue

            if decision.action == "ask":
                state = record.state.model_copy(update={
                    "status": "waiting_question",
                    "pending_question": {
                        "slot": decision.missing_slot or "unknown",
                        "question": decision.question or "Уточните задачу.",
                    },
                })
                return self._checkpoint(record, state, "question", state.pending_question or {})

            if decision.action == "approval":
                preview_ref = decision.preview_ref
                has_preview = any(
                    item.kind == "tool_result"
                    and item.payload.get("status") in {"succeeded", "partial"}
                    and item.payload.get("call_id") == preview_ref
                    for item in record.events
                )
                if not has_preview:
                    failure = ToolError(
                        code="APPROVAL_WITHOUT_PREVIEW",
                        message="Нельзя запросить подтверждение без проверенного preview.",
                        retryable=False,
                    )
                    state = record.state.model_copy(update={
                        "status": "failed",
                        "failure": failure,
                    })
                    return self._checkpoint(record, state, "run_failed", failure.model_dump(mode="json"))
                state = record.state.model_copy(update={
                    "status": "waiting_approval",
                    "pending_approval": {"preview_ref": preview_ref, "reason": decision.reason or ""},
                })
                return self._checkpoint(record, state, "approval_requested", state.pending_approval or {})

            if decision.action == "wait":
                state = record.state.model_copy(update={"status": "waiting_job"})
                return self._checkpoint(record, state, "job_waiting", {"job_ref": decision.job_ref})

            if decision.action == "finish":
                state = record.state.model_copy(update={"status": "finished", "outcome_ref": decision.outcome_ref})
                return self._checkpoint(record, state, "run_finished", {"outcome_ref": decision.outcome_ref})

        failure = ToolError(
            code="STEP_BUDGET_EXCEEDED",
            message="Агент достиг лимита шагов без проверяемого результата.",
            retryable=False,
        )
        state = record.state.model_copy(update={"status": "failed", "failure": failure})
        return self._checkpoint(record, state, "run_failed", failure.model_dump(mode="json"))
