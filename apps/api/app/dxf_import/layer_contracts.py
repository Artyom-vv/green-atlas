from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.geometry.axis_contracts import UtilityAxisBinding
from app.geometry.utility_contracts import UtilityContext


class LayerKind(StrEnum):
    SITE_BORDER = "site_border"
    BUILDING = "building"
    ROAD = "road"
    UTILITY = "utility"
    EXISTING_GREEN = "existing_green"
    WATER = "water"
    RESTRICTED = "restricted"
    IGNORE = "ignore"


class BoundaryCandidateStatus(StrEnum):
    USABLE = "usable"
    THIN = "thin"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class LayerSuggestionConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class BoundaryCandidate(BaseModel):
    status: BoundaryCandidateStatus
    basis: str
    area_m2: float = Field(default=0, ge=0)
    inset_1_5m_area_m2: float = Field(default=0, ge=0)
    component_count: int = Field(default=0, ge=0)
    issue: str | None = None


class Layer(BaseModel):
    id: str
    source_name: str
    suggested_kind: LayerKind
    # Optional on persisted legacy projects; every fresh DXF import sets it.
    suggestion_confidence: LayerSuggestionConfidence | None = None
    suggestion_reasons: list[str] = Field(default_factory=list)
    mapping_review_required: bool | None = None
    mapping_confirmed: bool | None = None
    mapped_kind: LayerKind | None = None
    object_count: int
    # Full normalized source extent, independent of the current viewport/LOD.
    bounds: tuple[float, float, float, float] | None = None
    color: str
    linetype: str = "CONTINUOUS"
    lineweight_mm: float | None = None
    entity_types: dict[str, int] = Field(default_factory=dict)
    # A saturated import can retain only a bounded map representation. This
    # distinguishes a safely complete layer from a preview that must not be
    # used as a planting constraint.
    geometry_complete: bool = True
    # Diagnostic source counts, not a permission to clear geometry_complete.
    # Empty defaults on legacy projects mean "not recorded", not full coverage.
    unsupported_geometry_types: dict[str, int] = Field(default_factory=dict)
    # Entity types whose complete per-instance geometry was supplied by an
    # admitted external provider (for example ObjectARX snapshot-v1). Counts
    # are compared with ``entity_types`` before a physical layer is allowed
    # into the calculation; the type name alone never bypasses the gate.
    projected_geometry_types: dict[str, int] = Field(default_factory=dict)
    unreadable_geometry_count: int = Field(default_factory=int, ge=0)
    boundary_candidate: BoundaryCandidate | None = None
    required: bool = False
    visible: bool = True
    utility_context: UtilityContext | None = None
    utility_axis_bindings: list[UtilityAxisBinding] = Field(default_factory=list)


class LayerMapping(BaseModel):
    layer_id: str
    kind: LayerKind
    # Missing means a legacy client explicitly submitted the old mapping
    # form. The new UI sends False for an untouched uncertain suggestion.
    confirmed: bool | None = None
    visible: bool = True
    utility_context: UtilityContext | None = None
    utility_axis_bindings: list[UtilityAxisBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_utility_layer(self) -> Self:
        if (
            self.utility_context is not None or self.utility_axis_bindings
        ) and self.kind != LayerKind.UTILITY:
            raise ValueError(
                "Параметры сети допустимы только для слоя инженерных сетей"
            )
        return self


class LayerMappingRequest(BaseModel):
    mappings: list[LayerMapping]
