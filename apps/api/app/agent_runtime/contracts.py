"""Stable contracts for autonomous agent execution.

These models deliberately separate user intent from policy and from facts
resolved by tools.  The model may propose a decision, but it cannot redefine a
policy rule or claim that a preview was committed.
"""

from datetime import UTC, datetime
from math import isfinite
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import TypedDict

from app.agent_runtime.history_budget import (
    MAX_HISTORY_CHARS,
    MAX_HISTORY_TURNS,
    join_source_history,
)
from app.agent_runtime.zone_contracts import BoundZoneIntent


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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


class EditIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["species", "move", "lock", "unlock"]
    move_dx_m: float | None = Field(default=None, allow_inf_nan=False)
    move_dy_m: float | None = Field(default=None, allow_inf_nan=False)


class ReadIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: Literal["plan_issues", "species_shortlist"]
    unsupported_requirements: list[str] = Field(default_factory=list, max_length=10)


class ControlIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["focus_zone"] = "focus_zone"
    zone_id: str | None = Field(default=None, min_length=1, max_length=200)
    unsupported_requirements: list[str] = Field(default_factory=list, max_length=20)


class ControlCommand(BaseModel):
    """Immutable server instruction bound to one source and execution attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=120)
    action: Literal["focus_zone"] = "focus_zone"
    project_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=120)
    execution_attempt_id: str = Field(min_length=1, max_length=120)
    zone_id: str = Field(min_length=1, max_length=200)
    zone_label: str = Field(min_length=1, max_length=500)
    state_version: int = Field(ge=1)
    geometry_version: int = Field(ge=0)
    geometry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    bounds: list[float] = Field(min_length=4, max_length=4)
    issued_at: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def valid_bounds(self):
        if (
            not all(isfinite(value) for value in self.bounds)
            or self.bounds[0] >= self.bounds[2]
            or self.bounds[1] >= self.bounds[3]
        ):
            raise ValueError("Control bounds must contain a nonempty area")
        return self


class ControlCommandGeometry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: ControlCommand
    # Digest this exact UTF-8 string, then parse it for map geometry. This
    # avoids differing number serialization in Python and JavaScript.
    geometry_json: str = Field(min_length=1)


class ControlResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: str = Field(min_length=1, max_length=120)
    project_id: str = Field(min_length=1, max_length=200)
    run_id: str = Field(min_length=1, max_length=120)
    execution_attempt_id: str = Field(min_length=1, max_length=120)
    zone_id: str = Field(min_length=1, max_length=200)
    geometry_version: int = Field(ge=0)
    geometry_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["completed", "failed", "cancelled", "unknown"]
    error_code: (
        Literal[
            "MAP_NOT_READY",
            "MAP_UNAVAILABLE",
            "CONTROL_STALE",
            "FOCUS_INTERRUPTED",
            "CONTROL_OUTCOME_UNKNOWN",
        ]
        | None
    ) = None
    message: str | None = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def matching_outcome(self):
        if (self.status == "completed") != (self.error_code is None):
            raise ValueError(
                "A failed control result needs an error; completion cannot carry an error"
            )
        if self.status == "unknown" and self.error_code != "CONTROL_OUTCOME_UNKNOWN":
            raise ValueError("An unknown control outcome must remain explicit")
        return self


class ReadPageEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    run_id: str | None = None
    call_id: str = Field(min_length=1)
    capability: Literal["plan_issues", "species_shortlist"]
    source: Literal["saved_plan_validation", "species_suitability"]
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    object_ids: list[str] = Field(default_factory=list, max_length=5000)
    kind: Literal["tree", "shrub"] | None = None
    snapshot_version: int = Field(ge=1)
    plan_version: int | None = Field(default=None, ge=1)
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    item_ids: list[str] = Field(default_factory=list, max_length=5000)
    next_offset: int | None = Field(default=None, ge=1)
    global_issues_excluded: bool = False
    caveat: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def valid_page(self):
        expected_source = (
            "saved_plan_validation"
            if self.capability == "plan_issues"
            else "species_suitability"
        )
        end = self.offset + len(self.item_ids)
        if self.source != expected_source or (self.zone_ids and self.object_ids):
            raise ValueError("Read evidence has an incompatible source or mixed scope")
        if len(set(self.item_ids)) != len(self.item_ids) or any(
            not identity for identity in self.item_ids
        ):
            raise ValueError("Read evidence contains repeated or missing identifiers")
        if (
            end > self.total
            or self.next_offset != (end if end < self.total else None)
            or (self.next_offset is not None and not self.item_ids)
        ):
            raise ValueError("Read evidence does not describe a complete page")
        if self.capability == "plan_issues" and self.global_issues_excluded != bool(
            self.zone_ids or self.object_ids
        ):
            raise ValueError(
                "Read evidence does not explain the excluded global issues"
            )
        return self


class ReadOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["complete"] = "complete"
    capability: Literal["plan_issues", "species_shortlist"]
    source: Literal["saved_plan_validation", "species_suitability"]
    total: int = Field(ge=0)
    pages: int = Field(ge=1)
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    object_ids: list[str] = Field(default_factory=list, max_length=5000)
    kind: Literal["tree", "shrub"] | None = None
    snapshot_version: int = Field(ge=1)
    plan_version: int | None = Field(default=None, ge=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=512)
    global_issues_excluded: bool = False
    caveat: str = Field(min_length=1, max_length=800)


class ObjectSelectionRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_count: int | None = Field(default=None, ge=1, le=5000)
    found_count: int = Field(ge=0, le=5000)


class SelectionContext(BaseModel):
    """UI snapshot, never an instruction to select or expand a scope."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1, max_length=200)
    state_version: int = Field(ge=1)
    plan_version: int | None = Field(ge=1)
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    object_ids: list[str] = Field(default_factory=list, max_length=5000)


class SelectionReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: Literal["zones", "objects"] | None = None
    plant_kind: Literal["tree", "shrub", "mixed"] | None = None
    unsupported_reason: str | None = Field(default=None, max_length=600)


class SelectionBinding(BaseModel):
    """Source-selected subset and last explicitly accepted project revision."""

    model_config = ConfigDict(extra="forbid")
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    object_ids: list[str] = Field(default_factory=list, max_length=5000)
    state_version: int = Field(ge=1)
    plan_version: int | None = Field(ge=1)

    @model_validator(mode="after")
    def exact_scope(self):
        if bool(self.zone_ids) == bool(self.object_ids):
            raise ValueError("Selection binding requires exactly one nonempty scope")
        if len(set(self.zone_ids)) != len(self.zone_ids) or len(
            set(self.object_ids)
        ) != len(self.object_ids):
            raise ValueError("Selection binding cannot repeat identifiers")
        return self


class SelectionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Literal[
        "SELECTION_MISSING",
        "SELECTION_EMPTY",
        "SELECTION_AMBIGUOUS",
        "SELECTION_STALE",
        "SELECTION_PROJECT_MISMATCH",
        "SELECTION_UNKNOWN",
        "SELECTION_CONFLICT",
    ]
    message: str = Field(min_length=1, max_length=800)


class AgentIntent(BaseModel):
    """Immutable user intent atoms plus explicit delegations.

    ``constraints`` contains only requirements, while subjective words such as
    «плотно» belong in ``preferences``.  Normative policy is represented by a
    policy-owned HardConstraint and is never generated as user text.
    """

    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(min_length=1, max_length=MAX_HISTORY_CHARS)
    # Server-recorded user messages; an empty legacy value means raw_text is
    # one whole message. Display separators never establish trust boundaries.
    source_turns: list[str] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)
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
    edit: EditIntent | None = None
    read: ReadIntent | None = None
    control: ControlIntent | None = None
    zone: BoundZoneIntent | None = None
    selection_context: SelectionContext | None = None
    selection_reference: SelectionReference | None = None
    selection_binding: SelectionBinding | None = None
    selection_issue: SelectionIssue | None = None

    @model_validator(mode="after")
    def history_fits_budget(self):
        if self.source_turns:
            join_source_history(self.source_turns)
        return self


