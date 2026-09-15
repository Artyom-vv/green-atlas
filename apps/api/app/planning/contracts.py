from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.regulations.trace_contracts import PlantingRuleTrace
from app.species.contracts import GrowthEnvelopeForecast
from app.validation.contracts import PlanValidationBasis, ValidationIssue


class PlanObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["tree", "shrub"]
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    radius: float = Field(gt=0, le=25, allow_inf_nan=False)
    layout_radius_m: float | None = Field(
        default=None, gt=0, le=25, allow_inf_nan=False
    )
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    pattern_id: str | None = None
    group_ids: list[str] = Field(default_factory=list, max_length=50)
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"
    locked: bool = False
    canopy_forecast: list[GrowthEnvelopeForecast] = Field(default_factory=list)
    root_forecast: list[GrowthEnvelopeForecast] = Field(default_factory=list)
    status: Literal["valid", "warning", "error"] = "valid"
    planting_zone_id: str | None = None

    @model_validator(mode="after")
    def migrate_layout_radius(self) -> PlanObject:
        # ``radius`` described the footprint used by the early manual editor.
        # Preserve it for old clients and DXF exports, while making the
        # layout meaning explicit. It must never be presented as an adult
        # crown or root forecast.
        if self.layout_radius_m is None:
            self.layout_radius_m = self.radius
        return self


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    objects: list[PlanObject] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)
    validation_basis: PlanValidationBasis | None = None

    @model_validator(mode="after")
    def remove_obsolete_crown_advice(self) -> Plan:
        """Migrate persisted false-positive crown notices on read.

        Older plans stored a generic warning for every mature crown wider
        than five metres. It was not a concrete conflict and the current
        generator already uses the forecast envelope when choosing points.
        Drop that legacy notice, including its orange object state, while
        preserving any independent issue attached to the same object.
        """
        obsolete_object_ids = {
            issue.object_id
            for issue in self.issues
            if issue.code == "CROWN_SETBACK_REVIEW" and issue.object_id
        }
        if not obsolete_object_ids:
            return self
        self.issues = [
            issue for issue in self.issues if issue.code != "CROWN_SETBACK_REVIEW"
        ]
        issue_object_ids = {issue.object_id for issue in self.issues if issue.object_id}
        missing_network_source = any(
            issue.code == "NO_NETWORK_FEATURES" and issue.object_id is None
            for issue in self.issues
        )
        for object_ in self.objects:
            if (
                object_.id in obsolete_object_ids
                and object_.id not in issue_object_ids
                and not missing_network_source
            ):
                object_.status = "valid"
        return self


class PlacementCheck(BaseModel):
    allowed: bool
    status: Literal["allowed", "blocked", "unknown"]
    kind: Literal["tree", "shrub"]
    x: float
    y: float
    radius: float
    reason: str
    rule_id: str | None = None
    zone_id: str | None = None
    code: str | None = None
    category: str | None = None
    source_layer: str | None = None
    source_feature_ids: list[str] = Field(default_factory=list)
    actual_distance_m: float | None = None
    required_distance_m: float | None = None
    suggested_action: str | None = None
    plan_version: int | None = None
    geometry_version: int | None = None
    state_version: int | None = None
    rule_trace: PlantingRuleTrace | None = None


class PlanObjectCreate(BaseModel):
    kind: Literal["tree", "shrub"]
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    radius: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    layout_radius_m: float | None = Field(
        default=None, gt=0, le=25, allow_inf_nan=False
    )
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    pattern_id: str | None = None
    group_ids: list[str] = Field(default_factory=list, max_length=50)
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"
    locked: bool = False


class PlacementCheckRequest(PlanObjectCreate):
    """The same candidate as Add, optionally bound to the caller's snapshot."""

    base_plan_version: int | None = Field(default=None, ge=1)
    geometry_version: int | None = Field(default=None, ge=0)
    state_version: int | None = Field(default=None, ge=1)


class PlanObjectUpdate(BaseModel):
    x: float | None = Field(default=None, allow_inf_nan=False)
    y: float | None = Field(default=None, allow_inf_nan=False)
    radius: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    layout_radius_m: float | None = Field(
        default=None, gt=0, le=25, allow_inf_nan=False
    )
    size_class: Literal["unspecified", "sapling", "standard", "large"] | None = None
    species_revision_id: str | None = None
    pattern_id: str | None = None
    group_ids: list[str] | None = Field(default=None, max_length=50)
    spacing_policy: Literal["open", "balanced", "canopy"] | None = None
    locked: bool | None = None


class PlanObjectsDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=5000)
