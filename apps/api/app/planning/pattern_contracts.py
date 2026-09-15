from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.planning.change_contracts import ChangeSetPreview
from app.planning.types import (
    RejectedCategory,
    RejectedStatus,
)


class RowPatternRequest(BaseModel):
    type: Literal["row"] = "row"
    base_plan_version: int = Field(ge=1)
    plant_kind: Literal["tree", "shrub"] = "tree"
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    axis: dict[str, Any]
    spacing_m: float = Field(default=6, ge=0.5, le=100, allow_inf_nan=False)
    placement_mode: Literal["count", "spacing"] = "spacing"
    target_count: int = Field(default=20, ge=2, le=5000)
    start_offset_m: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    end_offset_m: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    side: Literal["center", "left", "right", "both"] = "center"
    lateral_offset_m: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    layout_radius_m: float | None = Field(
        default=None, gt=0, le=25, allow_inf_nan=False
    )
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"

    @model_validator(mode="after")
    def validate_side_offset(self) -> RowPatternRequest:
        if self.side != "center" and self.lateral_offset_m <= 0:
            raise ValueError("Для бокового ряда укажите поперечный отступ")
        return self


class FillPatternRequest(BaseModel):
    type: Literal["fill"] = "fill"
    base_plan_version: int = Field(ge=1)
    plant_kind: Literal["tree", "shrub"] = "tree"
    composition: Literal["trees", "shrubs", "mixed"] | None = None
    tree_share: float = Field(default=0.65, ge=0, le=1, allow_inf_nan=False)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    placement_mode: Literal["count", "spacing"] = "spacing"
    target_count: int = Field(default=40, ge=1, le=5000)
    zone_distribution: Literal["available", "equal"] = "available"
    layout: Literal["regular", "staggered", "natural"] = "staggered"
    spacing_m: float = Field(default=6, ge=0.5, le=100, allow_inf_nan=False)
    edge_offset_m: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    angle_deg: float = Field(default=0, ge=-180, le=180, allow_inf_nan=False)
    seed: int = Field(default=1, ge=0, le=2_147_483_647)
    layout_radius_m: float | None = Field(
        default=None, gt=0, le=25, allow_inf_nan=False
    )
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    tree_species_revision_id: str | None = None
    shrub_species_revision_id: str | None = None
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"

    @model_validator(mode="after")
    def normalize_composition(self) -> FillPatternRequest:
        if self.composition is None:
            self.composition = "shrubs" if self.plant_kind == "shrub" else "trees"
        if self.composition == "shrubs":
            self.plant_kind = "shrub"
        else:
            # Mixed layouts use the stricter tree footprint for candidate
            # generation; individual candidate kinds are assigned later.
            self.plant_kind = "tree"
        return self


class PlacementMaskRequest(BaseModel):
    """A repeatable, project-aware planting arrangement preset.

    Masks describe an operator's spatial intent.  They only propose candidate
    positions; the ordinary change-set preview remains the authority for every
    statutory setback, occupied contour and plant-to-plant distance.
    """

    type: Literal["mask"] = "mask"
    mask_id: Literal[
        "road_edges",
        "regular_grid",
        "cluster_groves",
        "building_screen",
        "building_contour",
    ]
    building_offset_m: float | None = Field(
        default=None, ge=0.5, le=60, allow_inf_nan=False
    )
    screen_side: Literal["perimeter", "roads"] = "perimeter"
    base_plan_version: int = Field(ge=1)
    plant_kind: Literal["tree", "shrub"] = "tree"
    composition: Literal["trees", "shrubs", "mixed"] | None = None
    tree_share: float = Field(default=0.65, ge=0, le=1, allow_inf_nan=False)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    placement_mode: Literal["count", "spacing"] = "count"
    target_count: int = Field(default=40, ge=1, le=5000)
    zone_distribution: Literal["available", "equal"] = "available"
    spacing_m: float = Field(default=6, ge=0.5, le=100, allow_inf_nan=False)
    edge_offset_m: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    angle_deg: float = Field(default=0, ge=-180, le=180, allow_inf_nan=False)
    seed: int = Field(default=1, ge=0, le=2_147_483_647)
    # Road-edge masks follow the recognised road footprint at this offset.
    # The statutory check is still performed independently for every point.
    road_offset_m: float = Field(default=3, ge=0.5, le=30, allow_inf_nan=False)
    # Cluster centres stay separated while plants inside a grove use the
    # ordinary spacing policy and mature-crown calculation.
    cluster_gap_m: float = Field(default=18, ge=3, le=100, allow_inf_nan=False)
    cluster_size: int = Field(default=7, ge=3, le=19)
    layout_radius_m: float | None = Field(
        default=None, gt=0, le=25, allow_inf_nan=False
    )
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    tree_species_revision_id: str | None = None
    shrub_species_revision_id: str | None = None
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"

    @model_validator(mode="after")
    def normalize_composition(self) -> PlacementMaskRequest:
        if self.composition is None:
            self.composition = "shrubs" if self.plant_kind == "shrub" else "trees"
        if self.composition == "shrubs":
            self.plant_kind = "shrub"
        else:
            self.plant_kind = "tree"
        return self


