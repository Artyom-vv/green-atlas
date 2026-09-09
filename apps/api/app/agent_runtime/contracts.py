"""Stable contracts for autonomous agent execution.

These models deliberately separate user intent from policy and from facts
resolved by tools.  The model may propose a decision, but it cannot redefine a
policy rule or claim that a preview was committed.
"""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["place", "edit", "delete", "inspect", "zones", "release"]
    target_count: int | None = Field(default=None, ge=1, le=5000)
    acceptance: list[str] = Field(default_factory=list, max_length=30)


class HardConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(min_length=1, max_length=120)
    source_text: str | None = Field(default=None, max_length=500)
    policy_owned: bool = False


class Preference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Literal["density", "balance", "visual", "coverage", "proximity", "composition"]
    value: str | float | int | bool
    source_text: str | None = Field(default=None, max_length=500)


class Delegation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: Literal["scope", "species", "arrangement", "quantity", "presentation"]
    strategy: Literal["agent", "policy", "best_evidence"]
    reason: str | None = Field(default=None, max_length=300)


class IntentEvidence(BaseModel):
    """Typed, user-visible semantic anchors used to audit compilation.

    These are server-derived concept matches, not model reasoning.  They make
    a correction explainable when a local model confuses a destructive action
    with a placement request or drops one side of a mixed composition.
    """

    model_config = ConfigDict(extra="forbid")

    operation: list[str] = Field(default_factory=list, max_length=20)
    plant_kind: list[str] = Field(default_factory=list, max_length=10)
    arrangement: list[str] = Field(default_factory=list, max_length=10)
    delegations: list[str] = Field(default_factory=list, max_length=10)
    corrections: list[str] = Field(default_factory=list, max_length=20)


class AgentIntent(BaseModel):
    """Immutable user intent atoms plus explicit delegations.

    ``constraints`` contains only requirements, while subjective words such as
    «плотно» belong in ``preferences``.  Normative policy is represented by a
    policy-owned HardConstraint and is never generated as user text.
    """

    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1, max_length=2000)
    goal: Goal
    scope_mode: Literal["explicit", "selection", "delegated", "project"] = "explicit"
    explicit_zone_ids: list[str] = Field(default_factory=list, max_length=80)
    explicit_object_ids: list[str] = Field(default_factory=list, max_length=5000)
    hard_constraints: list[HardConstraint] = Field(default_factory=list, max_length=100)
    unresolved_requirements: list[str] = Field(default_factory=list, max_length=100)
    preferences: list[Preference] = Field(default_factory=list, max_length=50)
    delegations: list[Delegation] = Field(default_factory=list, max_length=20)
    evidence: IntentEvidence = Field(default_factory=IntentEvidence)
    arrangement: str | None = Field(default=None, max_length=80)
    plant_kind: Literal["tree", "shrub", "mixed"] | None = None
    species_ids: list[str] = Field(default_factory=list, max_length=100)
    post_action: Literal["focus_map"] | None = None


class ResolvedScope(BaseModel):
    """One authoritative scope chosen from explicit input or tool evidence."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=200)
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    object_ids: list[str] = Field(default_factory=list, max_length=5000)
    basis: Literal["user", "selection", "agent", "policy"]
    criteria: list[str] = Field(default_factory=list, max_length=20)
    source_revision: int = Field(ge=1)
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def has_scope(self):
        if not self.zone_ids and not self.object_ids:
            raise ValueError("Resolved scope must contain a zone or object")
        if self.zone_ids and self.object_ids:
            raise ValueError("Resolved scope cannot mix zones and objects")
        return self


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=800)
    retryable: bool = False
    remedy: str | None = Field(default=None, max_length=500)


class VerificationSummary(BaseModel):
    """Typed evidence that a preview is safe to present for approval."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["verified", "not_applicable", "rejected"]
    checks: list[str] = Field(default_factory=list, max_length=20)
    message: str | None = Field(default=None, max_length=500)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    status: Literal["succeeded", "partial", "blocked", "stale", "cancelled", "failed", "in_progress"]
    data: Any = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    resource_versions: dict[str, int | str] = Field(default_factory=dict, max_length=20)
    effects: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    error: ToolError | None = None
    verification: VerificationSummary | None = None


class AgentDecision(BaseModel):
    """Closed decision union used by the planner model."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["tool", "ask", "approval", "finish", "wait"]
    tool: ToolCall | None = None
    question: str | None = Field(default=None, max_length=500)
    missing_slot: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=500)
    preview_ref: str | None = Field(default=None, max_length=120)
    outcome_ref: str | None = Field(default=None, max_length=120)
    job_ref: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_action_payload(self):
        if self.action == "tool" and self.tool is None:
            raise ValueError("Tool decision requires a tool call")
        if self.action == "ask" and (not self.question or not self.missing_slot):
            raise ValueError("Ask decision requires one missing slot and a question")
        if self.action == "approval" and not self.preview_ref:
            raise ValueError("Approval decision requires a preview reference")
        if self.action == "finish" and not self.outcome_ref:
            raise ValueError("Finish decision requires an outcome reference")
        if self.action == "wait" and not self.job_ref:
            raise ValueError("Wait decision requires a job reference")
        return self


class AgentRunState(BaseModel):
    """Durable state restored after a process crash or human interruption."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=120)
    project_id: str = Field(min_length=1, max_length=200)
    conversation_id: str | None = Field(default=None, max_length=120)
    status: Literal["queued", "running", "waiting_question", "waiting_approval", "waiting_job", "finished", "failed", "cancelled"] = "queued"
    intent: AgentIntent
    resolved_scope: ResolvedScope | None = None
    candidate_zone_ids: list[str] = Field(default_factory=list, max_length=80)
    snapshot_version: int = Field(ge=1)
    plan_version: int | None = Field(default=None, ge=1)
    step: int = Field(default=0, ge=0)
    tool_calls: list[str] = Field(default_factory=list, max_length=512)
    # Stable identities let the runtime detect a model repeating the same
    # capability call with a fresh volatile call id.
    tool_fingerprints: list[str] = Field(default_factory=list, max_length=512)
    evidence_refs: list[str] = Field(default_factory=list, max_length=512)
    last_result: ToolResult | None = None
    pending_question: dict[str, str] | None = None
    pending_approval: dict[str, str] | None = None
    outcome_ref: str | None = None
    failure: ToolError | None = None
    max_steps: int = Field(default=64, ge=1, le=512)
