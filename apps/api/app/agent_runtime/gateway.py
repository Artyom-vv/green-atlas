"""Permissioned execution boundary for capability calls."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Literal

from pydantic import ValidationError

from app.agent_runtime.contracts import AgentIntent, ObjectSelectionRequirement, PreviewRefusal, RequirementAssessment, ToolCall, ToolError, ToolResult, VerificationSummary
from app.agent_conditions import bind_placement_conditions
from app.agent_runtime.registry import CapabilityRegistry, EffectClass
from app.agent_runtime.policy import assess_requirements, capability_allowed
from app.agent_runtime.verifier import verify_preview_data
from app.planning.errors import ExistingTargetSelectionRequired
from app.agent_runtime.read_workflow import read_arguments, verify_read_page
from app.agent_runtime.selection import bound_scope, selection_problem
from app.agent_runtime.zone_workflow import bind_zone_intent, verify_zone_preview, verify_zone_evidence, zone_prepare_arguments
from app.agent_runtime.control_sources import bind_control_source
from app.planting_zone_changes import ZoneChangePreview, get_zone_change_service


class GatewayRejected(ValueError):
    """A call was rejected before a domain function could run."""


@dataclass(frozen=True)
class GatewayPolicy:
    """Per-run effect policy. Writes are never enabled by default."""

    allowed_effects: frozenset[EffectClass] = frozenset({"read", "preview"})
    project_id: str | None = None
    require_approval_for_preview: bool = True


@dataclass(frozen=True)
class GatewayContext:
    project_id: str
    expected_state_version: int | None = None
    run_id: str | None = None
    allowed_zone_ids: frozenset[str] = frozenset()
    allowed_object_ids: frozenset[str] = frozenset()
    approved_change_set_id: str | None = None
    approved_digest: str | None = None
    approved_preview_result: ToolResult | None = None
    approved_preview_kind: Literal["plantings", "planting_zones"] = "plantings"
    intent: AgentIntent | None = None
    policy: GatewayPolicy = field(default_factory=GatewayPolicy)


class ToolGateway:
    """Typed gateway shared by the local runtime and future MCP transport."""

    def __init__(self, application, registry: CapabilityRegistry | None = None):
        self.application = application
        self.registry = registry or CapabilityRegistry()

    def call(self, context: GatewayContext, request: ToolCall) -> ToolResult:
        if context.policy.project_id and context.policy.project_id != context.project_id:
            raise GatewayRejected("Gateway project scope does not match the run")
        try:
            capability = self.registry.get(request.name)
        except KeyError as error:
            raise GatewayRejected(str(error)) from error
        if capability.effect not in context.policy.allowed_effects:
            raise GatewayRejected(f"Capability effect is not allowed: {capability.effect}")
        if context.intent is not None and not capability_allowed(context.intent.goal.operation, request.name, capability.effect):
            raise GatewayRejected("Capability does not match the user's operation")
        control_call = request.name == "focus_zone"
        if capability.effect == "control" and (not control_call or context.intent is None or context.intent.control is None):
            raise GatewayRejected("Control capability requires a source-bound map instruction")
        zone_call = request.name in {"prepare_zone_change", "commit_zone_change"}
        if zone_call and (context.intent is None or context.intent.goal.operation != "zones" or context.intent.zone is None):
            raise GatewayRejected("Zone capability requires a source-bound zone instruction")
        if capability.effect == "write":
            expected_kind = "planting_zones" if request.name == "commit_zone_change" else "plantings"
            if context.approved_preview_kind != expected_kind:
                raise GatewayRejected("Approval belongs to a different preview kind")
            if not context.approved_change_set_id or not context.approved_digest:
                raise GatewayRejected("Write capability requires an approved preview")
            if (request.arguments.get("preview_id") != context.approved_change_set_id
                    or request.arguments.get("digest") != context.approved_digest):
                raise GatewayRejected("Write capability does not match the approved preview")
        if capability.effect == "preview" and context.policy.require_approval_for_preview:
            # Preview is allowed before approval; the flag means commit is
            # never implied by a preview call.  A future write capability must
            # be rejected unless the run explicitly carries approval.
            pass
        if context.intent is not None and context.intent.scope_mode == "selection":
            selection_project = self.application.get(context.project_id, lightweight=True)
            problem = selection_problem(context.intent, selection_project)
            if problem:
                return ToolResult(call_id=request.call_id, name=request.name, status="blocked",
                    data={"selection_issue": problem.model_dump(mode="json")},
                    error=ToolError(code=problem.code, message=problem.message, remedy="Уточните или явно обновите выделение карты."))
        requirements = assess_requirements(context.intent) if context.intent is not None else None
        if capability.effect in {"preview", "write", "control"} and requirements and requirements.status == "unsupported":
            return ToolResult(
                call_id=request.call_id, name=request.name, status="blocked",
                data={"requirement_check": requirements.model_dump(mode="json")},
                error=ToolError(code="REQUIREMENTS_UNRESOLVED", message="Обязательные условия ещё не связаны с поддержанной проверкой.",
                                remedy="Уточните неподдержанные условия; до их разрешения изменение недоступно."),
            )
        # Reject an out-of-scope read before consulting project data.
        if (context.allowed_zone_ids and isinstance(request.arguments.get("zone_ids"), list)
                and all(isinstance(item, str) for item in request.arguments["zone_ids"])
                and set(request.arguments["zone_ids"]) - context.allowed_zone_ids):
            raise GatewayRejected("Zone is outside the agent's verified candidate set")
        if (context.allowed_object_ids and isinstance(request.arguments.get("object_ids"), list)
                and all(isinstance(item, str) for item in request.arguments["object_ids"])
                and set(request.arguments["object_ids"]) - context.allowed_object_ids):
            raise GatewayRejected("Object is outside the agent's verified target set")
        current = self.application.get(context.project_id, lightweight=True)
        if context.expected_state_version is not None and current.state_version != context.expected_state_version:
            return ToolResult(
                call_id=request.call_id,
                name=request.name,
                status="stale",
                resource_versions={
                    "project": current.state_version,
                    "plan": current.plan.version if current.plan else "none",
                },
                error=ToolError(
                    code="STALE_PROJECT",
                    message="Проект изменился до вызова инструмента.",
                    retryable=True,
                    remedy="Обновить снимок проекта и повторить только зависимые шаги.",
                ),
            )
        after = current
        try:
            parsed = capability.arguments.model_validate(request.arguments, extra="forbid")
        except ValidationError as error:
            invalid_arguments = [
                {
                    "path": ".".join(str(part) for part in item.get("loc", [])),
                    "message": str(item.get("msg", "Некорректное значение")),
                }
                for item in error.errors()
            ]
            return ToolResult(
                call_id=request.call_id,
                name=request.name,
                status="failed",
                data={"invalid_arguments": invalid_arguments},
                resource_versions={"project": current.state_version},
                error=ToolError(
                    code="INVALID_TOOL_ARGUMENTS",
                    message="Аргументы инструмента не соответствуют его контракту.",
                    retryable=False,
                    remedy="Исправьте только перечисленные поля и не повторяйте прежний вызов.",
                ),
            )
        arguments = parsed.model_dump(mode="json")
        if control_call:
            intent = context.intent
            target = intent.control.zone_id
            if (arguments != {"zone_id": target} or context.allowed_zone_ids != frozenset({target})
                    or context.allowed_object_ids):
                raise GatewayRejected("Control arguments do not match the exact source-bound target")
            try:
                rebound = bind_control_source(intent.raw_text, self.application.get(context.project_id),
                    source_turns=intent.source_turns)
                if rebound != intent.control or rebound is None or rebound.unsupported_requirements:
                    raise ValueError("Сохранённое поручение показа участка не соответствует исходным сообщениям")
            except (KeyError, ValueError) as error:
                raise GatewayRejected(str(error)) from error
        if zone_call:
            bound = context.intent.zone
            target_id = bound.draft.zone_id
            expected_scope = [target_id] if target_id is not None else []
            if (context.intent.raw_text != bound.source_text or bound.project_id != context.project_id
                    or context.intent.scope_mode != ("project" if target_id is None else "explicit")
                    or context.intent.explicit_zone_ids != expected_scope or context.intent.explicit_object_ids
                    or context.allowed_object_ids
                    or (context.allowed_zone_ids and context.allowed_zone_ids != frozenset(expected_scope))):
                raise GatewayRejected("Zone instruction does not match the source and exact target scope")
            try:
                if bind_zone_intent(bound.source_text, self.application.get(context.project_id), bound.intent, source_turns=bound.source_turns) != bound:
                    raise ValueError("Сохранённые основания изменения участка изменились")
            except (KeyError, ValueError) as error:
                raise GatewayRejected(str(error)) from error
            if request.name == "prepare_zone_change" and arguments != zone_prepare_arguments(bound):
                raise GatewayRejected("Zone arguments do not match the source-bound instruction")
        requested_zones = arguments.get("zone_ids") or []
        requested_objects = arguments.get("object_ids") or []
        if context.allowed_zone_ids and set(requested_zones) - context.allowed_zone_ids:
            raise GatewayRejected("Zone is outside the agent's verified candidate set")
        if context.allowed_object_ids and set(requested_objects) - context.allowed_object_ids:
            raise GatewayRejected("Object is outside the agent's verified target set")
        read_scope = SimpleNamespace(zone_ids=list(context.allowed_zone_ids), object_ids=list(context.allowed_object_ids))
        if context.intent is not None and context.intent.read is not None and request.name == context.intent.read.capability:
            try:
                expected_read = read_arguments(context.intent, read_scope, offset=arguments.get("offset", 0))
            except ValueError as error:
                raise GatewayRejected(str(error)) from error
            if (set(requested_zones) != set(expected_read["zone_ids"]) or set(requested_objects) != set(expected_read["object_ids"])
                    or (request.name == "plan_issues" and (arguments.get("rule_ids") or arguments.get("severity")))
                    or (request.name == "species_shortlist" and arguments.get("kind") != expected_read["kind"])):
                raise GatewayRejected("Read scope or filters do not match the user's query")
        if request.name in {"prepare_placement", "prepare_existing_change"} and context.intent is not None:
            intent = context.intent
            operation = intent.goal.operation
            if request.name == "prepare_existing_change" and arguments["operation"] != operation:
                raise GatewayRejected("Change operation does not match the user's operation")
            if request.name == "prepare_existing_change" and intent.edit is not None:
                if arguments.get("edit_action") != intent.edit.action or any(
                    arguments.get(field) != getattr(intent.edit, field) for field in ("move_dx_m", "move_dy_m")
                ):
                    raise GatewayRejected("Edit action or displacement does not match the user's instruction")
            quantity_key = "target_count" if request.name == "prepare_placement" else "quantity"
            if intent.goal.target_count is not None and (
                arguments.get(quantity_key) != intent.goal.target_count or arguments.get("quantity_mode") != "target"
            ):
                raise GatewayRejected("Change quantity does not match the user's target")
            if intent.scope_mode == "delegated" and not (context.allowed_zone_ids or context.allowed_object_ids):
                raise GatewayRejected("Delegated scope requires verified zone or object evidence")
            if intent.scope_mode in {"explicit", "selection"}:
                intent_zones, intent_objects = bound_scope(intent)
                if set(requested_zones) != set(intent_zones) or set(requested_objects) != set(intent_objects):
                    raise GatewayRejected("Change scope does not match the user's explicit zones or objects")
            if intent.plant_kind is not None and arguments.get("plant_kind") != intent.plant_kind:
                raise GatewayRejected("Plant kind does not match the user's composition")
            if intent.species_ids and set(arguments.get("species_revision_ids") or []) != set(intent.species_ids):
                raise GatewayRejected("Species do not match the user's choice")
            if request.name == "prepare_placement" and intent.arrangement is not None and arguments.get("arrangement") != intent.arrangement:
                raise GatewayRejected("Arrangement does not match the user's choice")
            if requirements and set(arguments.get("condition_rule_ids") or []) != set(requirements.rule_ids):
                raise GatewayRejected("Required policy bindings do not match the user's conditions")
        if capability.effect in {"preview", "write"} and requirements and requirements.rule_ids:
            binding = bind_placement_conditions(self.application.get(context.project_id), ["нормативные отступы"], [],
                passport=self.application.projects.get_data_passport(context.project_id))
            missing = [rule["obstacle_kind"] for rule in binding["rules"]
                       if rule["rule_id"] in requirements.rule_ids and rule["status"] != "geometry_available"]
            if missing:
                assessment = RequirementAssessment(status="unsupported", rule_ids=requirements.rule_ids,
                    unresolved=["Для обязательной проверки не распознана геометрия: " + ", ".join(missing)])
                return ToolResult(call_id=request.call_id, name=request.name, status="blocked",
                    data={"requirement_check": assessment.model_dump(mode="json")},
                    error=ToolError(code="REQUIREMENTS_UNRESOLVED", message="Для обязательных условий недостаточно данных чертежа.",
                                    remedy="Проверьте разметку слоёв зданий и дорог перед новым расчётом."))
        if capability.effect == "write" and context.intent is not None:
            evidence = context.approved_preview_result
            if evidence is None or evidence.status != "succeeded" or not isinstance(evidence.data, dict):
                raise GatewayRejected("Write capability requires verified intent-bound preview evidence")
            if request.name == "commit_zone_change":
                try:
                    if evidence.name != "prepare_zone_change":
                        raise ValueError("Подтверждение относится к другой операции")
                    preview = get_zone_change_service(self.application).get_preview(
                        context.project_id, context.approved_change_set_id, context.approved_digest)
                    expected_ref = f"zone-preview:{context.project_id}:{context.run_id or 'standalone'}:{evidence.call_id}:{preview.id}"
                    if expected_ref not in evidence.evidence_refs:
                        raise ValueError("Предложение не принадлежит текущему запуску агента")
                    verify_zone_evidence(context.intent.zone, evidence.data, preview)
                    if arguments["base_state_version"] != preview.base_state_version:
                        raise ValueError("Подтверждение не соответствует сохранённому предложению участка")
                except (KeyError, ValueError) as error:
                    raise GatewayRejected(str(error)) from error
            else:
                self._verify_plan_write(context, evidence, current)
        try:
            result = capability.executor(self.application, context.project_id, parsed)
            encoded = _json(result)
            after = self.application.get(context.project_id, lightweight=True)
            if capability.effect != "write" and after.state_version != current.state_version:
                raise ValueError("Проект изменился во время расчёта. Повторите запрос")
        except ExistingTargetSelectionRequired as error:
            selection = ObjectSelectionRequirement(requested_count=error.requested_count, found_count=error.found_count)
            return ToolResult(call_id=request.call_id, name=request.name, status="blocked",
                data={"object_selection": selection.model_dump(mode="json")},
                resource_versions={"project": current.state_version},
                error=ToolError(code="OBJECT_SELECTION_REQUIRED", message=str(error),
                                remedy="Укажите нужные посадки или измените количество; произвольный выбор недоступен."))
        except (KeyError, TypeError, ValueError) as error:
            return ToolResult(
                call_id=request.call_id, name=request.name, status="failed",
                resource_versions={"project": after.state_version if capability.effect == "write" else current.state_version},
                error=ToolError(code="TOOL_REJECTED", message=str(error)[:800], retryable=False),
            )
        if request.name == "prepare_zone_change":
            return self._zone_preview_result(context, request, encoded, current)
        if control_call:
            from app.agent_runtime.control_workflow import focus_zone_facts

            try:
                full_project = self.application.get(context.project_id)
                expected = focus_zone_facts(full_project, arguments["zone_id"])
                if full_project.state_version != current.state_version or encoded != expected:
                    raise ValueError("Подготовка показа не соответствует актуальному полному контуру участка")
            except (KeyError, ValueError) as error:
                return ToolResult(call_id=request.call_id, name=request.name, status="failed",
                    verification=VerificationSummary(status="rejected", message=str(error)[:500]),
                    error=ToolError(code="CONTROL_NOT_VERIFIED", message=str(error)[:800]))
            return ToolResult(call_id=request.call_id, name=request.name, status="succeeded", data=encoded,
                verification=VerificationSummary(status="verified", checks=["control_source", "exact_zone", "full_zone_geometry", "project_versions"]),
                resource_versions={"project": current.state_version, "geometry": current.geometry_version},
                evidence_refs=[f"control-preparation:{context.project_id}:{context.run_id or 'standalone'}:{request.call_id}:{expected['geometry_digest']}"],
                effects=[])
        read_page = None
        if capability.effect == "read" and context.intent is not None:
            try:
                read_page = verify_read_page(context.intent, read_scope, request, arguments, encoded,
                    project_id=context.project_id, run_id=context.run_id,
                    snapshot_version=current.state_version, plan_version=current.plan.version if current.plan else None)
            except ValueError as error:
                return ToolResult(call_id=request.call_id, name=request.name, status="failed", data=encoded,
                    resource_versions={"project": current.state_version},
                    error=ToolError(code="READ_RESULT_NOT_VERIFIED", message=str(error),
                                    remedy="Обновите проект и уточните область чтения."))
        verification = verify_preview_data(
            encoded, tool_name=request.name, intent=context.intent,
            zone_ids=list(context.allowed_zone_ids),
        ) if capability.effect == "preview" else None
        outcome = encoded.get("placement_outcome") if isinstance(encoded, dict) else None
        if request.name == "prepare_placement" and isinstance(outcome, dict) and outcome.get("status") in {"partial", "impossible"}:
            return ToolResult(
                call_id=request.call_id, name=request.name,
                status="partial" if outcome["status"] == "partial" else "blocked",
                data=encoded, verification=verification,
                resource_versions={"project": current.state_version},
                error=ToolError(
                    code="PLACEMENT_PARTIAL" if outcome["status"] == "partial" else "PLACEMENT_IMPOSSIBLE",
                    message=outcome["reason"], retryable=False,
                    remedy="Измените задание с помощью предложенных вариантов и рассчитайте новый preview.",
                ),
            )
        if verification is not None and verification.status == "rejected":
            refusal = None
            if (request.name == "prepare_existing_change" and isinstance(encoded, dict)
                    and {"existing_targets", "existing_operation", "target_count", "base_plan_version"}.issubset(verification.checks)):
                from app.contracts import ChangeSetPreview
                try:
                    full = ChangeSetPreview.model_validate(encoded.get("change_set"))
                    saved = self.application.get_change_set_preview(context.project_id, full.id, full.digest, allow_blocked=True)
                    denied = [item for item in full.candidate_results if item.status != "allowed"]
                    if not full.can_apply and denied and saved == full:
                        refusal = PreviewRefusal(candidate_count=len(full.candidate_results), blocked_count=len(denied),
                            reason_codes=list(dict.fromkeys(item.code for item in denied))[:20],
                            reasons=list(dict.fromkeys(item.reason for item in denied))[:20],
                            remedy="Уточните породу, схему или область новым ответом. Применение без проверки недоступно.")
                except (KeyError, ValueError):
                    pass
            return ToolResult(
                call_id=request.call_id,
                name=request.name,
                status="failed",
                data=encoded,
                verification=verification,
                preview_refusal=refusal,
                resource_versions={"project": current.state_version},
                error=ToolError(
                    code="DOMAIN_PREVIEW_BLOCKED" if refusal else "PREVIEW_NOT_VERIFIED",
                    message=("; ".join(refusal.reasons)[:800] if refusal else verification.message or "Preview не прошёл проверку"),
                    retryable=False,
                    remedy=refusal.remedy if refusal else None,
                ),
            )
        return ToolResult(
            call_id=request.call_id,
            name=request.name,
            status="succeeded",
            data=encoded,
            verification=verification,
            resource_versions={
                "project": after.state_version if capability.effect == "write" else current.state_version,
                "plan": after.plan.version if after.plan else "none",
            },
            effects=[],
            read_page=read_page,
        )

    def _verify_plan_write(self, context, evidence, current):
        try:
            preview = self.application.get_change_set_preview(context.project_id, context.approved_change_set_id, context.approved_digest)
            full = preview.model_dump(mode="json")
            data = {**evidence.data, "change_set": full}
            if isinstance(data.get("proposal"), dict):
                data["proposal"] = {**data["proposal"], "change_set": full}
            if evidence.name == "prepare_existing_change":
                affected = set(full.get("deletion_ids", [])) | {item["id"] for item in full.get("updates", [])}
                data["target_before"] = [obj.model_dump(mode="json") for obj in current.plan.objects if obj.id in affected]
            check = verify_preview_data(data, tool_name=evidence.name, intent=context.intent, zone_ids=list(context.allowed_zone_ids))
        except (KeyError, ValueError) as error:
            raise GatewayRejected(str(error)) from error
        if check.status != "verified":
            raise GatewayRejected(check.message or "Approved preview no longer satisfies the intent")

    def _zone_preview_result(self, context, request, encoded, current):
        checks = ["zone_source", "zone_operation", "zone_target", "zone_population", "zone_versions", "zone_digest", "zone_area"]
        try:
            preview = ZoneChangePreview.model_validate(encoded)
            saved = get_zone_change_service(self.application).get_preview(context.project_id, preview.id, preview.digest)
            verify_zone_preview(context.intent.zone, saved, require_applicable=False)
            if saved != preview:
                raise ValueError("Результат не соответствует сохранённому предложению участка")
        except (KeyError, ValueError) as error:
            return ToolResult(call_id=request.call_id, name=request.name, status="failed", data=encoded,
                resource_versions={"project": current.state_version},
                verification=VerificationSummary(status="rejected", message=str(error)[:500]),
                error=ToolError(code="PREVIEW_NOT_VERIFIED", message=str(error)[:800]))
        blocked = not preview.can_apply or bool(preview.blockers or preview.affected_planting_ids)
        message = "; ".join(preview.blockers) or "Изменение затрагивает существующие посадки"
        return ToolResult(call_id=request.call_id, name=request.name,
            status="blocked" if blocked else "succeeded", data=preview.model_dump(mode="json"),
            evidence_refs=[f"zone-preview:{context.project_id}:{context.run_id or 'standalone'}:{request.call_id}:{preview.id}"],
            resource_versions={"project": current.state_version, "geometry": current.geometry_version,
                               "plan": current.plan.version if current.plan else "none"},
            verification=VerificationSummary(status="rejected" if blocked else "verified", checks=checks,
                                             message=message[:500] if blocked else None),
            error=ToolError(code="ZONE_CHANGE_BLOCKED", message=message[:800],
                            remedy="Уточните участок или контур. Существующие посадки должны остаться неизменными.") if blocked else None)


def _json(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    return value
