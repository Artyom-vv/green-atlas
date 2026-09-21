from __future__ import annotations

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.planning.contracts import Plan, PlanObject, PlanObjectCreate, PlanObjectUpdate
from app.planning.types import (
    CandidateCategory,
    CandidateStatus,
    OperationType,
)
from app.regulations.trace_contracts import PlantingRuleTrace
from app.species.eligibility_contracts import PlantEligibility


class PlanObjectAddOperation(BaseModel):
    type: Literal["add"] = "add"
    object: PlanObjectCreate


class PlanObjectUpdateOperation(BaseModel):
    type: Literal["update"] = "update"
    object_id: str
    changes: PlanObjectUpdate


class PlanObjectDeleteOperation(BaseModel):
    type: Literal["delete"] = "delete"
    object_id: str


PlanChangeOperation = Annotated[
    PlanObjectAddOperation | PlanObjectUpdateOperation | PlanObjectDeleteOperation,
    Field(discriminator="type"),
]


class PlanChangeSetDraft(BaseModel):
    base_plan_version: int = Field(ge=1)
    source: Literal[
        "manual", "group", "pattern", "recommendation", "brush", "system"
    ] = "manual"
    label: str = Field(min_length=1, max_length=160)
    policy: Literal["all_or_nothing"] = "all_or_nothing"
    operations: list[PlanChangeOperation] = Field(min_length=1, max_length=5000)


class ChangeSetCandidateResult(BaseModel):
    operation_index: int = Field(ge=0)
    type: OperationType
    status: CandidateStatus
    code: str
    category: CandidateCategory
    reason: str
    object_id: str | None = None
    rule_id: str | None = None
    source_layer: str | None = None
    source_feature_ids: list[str] = Field(default_factory=list)
    actual_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    required_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    suggested_action: str | None = None
    zone_id: str | None = None
    rule_trace: PlantingRuleTrace | None = None
    assortment: PlantEligibility | None = None


class ChangeSetPreview(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    digest: str
    base_plan_version: int = Field(ge=1)
    source: Literal["manual", "group", "pattern", "recommendation", "brush", "system"]
    label: str
    can_apply: bool
    additions: list[PlanObject] = Field(default_factory=list)
    updates: list[PlanObject] = Field(default_factory=list)
    deletion_ids: list[str] = Field(default_factory=list)
    candidate_results: list[ChangeSetCandidateResult] = Field(default_factory=list)
    expires_at: str


class PlanChangeSetApplyRequest(BaseModel):
    preview_id: str
    digest: str
    base_plan_version: int = Field(ge=1)


class PlanMutationResult(BaseModel):
    change_set_id: str
    plan_version: int = Field(ge=1)
    state_version: int = Field(ge=1)
    added_ids: list[str] = Field(default_factory=list)
    updated_ids: list[str] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)
    affected_bounds: list[float] | None = None
    plan: Plan
