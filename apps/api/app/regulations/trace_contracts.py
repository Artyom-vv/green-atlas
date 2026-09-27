from typing import Literal

from pydantic import BaseModel, Field

from app.geometry.axis_contracts import AxisDistanceEvidence
from app.geometry.utility_contracts import UtilityContext
from app.regulations.network_rules import NETWORK_RULE_PACK


class RuleTraceBasis(BaseModel):
    project_id: str
    state_version: int = Field(ge=1)
    geometry_version: int = Field(ge=0)
    plan_version: int | None = Field(default=None, ge=1)
    requirement_profile: str
    registry_revision: str
    network_rule_pack: str = NETWORK_RULE_PACK
    source_content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class RuleSourceFeature(BaseModel):
    feature_id: str | None = None
    source_layer: str | None = None
    source_index: int = Field(
        ge=0,
        description="Index among parsed nonempty geometries of obstacle_kind in this geometry revision",
    )
    actual_distance_m: float = Field(ge=0, allow_inf_nan=False)
    utility_context: UtilityContext | None = None
    axis_evidence: AxisDistanceEvidence | None = None


class RuleTraceEntry(BaseModel):
    rule_id: str | None = None
    document_code: str | None = None
    clause: str | None = None
    source_url: str | None = None
    obstacle_kind: str
    status: Literal["passed", "failed", "not_checked", "no_matching_obstacle"]
    code: str
    actual_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    required_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    nearest_features: list[RuleSourceFeature] = Field(
        default_factory=list,
        description="Up to 20 equidistant nearest source features in original order",
    )
    note: str


class PlantingRuleTrace(BaseModel):
    basis: RuleTraceBasis
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    plant_kind: Literal["tree", "shrub"]
    mature_crown_diameter_m: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    current_compliance: Literal["not_established"] = "not_established"
    entries: list[RuleTraceEntry]
