"""Recursive checkpointed execution for capability-driven agent runs."""

from collections.abc import Callable
from hashlib import sha256
import json
from typing import Any, Protocol

from app.agent_runtime.contracts import AgentDecision, AgentRunState, ObjectSelectionRequirement, ResolvedScope, ToolCall, ToolError, ToolResult
from app.agent_runtime.gateway import GatewayContext, GatewayRejected, ToolGateway
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.policy import PLAN_OPERATIONS, assess_requirements, capability_allowed
from app.agent_runtime.store import AgentRunRecord, AgentRunStore, RunConflict
from app.agent_runtime.verifier import extract_change_set, verify_preview_data
from app.agent_runtime.read_workflow import read_arguments, read_progress
from app.agent_runtime.selection import bound_scope, selection_problem
from app.agent_runtime.existing_workflow import existing_arguments
from app.agent_runtime.zone_workflow import zone_prepare_arguments, verify_zone_preview, zone_effect_summary


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
    if name == "prepare_zone_change" and isinstance(data, dict) and data.get("id"):
        # Full contours stay in the authoritative cache, never in model context.
        full = isinstance(data.get("before_zones"), list) and isinstance(data.get("after_zones"), list)
        # Retain only the validated target names for the historical receipt.
        # Re-projecting an already compact result must preserve these facts.
        effects = zone_effect_summary(data) if full else {key: data.get(key) for key in ("target_before", "target_after", "geometry_changed")}
        return {key: data.get(key) for key in ("id", "digest", "project_id", "operation", "target_zone_id",
            "base_state_version", "base_geometry_version", "base_plan_version", "can_apply",
            "before_area_m2", "after_area_m2", "blockers", "expires_at")} | {
            "affected_planting_count": data.get("affected_planting_count", len(data.get("affected_planting_ids", []))),
            "affected_planting_ids": data.get("affected_planting_ids", [])[:12],
        } | effects
    if name in {"prepare_placement", "prepare_existing_change"} and isinstance(data, dict):
        saved_change = data.get("change_set")
        if isinstance(saved_change, dict) and "additions_count" in saved_change:
            return data
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
            "placement_outcome": data.get("placement_outcome"),
            "scope_search": data.get("scope_search"),
            "arrangement": data.get("arrangement"),
            "plant_kind": data.get("plant_kind"),
            "condition_rule_ids": data.get("condition_rule_ids", []),
            "requested": requested,
            "found": found,
            "shortfall": shortfall,
            "quantity_mode": data.get("quantity_mode", proposal.get("quantity_mode")),
            "species_revision_ids": (data.get("species_revision_ids") or proposal.get("species_revision_ids")
                                     or ([proposal["species_revision_id"]] if proposal.get("species_revision_id") else species_ids)),
            "resolved_zone_ids": data.get("resolved_zone_ids") or proposal.get("resolved_zone_ids"),
            "condition_check": data.get("condition_check", proposal.get("condition_check")),
            "composition_policy": _compact(data.get("composition_policy"), max_depth=2, max_items=8, max_text=700),
            "shortfall_explanation": data.get("shortfall_explanation") or proposal.get("shortfall_explanation"),
            "shortfall_evidence": _compact(data.get("shortfall_evidence") or proposal.get("shortfall_evidence"), max_depth=3, max_items=8, max_text=700),
            "kind_counts": kind_counts,
            "actual_species_revision_ids": species_ids,
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
                "candidate_summary": {"total": len(change_set.get("candidate_results", [])),
                    "allowed": sum(item.get("status") in {"allowed", "accepted"} for item in change_set.get("candidate_results", []))},
            },
        }
    if name == "prepare_existing_change" and isinstance(data, dict):
        change_set = data.get("change_set") if isinstance(data.get("change_set"), dict) else {}
        return {
            "operation": data.get("operation"),
            "plant_kind": data.get("plant_kind"),
            "species_revision_ids": data.get("species_revision_ids", []),
            "target_before": data.get("target_before", []),
            "verified_updates": change_set.get("updates", []),
            "edit_action": data.get("edit_action"),
            "edit_parameters": data.get("edit_parameters"),
            "resolved_zone_ids": data.get("resolved_zone_ids", []),
            "resolved_object_ids": data.get("resolved_object_ids", []),
            "quantity_mode": data.get("quantity_mode"),
            "condition_rule_ids": data.get("condition_rule_ids", []),
            "condition_check": data.get("condition_check"),
            "requested": data.get("requested"),
            "found": data.get("found"),
            "shortfall": data.get("shortfall"),
            "target_ids_count": len(data.get("target_ids", []) or []),
            "target_ids": data.get("target_ids", []),
            "change_set": {
                key: change_set.get(key)
                for key in ("id", "digest", "base_plan_version", "source", "label", "can_apply")
                if key in change_set
            } | {
                "additions_count": len(change_set.get("additions", []) or []),
                "updates_count": len(change_set.get("updates", []) or []),
                "deletions_count": len(change_set.get("deletion_ids", []) or []),
                "update_ids": [item["id"] for item in change_set.get("updates", [])],
                "deletion_ids": change_set.get("deletion_ids", []),
                "candidate_results": _compact(change_set.get("candidate_results", []),
                                               max_depth=2, max_items=8, max_text=600),
                "candidate_summary": {"total": len(change_set.get("candidate_results", [])),
                    "allowed": sum(item.get("status") in {"allowed", "accepted"} for item in change_set.get("candidate_results", []))},
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


def _result_data(name: str | None, status: str, data: Any) -> Any:
    """Errors retain their corrective facts instead of an empty success preview."""
    capacity_outcome = name == "prepare_placement" and isinstance(data, dict) and data.get("placement_outcome")
    if status in {"succeeded", "partial"} or capacity_outcome or name == "prepare_zone_change":
        return _summarize_data(name, data)
    return _compact(data, max_depth=5, max_items=24, max_text=1200)


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project tool output into the facts needed for the next decision."""
    name = result.get("name")
    data = _result_data(name, result.get("status", "failed"), result.get("data"))
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
            "read_page": result.get("read_page"),
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


def _bound_placement_arguments(state: AgentRunState) -> dict | None:
    """A complete bound brief can go directly to its canonical domain preview.

    Choosing new scope would discard an explicit user decision. The domain
    already resolves delegated species and reads geometry/norms itself; its
    saved preview still determines exact, partial or impossible capacity.
    """
    intent = state.intent
    if (intent.goal.operation != "place" or intent.scope_mode not in {"explicit", "selection"}
            or state.plan_version is None or intent.goal.target_count is None
            or intent.plant_kind is None
            or intent.arrangement not in {"area", "building_contour", "building_groves", "road_edges"}):
        return None
    zones, objects = bound_scope(intent)
    if not zones or objects or selection_problem(intent):
        return None
    if not intent.species_ids and not any(
            item.slot == "species" and item.strategy in {"agent", "best_evidence"}
            for item in intent.delegations):
        return None
    return {"base_plan_version": state.plan_version, "zone_ids": zones,
            "target_count": intent.goal.target_count, "quantity_mode": "target",
            "plant_kind": intent.plant_kind, "arrangement": intent.arrangement,
            "species_revision_ids": intent.species_ids}


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
        return [item for item in capabilities if capability_allowed(state.intent.goal.operation, item["name"], item["effect"])]

    @staticmethod
    def _current_epoch_events(record: AgentRunRecord):
        boundary = next((index for index in range(len(record.events) - 1, -1, -1)
                         if record.events[index].kind in {"question_answered", "run_restarted"}), 0)
        return record.events[boundary:]

    def _context(self, record: AgentRunRecord) -> dict[str, Any]:
        state = record.state
        capabilities = self._planner_capabilities(state)
        return {
            "run": state.model_dump(mode="json", exclude={"last_result": True, "intent": {"zone": {"draft": True}}}),
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
                for item in self._current_epoch_events(record)[-24:]
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

    def _read_question(self, record: AgentRunRecord, message: str) -> AgentRunRecord:
        question = {"slot": "read", "question": message[:500]}
        state = record.state.model_copy(update={"status": "waiting_question", "pending_question": question,
                                               "pending_approval": None, "read_outcome": None})
        return self._checkpoint(record, state, "question", question)

    def _complete_read(self, record: AgentRunRecord, outcome) -> AgentRunRecord:
        state = record.state.model_copy(update={"status": "finished", "read_outcome": outcome,
            "outcome_ref": f"read:{outcome.capability}:{outcome.evidence_refs[-1]}", "pending_approval": None, "failure": None})
        return self._checkpoint(record, state, "read_completed", outcome.model_dump(mode="json"))

    def _record_result(self, record: AgentRunRecord, result: ToolResult,
                       requested_zones: list[str] | None = None,
                       tool_fingerprint: str | None = None,
                       resolving_fallback: bool = False) -> AgentRunRecord:
        state = record.state.model_copy(deep=True)
        if result.name == "prepare_placement" and isinstance(result.data, dict):
            attempts = self._placement_attempts(record)
            if attempts:
                result.data["scope_search"] = {
                    "attempts": attempts,
                    "selected_zone_ids": result.data.get("proposal", {}).get("resolved_zone_ids", []),
                    "limit": 3,
                    "exhaustive": False,
                    "reason": "Выбран следующий участок из ранжированного списка после проверки вместимости предыдущего.",
                }
        stored_result = result.model_copy(update={
            "data": _result_data(result.name, result.status, result.data),
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
                and state.intent.scope_mode == "delegated" and (state.resolved_scope is None or resolving_fallback)):
            requested_zones = requested_zones or ((result.data or {}).get("zone_ids")
                                                  if isinstance(result.data, dict) else None)
            if (isinstance(requested_zones, list) and len(requested_zones) == 1
                    and requested_zones[0] in state.candidate_zone_ids):
                state.resolved_scope = ResolvedScope(
                    project_id=state.project_id,
                    zone_ids=requested_zones,
                    basis="agent",
                    criteria=["candidate_ranked", "single_scope_resolved"] + (["capacity_fallback"] if resolving_fallback else []),
                    source_revision=state.snapshot_version,
                    evidence_refs=([record.state.last_result.call_id] if resolving_fallback and record.state.last_result else []) + [result.call_id],
                )
        if result.status == "stale":
            state.snapshot_version = int(result.resource_versions.get("project", state.snapshot_version))
            plan_version = result.resource_versions.get("plan")
            if isinstance(plan_version, int):
                state.plan_version = plan_version
            if state.intent.scope_mode == "delegated":
                state.candidate_zone_ids = []
                state.resolved_scope = None
        return self._checkpoint(record, state, "tool_result", stored_result.model_dump(mode="json"))

    @staticmethod
    def _placement_attempts(record: AgentRunRecord) -> list[dict]:
        attempts = []
        for event in record.events:
            if event.kind in {"question_answered", "run_restarted"}:
                attempts = []
                continue
            payload = event.payload
            if (event.kind != "tool_result" or payload.get("name") != "prepare_placement"
                    or payload.get("resource_versions", {}).get("project") != record.state.snapshot_version):
                continue
            data = payload.get("data") or {}
            outcome = data.get("placement_outcome")
            if isinstance(outcome, dict):
                attempts.append({"call_id": payload["call_id"], "zone_ids": data.get("resolved_zone_ids") or [],
                                 "status": outcome["status"], "found": outcome["found"],
                                 "requested": outcome["requested"]})
        return attempts

    def _handle_capacity(self, record: AgentRunRecord, request: ToolCall, *, limit: int) -> AgentRunRecord:
        """Try at most three ranked, inspected zones only when scope was delegated."""
        result = record.state.last_result
        assert result is not None
        outcome = result.data["placement_outcome"]
        attempts = self._placement_attempts(record)
        tried = {zone for attempt in attempts for zone in attempt["zone_ids"]}
        candidates = [zone for zone in record.state.candidate_zone_ids if zone not in tried]
        if (record.state.intent.scope_mode == "delegated" and len(attempts) < 3
                and candidates and record.state.step + 1 < limit):
            selected = candidates[0]
            inspection = ToolCall(name="inspect_zones", arguments={"zone_ids": [selected]})
            record = self._checkpoint(record, record.state.model_copy(update={"step": record.state.step + 1}),
                                      "scope_fallback_started", {
                "from_zone_ids": record.state.resolved_scope.zone_ids if record.state.resolved_scope else [],
                "to_zone_ids": [selected], "capacity_evidence_ref": result.call_id,
                "candidate_rank": record.state.candidate_zone_ids.index(selected) + 1,
                "reason": outcome["reason"], "attempt_limit": 3,
            })
            inspected = self.gateway.call(GatewayContext(
                project_id=record.state.project_id, expected_state_version=record.state.snapshot_version,
                run_id=record.state.run_id, allowed_zone_ids=frozenset(record.state.candidate_zone_ids),
                intent=record.state.intent,
            ), inspection)
            latest = self.store.get(record.state.project_id, record.state.run_id)
            if latest.revision != record.revision:
                return latest
            record = self._record_result(record, inspected, requested_zones=[selected], resolving_fallback=True)
            if inspected.status == "succeeded":
                retry = ToolCall(name=request.name, arguments={**request.arguments, "zone_ids": [selected]})
                return self._checkpoint(record, record.state.model_copy(update={"placement_retry": retry}),
                                        "scope_fallback_ready", {"zone_ids": [selected], "evidence_ref": inspected.call_id})
        if record.state.intent.scope_mode == "delegated" and attempts:
            # A later failed sample must not erase the useful capacity already
            # established on an earlier ranked candidate.
            best = max(attempts, key=lambda attempt: attempt["found"])
            selected_event = next(event for event in record.events if event.kind == "tool_result"
                                  and event.payload.get("call_id") == best["call_id"])
            result = ToolResult.model_validate(selected_event.payload)
            result.data["scope_search"] = {
                "attempts": attempts, "selected_zone_ids": best["zone_ids"], "limit": 3, "exhaustive": False,
                "reason": "Показан лучший проверенный результат среди рассмотренных участков; целевое количество сохранено.",
            }
            outcome = result.data["placement_outcome"]
            scope = ResolvedScope(
                project_id=record.state.project_id, zone_ids=best["zone_ids"], basis="agent",
                criteria=["candidate_ranked", "capacity_best_result", "bounded_scope_search"],
                source_revision=record.state.snapshot_version, evidence_refs=[attempt["call_id"] for attempt in attempts],
            )
            record = self._checkpoint(record, record.state.model_copy(update={"resolved_scope": scope, "last_result": result}),
                                      "capacity_search_completed", {"selected_call_id": best["call_id"], "attempts": attempts})
        state = record.state.model_copy(update={
            "status": "waiting_question", "pending_approval": None, "placement_retry": None,
            "last_result": result,
            "pending_question": {"slot": "capacity", "question":
                f"{outcome['reason']} Как изменить задание: количество, участок, схему или породы?"},
        })
        return self._checkpoint(record, state, "question", state.pending_question)

    def _selection_question(self, record, issue):
        state = record.state.model_copy(update={"status": "waiting_question", "pending_approval": None,
            "read_outcome": None, "resolved_scope": None,
            "intent": record.state.intent.model_copy(update={"selection_issue": issue}),
            "pending_question": {"slot": "selection", "code": issue.code, "question": issue.message}})
        return self._checkpoint(record, state, "question", state.pending_question)

    @staticmethod
    def _preview_change_set(result: ToolResult, state: AgentRunState | None = None) -> dict[str, Any] | None:
        if result.status not in {"succeeded", "partial"}:
            return None
        verification = verify_preview_data(
            result.data, tool_name=result.name,
            intent=state.intent if state else None,
            zone_ids=state.resolved_scope.zone_ids if state and state.resolved_scope else None,
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
        if request.name in {"prepare_placement", "prepare_existing_change"}:
            if state.intent.scope_mode == "selection" and not selection_problem(state.intent):
                zones, objects = bound_scope(state.intent)
                for field, value in {"zone_ids": zones, **({"object_ids": objects} if request.name == "prepare_existing_change" else {})}.items():
                    if arguments.get(field) != value:
                        changes.append({"field": field, "from": arguments.get(field), "to": value})
                        arguments[field] = value
            rules = assess_requirements(state.intent).rule_ids
            if rules or "condition_rule_ids" in arguments:
                if arguments.get("condition_rule_ids") != rules:
                    arguments["condition_rule_ids"] = rules
                    changes.append({"field": "condition_rule_ids", "from": "intent", "to": rules})
        if request.name == "prepare_existing_change":
            defaults = {"operation": state.intent.goal.operation, "quantity": state.intent.goal.target_count,
                        "quantity_mode": "target" if state.intent.goal.target_count is not None else None,
                        "plant_kind": state.intent.plant_kind, "species_revision_ids": state.intent.species_ids or None}
            if state.intent.edit is not None:
                defaults.update({"edit_action": state.intent.edit.action,
                                 "move_dx_m": state.intent.edit.move_dx_m,
                                 "move_dy_m": state.intent.edit.move_dy_m})
            for field, value in defaults.items():
                if (value is not None or field in {"move_dx_m", "move_dy_m"}) and arguments.get(field) != value:
                    changes.append({"field": field, "from": arguments.get(field), "to": value})
                    arguments[field] = value
            if not arguments.get("zone_ids") and not arguments.get("object_ids") and state.resolved_scope is not None:
                field = "object_ids" if state.resolved_scope.object_ids else "zone_ids"
                arguments[field] = list(getattr(state.resolved_scope, field))
                changes.append({"field": field, "from": "resolved_scope", "to": arguments[field]})
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
            # These slots belong to the immutable brief. Model omissions and
            # smaller fallback quantities cannot weaken them between tools.
            intent_fields = {
                "target_count": state.intent.goal.target_count,
                "quantity_mode": "target" if state.intent.goal.target_count is not None else None,
                "plant_kind": state.intent.plant_kind,
                "arrangement": state.intent.arrangement,
                "species_revision_ids": state.intent.species_ids or None,
            }
            for field, value in intent_fields.items():
                if value is not None and arguments.get(field) != value:
                    changes.append({"field": field, "from": arguments.get(field), "to": value})
                    arguments[field] = value
            if not arguments.get("zone_ids") and state.resolved_scope is not None:
                arguments["zone_ids"] = list(state.resolved_scope.zone_ids)
                changes.append({"field": "zone_ids", "from": "resolved_scope", "to": "typed_context_default"})
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
            execution_attempt_id: str | None = None,
            stopped: Callable[[], bool] | None = None) -> AgentRunRecord:
        """Resume a run and persist every decision/result before continuing."""
        stopped = stopped or (lambda: False)
        record = self.store.get(project_id, run_id)
        if execution_attempt_id is None:
            # Internal callers share the same durable dispatch boundary as
            # HTTP. They may start compiled work, never replay an old attempt.
            record, claimed = self.store.claim_execution(project_id, run_id)
            if not claimed:
                return record
            execution_attempt_id = record.state.execution_attempt_id
            record, started = self.store.start_execution(project_id, run_id, execution_attempt_id)
            if not started:
                return record
        state = record.state
        if state.execution_attempt_id != execution_attempt_id or state.status != "running":
            return record
        if record.state.intent.goal.operation == "release":
            failure = ToolError(
                code="UNSUPPORTED_OPERATION", message="Эта операция пока не подключена к автономному агенту.",
                remedy="Используйте редактор участков." if record.state.intent.goal.operation == "zones" else "Создайте выпуск на странице выпуска проекта.",
            )
            return self._checkpoint(record, record.state.model_copy(update={"status": "failed", "failure": failure}),
                                    "run_failed", failure.model_dump(mode="json"))
        limit = min(max_steps or state.max_steps, state.max_steps)

        while record.state.step < limit:
            record = self.store.get(project_id, run_id)
            state = record.state
            if state.status != "running" or state.execution_attempt_id != execution_attempt_id:
                return record
            if stopped():
                state = record.state.model_copy(update={"status": "cancelled"})
                return self._checkpoint(record, state, "run_cancelled", {"reason": "cancelled"})
            if state.intent.control is not None:
                from app.agent_runtime.control_workflow import prepare_control_command
                return prepare_control_command(record, self.gateway, self.store)
            if state.intent.goal.operation == "zones":
                requirement_check = assess_requirements(state.intent)
                if state.intent.zone is None or requirement_check.status != "supported":
                    state = state.model_copy(update={"status": "waiting_question", "pending_approval": None,
                        "pending_question": {"slot": "zone", "code": "ZONE_INTENT_UNRESOLVED", "question":
                            "; ".join(requirement_check.unresolved)[:550] + ". Можно прислать отдельно участок, новое имя в кавычках или контур проекта."}})
                    return self._checkpoint(record, state, "question", state.pending_question)
            if state.intent.scope_mode == "selection":
                project = (self.gateway.application.get(project_id, lightweight=True)
                           if state.intent.selection_binding is not None else None)
                problem = selection_problem(state.intent, project)
                if problem:
                    return self._selection_question(record, problem)
            try:
                if state.intent.goal.operation == "zones":
                    decision = AgentDecision(action="tool", tool=ToolCall(name="prepare_zone_change", arguments=zone_prepare_arguments(state.intent.zone)))
                elif state.intent.goal.operation == "inspect" and state.intent.read is not None:
                    try:
                        outcome, offset = read_progress(record, self._current_epoch_events(record))
                        if outcome is not None:
                            return self._complete_read(record, outcome)
                        arguments = read_arguments(state.intent, state.resolved_scope, offset=offset)
                    except ValueError as error:
                        return self._read_question(record, str(error))
                    decision = AgentDecision(action="tool", tool=ToolCall(name=state.intent.read.capability, arguments=arguments))
                elif (arguments := existing_arguments(state.intent, state.plan_version)) is not None:
                    decision = AgentDecision(action="tool", tool=ToolCall(name="prepare_existing_change", arguments=arguments))
                elif (arguments := _bound_placement_arguments(state)) is not None:
                    decision = AgentDecision(action="tool", tool=ToolCall(name="prepare_placement", arguments=arguments))
                elif state.placement_retry is not None:
                    decision = AgentDecision(action="tool", tool=state.placement_retry)
                    state = state.model_copy(update={"placement_retry": None})
                else:
                    decision = choose(self._context(record))
            except Exception as error:
                latest = self.store.get(project_id, run_id)
                if latest.revision != record.revision:
                    return latest
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
            latest = self.store.get(project_id, run_id)
            if latest.revision != record.revision:
                return latest
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
                if (record.state.intent.scope_mode == "delegated" and decision_tool.name == "inspect_zones"
                        and (len(decision_tool.arguments.get("zone_ids") or []) != 1
                             or not set(decision_tool.arguments.get("zone_ids") or []).issubset(record.state.candidate_zone_ids))):
                    result = ToolResult(
                        call_id=decision_tool.call_id, name=decision_tool.name, status="blocked",
                        error=ToolError(code="SCOPE_CANDIDATES_REQUIRED", message="Участок ещё не подтверждён списком кандидатов.",
                                        remedy="Сначала вызовите find_zone_candidates и выберите одну зону из результата."),
                    )
                elif (record.state.intent.scope_mode == "delegated"
                        and record.state.resolved_scope is None
                        and (decision_tool.name.startswith("preview") or decision_tool.name in {"prepare_placement", "prepare_existing_change"})):
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
                                allowed_object_ids=frozenset(record.state.resolved_scope.object_ids) if record.state.resolved_scope else frozenset(),
                                intent=record.state.intent,
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
                latest = self.store.get(project_id, run_id)
                if latest.revision != record.revision:
                    return latest
                record = self._record_result(
                    record,
                    result,
                    requested_zones=(decision_tool.arguments.get("zone_ids")
                                     if decision_tool.name == "inspect_zones" else None),
                    tool_fingerprint=fingerprint,
                )
                if result.name == "prepare_zone_change":
                    try:
                        if result.status != "succeeded" or record.state.intent.zone is None:
                            raise ValueError(result.error.message if result.error else "Предложение участка не прошло проверку")
                        verify_zone_preview(record.state.intent.zone, result.data)
                    except ValueError as error:
                        state = record.state.model_copy(update={"status": "waiting_question", "pending_approval": None,
                            "pending_question": {"slot": "zone", "code": result.error.code if result.error else "PREVIEW_NOT_VERIFIED",
                                "question": str(error)[:550] + ". Уточните полное поручение или пересчитайте его после обновления проекта."}})
                        return self._checkpoint(record, state, "question", state.pending_question)
                    state = record.state.model_copy(update={"status": "waiting_approval", "pending_approval": {
                        "kind": "planting_zones", "preview_ref": result.call_id,
                        "reason": "Участок проверен. Применить изменение участка с сохранением посадок?"}})
                    return self._checkpoint(record, state, "approval_requested", state.pending_approval)
                if isinstance(result.data, dict) and result.data.get("selection_issue"):
                    from app.agent_runtime.contracts import SelectionIssue
                    return self._selection_question(record, SelectionIssue.model_validate(result.data["selection_issue"]))
                if result.preview_refusal is not None:
                    refusal = result.preview_refusal
                    state = record.state.model_copy(update={"status": "waiting_question", "pending_approval": None,
                        "pending_question": {"slot": "preview", "code": "DOMAIN_PREVIEW_BLOCKED", "question":
                            "; ".join(refusal.reasons)[:650] + ". " + refusal.remedy}})
                    return self._checkpoint(record, state, "question", state.pending_question)
                if record.state.intent.goal.operation == "inspect" and record.state.intent.read is not None:
                    if result.status == "stale":
                        continue
                    if result.status != "succeeded" or result.read_page is None:
                        return self._read_question(record, (result.error.message if result.error else "Чтение не дало подтверждённого полного результата.")
                                                   + " Уточните область или повторите запрос после обновления проекта.")
                    try:
                        outcome, _ = read_progress(record, self._current_epoch_events(record))
                    except ValueError as error:
                        return self._read_question(record, str(error))
                    if outcome is not None:
                        return self._complete_read(record, outcome)
                    continue
                if result.error is not None and result.error.code == "OBJECT_SELECTION_REQUIRED":
                    selection = ObjectSelectionRequirement.model_validate(result.data["object_selection"])
                    counts = (f"В указанной области найдено посадок: {selection.found_count}; в задании: {selection.requested_count}. "
                              if selection.requested_count is not None else "Подходящие посадки в указанной области не найдены. ")
                    state = record.state.model_copy(update={
                        "status": "waiting_question", "pending_approval": None,
                        "pending_question": {"slot": "objects", "question":
                            counts + "Уточните область или конкретные посадки, либо измените количество."},
                    })
                    return self._checkpoint(record, state, "question", state.pending_question)
                if result.error is not None and result.error.code == "REQUIREMENTS_UNRESOLVED":
                    unresolved = (result.data.get("requirement_check", {}).get("unresolved", []) if isinstance(result.data, dict)
                                  else assess_requirements(record.state.intent).unresolved)
                    state = record.state.model_copy(update={
                        "status": "waiting_question", "pending_approval": None,
                        "pending_question": {"slot": "requirements", "question":
                            "Для этих условий пока нет проверяемого расчёта: " + "; ".join(unresolved)[:350]
                            + ". Уточните задание или условия на карте."},
                    })
                    return self._checkpoint(record, state, "question", state.pending_question)
                if (result.name == "prepare_existing_change" and result.status not in {"succeeded", "stale"}
                        and existing_arguments(record.state.intent, record.state.plan_version) is not None):
                    state = record.state.model_copy(update={"status": "waiting_question", "pending_approval": None,
                        "pending_question": {"slot": "preview", "code": result.error.code if result.error else "PREVIEW_INCOMPLETE",
                            "question": (result.error.message if result.error else "Расчёт изменения не завершён.")
                            + " Уточните задание или повторите расчёт после обновления проекта."}})
                    return self._checkpoint(record, state, "question", state.pending_question)
                if (result.name == "find_zone_candidates" and result.status == "succeeded"
                        and record.state.intent.scope_mode == "delegated" and not record.state.candidate_zone_ids):
                    state = record.state.model_copy(update={
                        "status": "waiting_question", "pending_approval": None,
                        "pending_question": {"slot": "scope", "question":
                            "В проекте нет рабочих участков для расчёта. Подготовьте участок из допустимой области "
                            "на шаге «Настройка», затем продолжите задание."},
                    })
                    return self._checkpoint(record, state, "question", state.pending_question)
                outcome = result.data.get("placement_outcome") if isinstance(result.data, dict) else None
                if (result.name == "prepare_placement" and result.status in {"partial", "blocked"}
                        and isinstance(outcome, dict) and outcome.get("status") in {"partial", "impossible"}):
                    record = self._handle_capacity(record, decision_tool, limit=limit)
                    if record.state.status == "waiting_question":
                        return record
                    continue
                if self._preview_change_set(result, record.state) is not None:
                    state = record.state.model_copy(update={
                        "status": "waiting_approval",
                        "pending_approval": {
                            "kind": "plantings",
                            "preview_ref": result.call_id,
                            "reason": "Предложение рассчитано и проверено. Применить его к плану?",
                        },
                    })
                    return self._checkpoint(record, state, "approval_requested", state.pending_approval or {})
                if (result.name == "prepare_placement" and result.status != "stale"
                        and _bound_placement_arguments(record.state) is not None):
                    # A domain/infrastructure failure is not a capacity fact,
                    # and cannot be repaired by choosing a different scope.
                    state = record.state.model_copy(update={"status": "waiting_question", "pending_approval": None,
                        "pending_question": {"slot": "preview", "code": result.error.code if result.error else "PREVIEW_NOT_VERIFIED",
                            "question": (result.error.message if result.error else "Предложение посадки не прошло проверку.")
                            + " Уточните задание или повторите расчёт после обновления проекта."}})
                    return self._checkpoint(record, state, "question", state.pending_question)
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
                    and self._preview_change_set(ToolResult.model_validate(item.payload), record.state) is not None
                    for item in self._current_epoch_events(record)
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
                    "pending_approval": {"kind": "plantings", "preview_ref": preview_ref, "reason": decision.reason or ""},
                })
                return self._checkpoint(record, state, "approval_requested", state.pending_approval or {})

            if decision.action == "wait":
                state = record.state.model_copy(update={"status": "waiting_job"})
                return self._checkpoint(record, state, "job_waiting", {"job_ref": decision.job_ref})

            if decision.action == "finish":
                if record.state.intent.goal.operation in PLAN_OPERATIONS or record.state.intent.goal.operation == "zones":
                    failure = ToolError(code="FINISH_WITHOUT_VERIFIED_CHANGE", message="Изменение не завершено: нет подтверждённого предложения.",
                                        remedy="Подготовьте preview соответствующей операции и проверьте его перед применением.")
                    return self._checkpoint(record, record.state.model_copy(update={"status": "failed", "failure": failure}),
                                            "run_failed", failure.model_dump(mode="json"))
                return self._read_question(record, "Для этого запроса ещё нет подтверждённого результата чтения. "
                    "Уточните, нужны сохранённые замечания плана или подбор пород для конкретного участка.")

        failure = ToolError(
            code="STEP_BUDGET_EXCEEDED",
            message="Агент достиг лимита шагов без проверяемого результата.",
            retryable=False,
        )
        state = record.state.model_copy(update={"status": "failed", "failure": failure})
        return self._checkpoint(record, state, "run_failed", failure.model_dump(mode="json"))