class PendingApproval(TypedDict, total=False):
    preview_ref: str
    reason: str
    # Missing kind is accepted only for durable planting previews from older runs.
    kind: Literal["plantings", "planting_zones"]


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

    call_id: str = Field(
        default_factory=lambda: str(uuid4()), min_length=1, max_length=120
    )
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


class RequirementAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["supported", "unsupported"]
    rule_ids: list[str] = Field(default_factory=list, max_length=10)
    unresolved: list[str] = Field(default_factory=list, max_length=100)


class PlacementRemedy(BaseModel):
    """A change to the brief that must be explicit before another preview."""

    model_config = ConfigDict(extra="forbid")

    code: Literal[
        "reduce_quantity", "change_scope", "change_arrangement", "change_species"
    ]
    label: str = Field(min_length=1, max_length=300)
    requires_user_input: bool = True
    target_count: int | None = Field(default=None, ge=1, le=5000)


class PlacementOutcome(BaseModel):
    """Capacity found by bounded domain search, never an absolute maximum."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["exact", "partial", "impossible"]
    requested: int | None = Field(default=None, ge=1, le=5000)
    found: int = Field(ge=0, le=5000)
    shortfall: int = Field(ge=0, le=5000)
    reason: str | None = Field(default=None, max_length=800)
    remedy_options: list[PlacementRemedy] = Field(default_factory=list, max_length=4)
    search_exhaustive: bool = False

    @model_validator(mode="after")
    def consistent_capacity(self):
        expected = (
            max(0, self.requested - self.found) if self.requested is not None else 0
        )
        if self.shortfall != expected:
            raise ValueError("Placement shortfall must match requested and found")
        if self.status == "exact" and (not self.found or self.shortfall):
            raise ValueError("Exact placement must fulfill the target")
        if self.status == "partial" and not (self.found and self.shortfall):
            raise ValueError("Partial placement must have a positive shortfall")
        if self.status == "impossible" and self.found:
            raise ValueError("Impossible placement cannot have usable positions")
        if self.status != "exact" and (not self.reason or not self.remedy_options):
            raise ValueError(
                "Incomplete placement requires an explanation and remedies"
            )
        return self


class PreviewRefusal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["blocked"] = "blocked"
    candidate_count: int = Field(ge=1)
    blocked_count: int = Field(ge=1)
    reason_codes: list[str] = Field(min_length=1, max_length=20)
    reasons: list[str] = Field(min_length=1, max_length=20)
    remedy: str = Field(min_length=1, max_length=500)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    status: Literal[
        "succeeded", "partial", "blocked", "stale", "cancelled", "failed", "in_progress"
    ]
    data: Any = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=100)
    resource_versions: dict[str, int | str] = Field(default_factory=dict, max_length=20)
    effects: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    error: ToolError | None = None
    verification: VerificationSummary | None = None
    read_page: ReadPageEvidence | None = None
    preview_refusal: PreviewRefusal | None = None


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
    status: Literal[
        "queued",
        "scheduled",
        "running",
        "waiting_question",
        "waiting_approval",
        "waiting_job",
        "waiting_ui",
        "finished",
        "failed",
        "cancelled",
    ] = "queued"
    # queued has no scheduled work. A worker may run only the exact attempt
    # accepted by /run; an answer or explicit restart creates a new epoch.
    execution_attempt_id: str | None = Field(default=None, min_length=1, max_length=120)
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
    read_outcome: ReadOutcome | None = None
    control_command: ControlCommand | None = None
    control_result: ControlResult | None = None
    placement_retry: ToolCall | None = None
    pending_question: dict[str, str] | None = None
    pending_approval: PendingApproval | None = None
    outcome_ref: str | None = None
    failure: ToolError | None = None
    max_steps: int = Field(default=64, ge=1, le=512)