PatternPreviewRequest = Annotated[
    RowPatternRequest | FillPatternRequest | PlacementMaskRequest,
    Field(discriminator="type"),
]


class PlacementMaskPreset(BaseModel):
    id: Literal["road_edges", "regular_grid", "cluster_groves"]
    title: str
    description: str
    available: bool = True
    unavailable_reason: str | None = None


class PatternSkippedCandidate(BaseModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    status: RejectedStatus = "blocked"
    code: str = "PLACEMENT_BLOCKED"
    category: RejectedCategory = "constraint"
    reason: str
    rule_id: str | None = None
    source_layer: str | None = None
    source_feature_ids: list[str] = Field(default_factory=list)
    actual_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    required_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    suggested_action: str | None = None
    zone_id: str | None = None


class CandidateReasonSummary(BaseModel):
    status: RejectedStatus
    code: str
    category: RejectedCategory
    count: int = Field(ge=1)
    message: str


class PatternZoneAllocation(BaseModel):
    zone_id: str
    requested_count: int | None = Field(default=None, ge=0)
    accepted_count: int = Field(ge=0)


class PatternPreview(BaseModel):
    pattern_id: str
    type: Literal["row", "fill", "mask"]
    mask_id: (
        Literal[
            "road_edges",
            "regular_grid",
            "cluster_groves",
            "building_screen",
            "building_contour",
        ]
        | None
    ) = None
    requested_count: int = Field(ge=0)
    generated_count: int = Field(default=0, ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(default=0, ge=0)
    capacity_shortfall: int = Field(default=0, ge=0)
    effective_spacing_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    zone_allocations: list[PatternZoneAllocation] = Field(default_factory=list)
    skipped: list[PatternSkippedCandidate] = Field(default_factory=list)
    reason_summary: list[CandidateReasonSummary] = Field(default_factory=list)
    unverified_data: list[str] = Field(default_factory=list)
    data_confidence: Literal["verified", "limited", "blocked"] = "verified"
    data_confidence_reasons: list[str] = Field(default_factory=list)
    change_set: ChangeSetPreview | None = None


class BrushStroke(BaseModel):
    mode: Literal["add", "subtract"] = "add"
    geometry: dict[str, Any]


class BrushPreviewRequest(BaseModel):
    base_plan_version: int = Field(ge=1)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    strokes: list[BrushStroke] = Field(min_length=1, max_length=40)
    width_m: float = Field(default=12, ge=1, le=100, allow_inf_nan=False)
    spacing_m: float = Field(default=6, ge=1, le=30, allow_inf_nan=False)
    density: Literal["sparse", "balanced", "dense"] = "balanced"
    composition: Literal["trees", "shrubs", "mixed"] = "trees"
    tree_share: float = Field(default=0.7, ge=0, le=1, allow_inf_nan=False)
    seed: int = Field(default=47, ge=0, le=2_147_483_647)
    max_sites: int = Field(default=500, ge=1, le=500)
    tree_species_revision_id: str | None = None
    shrub_species_revision_id: str | None = None
    size_class: Literal["sapling", "standard", "large"] = "standard"


class BrushPreview(BaseModel):
    brush_id: str
    requested_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    added_count: int = Field(ge=0)
    removed_count: int = Field(ge=0)
    skipped: list[PatternSkippedCandidate] = Field(default_factory=list)
    reason_summary: list[CandidateReasonSummary] = Field(default_factory=list)
    change_set: ChangeSetPreview | None = None
