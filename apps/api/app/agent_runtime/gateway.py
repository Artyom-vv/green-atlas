"""Permissioned execution boundary for capability calls."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import ValidationError

from app.agent_runtime.contracts import ToolCall, ToolError, ToolResult
from app.agent_runtime.registry import CapabilityRegistry, EffectClass
from app.agent_runtime.verifier import verify_preview_data


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
    approved_change_set_id: str | None = None
    approved_digest: str | None = None
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
        if capability.effect == "write":
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
        requested_zones = request.arguments.get("zone_ids")
        if context.allowed_zone_ids and isinstance(requested_zones, list):
            if set(requested_zones) - context.allowed_zone_ids:
                raise GatewayRejected("Zone is outside the agent's verified candidate set")
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
        try:
            result = capability.executor(self.application, context.project_id, parsed)
            encoded = _json(result)
            after = self.application.get(context.project_id, lightweight=True)
            if capability.effect != "write" and after.state_version != current.state_version:
                raise ValueError("Проект изменился во время расчёта. Повторите запрос")
        except (KeyError, TypeError, ValueError) as error:
            return ToolResult(
                call_id=request.call_id,
                name=request.name,
                status="failed",
                resource_versions={"project": after.state_version if capability.effect == "write" else current.state_version},
                error=ToolError(code="TOOL_REJECTED", message=str(error)[:800], retryable=False),
            )
        verification = verify_preview_data(encoded, tool_name=request.name) if capability.effect == "preview" else None
        if verification is not None and verification.status == "rejected":
            return ToolResult(
                call_id=request.call_id,
                name=request.name,
                status="failed",
                data=encoded,
                verification=verification,
                resource_versions={"project": current.state_version},
                error=ToolError(
                    code="PREVIEW_NOT_VERIFIED",
                    message=verification.message or "Preview не прошёл проверку",
                    retryable=False,
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
        )


def _json(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    return value
