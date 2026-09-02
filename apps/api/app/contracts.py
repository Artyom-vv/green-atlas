from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectStatus(StrEnum):
    EMPTY = "empty"
    IMPORTED = "imported"
    MAPPED = "mapped"
    ZONES_SELECTED = "zones_selected"
    EDITING = "editing"


class ImportMode(StrEnum):
    """How the current project's source was opened.

    A plain DXF that contains Green Atlas planting layers is deliberately not
    treated as an editable revision: without the release manifest those
    circles are indistinguishable from existing vegetation.  A complete
    release bundle carries the source and plan semantics needed to continue
    editing safely.
    """

    SOURCE_DXF = "source_dxf"
    RELEASE_BUNDLE = "release_bundle"
    PLAIN_DXF_FALLBACK = "plain_dxf_fallback"


class ImportEditability(StrEnum):
    EDITABLE = "editable"
    READ_ONLY = "read_only"


class ImportStatus(BaseModel):
    mode: ImportMode = ImportMode.SOURCE_DXF
    editability: ImportEditability = ImportEditability.EDITABLE
    release_id: str | None = None
    message: str = "Исходный DXF доступен для подготовки редактируемого плана."


class OperationKind(StrEnum):
    CALCULATE_GEOMETRY = "calculate_geometry"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"


class OperationError(BaseModel):
    code: str
    message: str


class ProjectOperation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    project_id: str
    kind: OperationKind
    status: OperationStatus = OperationStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    progress_mode: Literal["determinate", "indeterminate"] = "indeterminate"
    stage: str = "Операция поставлена в очередь"
    processed_items: int | None = Field(default=None, ge=0)
    total_items: int | None = Field(default=None, ge=0)
    progress_unit: str | None = None
    error: OperationError | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    cancel_requested_at: str | None = None
    retry_of_operation_id: str | None = None
    project_state_version: int = Field(default=1, ge=1)


class LayerKind(StrEnum):
    SITE_BORDER = "site_border"
    BUILDING = "building"
    ROAD = "road"
    UTILITY = "utility"
    EXISTING_GREEN = "existing_green"
    WATER = "water"
    RESTRICTED = "restricted"
    IGNORE = "ignore"


class SourceFile(BaseModel):
    name: str
    size: int
    imported_at: str
    owner: str | None = None
    dxf_version: str
    units: str
    units_assumed: bool = False
    entity_count: int
    bounds: list[float] | None = None
    warnings: list[str] = Field(default_factory=list)


class Layer(BaseModel):
    id: str
    source_name: str
    suggested_kind: LayerKind
    mapped_kind: LayerKind | None = None
    object_count: int
    color: str
    linetype: str = "CONTINUOUS"
    lineweight_mm: float | None = None
    entity_types: dict[str, int] = Field(default_factory=dict)
    # A saturated import can retain only a bounded map representation. This
    # distinguishes a safely complete layer from a preview that must not be
    # used as a planting constraint.
    geometry_complete: bool = True
    required: bool = False
    visible: bool = True


class DataPassportEntry(BaseModel):
    """One source-data class and its role in the current calculation.

    The passport is deliberately a compact audit contract.  It reports what
    the importer can prove from the DXF and never treats an absent layer as an
    empty physical area.
    """

    kind: Literal[
        "site_border",
        "building",
        "road",
        "utility",
        "existing_green",
        "water",
        "restricted",
        "unclassified",
    ]
    label: str
    status: Literal["verified", "partial", "missing", "excluded"]
    layer_names: list[str] = Field(default_factory=list)
    object_count: int = Field(default=0, ge=0)
    used_object_count: int = Field(default=0, ge=0)
    used_in_calculation: bool = False
    semantic_confidence: Literal["high", "medium", "low"] = "low"
    decision_level: Literal["stop", "warning", "advisory"] = "advisory"
    source_file_name: str | None = None
    source_imported_at: str | None = None
    source_owner: str | None = None
    note: str


class DataPassport(BaseModel):
    """Evidence summary shown before an operator starts mass placement."""

    overall_status: Literal["verified", "limited", "not_ready"]
    calculation_status: Literal["ready", "not_ready"]
    mass_placement_status: Literal["verified", "limited", "blocked"]
    summary: str
    source_file_name: str | None = None
    source_imported_at: str | None = None
    source_owner: str | None = None
    coordinate_reference: CoordinateReference = Field(default_factory=lambda: CoordinateReference())
    entries: list[DataPassportEntry] = Field(default_factory=list)
    unclassified_layers: list[str] = Field(default_factory=list)
    incomplete_layers: list[str] = Field(default_factory=list)
    excluded_layers: list[str] = Field(default_factory=list)
    used_in_calculation: list[str] = Field(default_factory=list)
    missing_classes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class LayerMapping(BaseModel):
    layer_id: str
    kind: LayerKind
    visible: bool = True


class PlantingZoneAssignment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    label: str = Field(min_length=1, max_length=160)
    geometry: dict[str, Any]


class CoordinateReference(BaseModel):
    status: Literal["unknown", "local", "declared", "verified"] = "unknown"
    crs_id: str | None = None
    name: str | None = None
    source: Literal["none", "dxf_geodata", "user_declared", "control_points"] = "none"
    axis_order: Literal["xy", "yx"] | None = None
    control_points_count: int = Field(default=0, ge=0)
    evidence: str = "Система координат не указана"
    updated_at: str | None = None


class GeometrySnapshot(BaseModel):
    feature_collection: dict[str, Any]
    site_area_m2: float | None = Field(default=None, ge=0)
    planning_area_m2: float | None = Field(default=None, ge=0)
    allowed_area_m2: float | None = Field(default=None, ge=0)


class DxfImportResult(BaseModel):
    layers: list[Layer]
    geometry: GeometrySnapshot
    dxf_version: str
    units: str
    units_assumed: bool = False
    entity_count: int
    bounds: list[float] | None = None
    warnings: list[str] = Field(default_factory=list)
    coordinate_reference: CoordinateReference = Field(default_factory=CoordinateReference)


class GrowthEnvelopeForecast(BaseModel):
    """A bounded biological forecast, never a regulatory exclusion zone."""

    horizon_year: int = Field(ge=0, le=40)
    radius_min_m: float = Field(ge=0, allow_inf_nan=False)
    radius_max_m: float = Field(ge=0, allow_inf_nan=False)
    confidence: Literal["low", "medium", "high"]
    basis: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_range(self) -> "GrowthEnvelopeForecast":
        if self.radius_max_m < self.radius_min_m:
            raise ValueError("Верхняя граница прогноза должна быть не меньше нижней")
        return self


class PlanObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["tree", "shrub"]
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    radius: float = Field(gt=0, le=25, allow_inf_nan=False)
    layout_radius_m: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
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
    def migrate_layout_radius(self) -> "PlanObject":
        # ``radius`` described the footprint used by the early manual editor.
        # Preserve it for old clients and DXF exports, while making the
        # layout meaning explicit. It must never be presented as an adult
        # crown or root forecast.
        if self.layout_radius_m is None:
            self.layout_radius_m = self.radius
        return self


class ValidationIssue(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    severity: Literal["warning", "error"]
    code: str
    title: str
    description: str
    object_id: str | None = None
    actual: float | int | None = None
    required: float | int | None = None
    unit: str | None = None
    rule_id: str | None = None
    x: float | None = None
    y: float | None = None
    suggested_action: str | None = None
    related_object_ids: list[str] = Field(default_factory=list)

    @field_validator("severity", mode="before")
    @classmethod
    def migrate_legacy_recommendation_severity(cls, value: object) -> object:
        """Keep pre-simplification SQLite projects readable.

        Earlier builds stored non-blocking advice as ``recommendation``.
        The current manual-planning flow intentionally exposes just two
        actionable severities: a warning or an error.  Legacy advice is a
        warning, rather than an invalid project record that hides the list.
        """
        return "warning" if value == "recommendation" else value


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    objects: list[PlanObject] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    version: int = Field(default=1, ge=1)


class PlacementCheckRequest(BaseModel):
    kind: Literal["tree", "shrub"]
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    radius: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)


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


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    status: ProjectStatus = ProjectStatus.EMPTY
    source_file: SourceFile | None = None
    # The source DXF remains the immutable CAD input.  ``import_status``
    # tells clients whether the current payload also carries the release
    # semantics required for another editable revision.
    import_status: ImportStatus = Field(default_factory=ImportStatus)
    layers: list[Layer] = Field(default_factory=list)
    coordinate_reference: CoordinateReference = Field(default_factory=CoordinateReference)
    source_geometry: GeometrySnapshot | None = None
    geometry: GeometrySnapshot | None = None
    # Map payloads are intentionally stripped from ordinary project reads.
    # Keep this small durable flag so the web client can distinguish an
    # imported source preview from a prepared map even when the DXF has no
    # outer site contour (and consequently no calculated area metric).
    map_ready: bool = False
    geometry_version: int = Field(default=0, ge=0)
    planting_zones: list[PlantingZoneAssignment] = Field(default_factory=list)
    site_area_m2: float | None = Field(default=None, ge=0)
    planning_area_m2: float | None = Field(default=None, ge=0)
    allowed_area_m2: float | None = Field(default=None, ge=0)
    plan: Plan | None = None
    state_version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("status", mode="before")
    @classmethod
    def migrate_legacy_status(cls, value: object) -> object:
        """Read older SQLite payloads without preserving retired semantics."""
        return {
            "configured": ProjectStatus.ZONES_SELECTED.value,
            "generated": ProjectStatus.EDITING.value,
            # Earlier builds modelled one-off validation and export as
            # project stages. A manual plan is validated continuously, while
            # an exported DXF is an artefact of that plan, so both legacy
            # states reopen as an editable project.
            "validated": ProjectStatus.EDITING.value,
            "checked": ProjectStatus.EDITING.value,
            "exported": ProjectStatus.EDITING.value,
        }.get(str(value), value)

    @model_validator(mode="after")
    def infer_map_readiness_for_existing_projects(self) -> "Project":
        # Existing SQLite records predate ``map_ready``. When their full map
        # snapshot is loaded, infer the flag once and preserve their usable
        # editor state rather than showing a false read-only preview.
        if self.geometry is not None:
            self.map_ready = True
        return self


class ProjectSummary(BaseModel):
    id: str
    name: str
    status: ProjectStatus
    source_name: str | None = None
    source_size: int | None = None
    has_geometry: bool = False
    planting_zone_count: int = Field(default=0, ge=0)
    plan_object_count: int = Field(default=0, ge=0)
    plan_version: int | None = Field(default=None, ge=1)
    import_status: ImportStatus = Field(default_factory=ImportStatus)
    state_version: int = Field(default=1, ge=1)
    created_at: str
    updated_at: str


class ProjectCreate(BaseModel):
    name: str = "Новый проект"


class LayerMappingRequest(BaseModel):
    mappings: list[LayerMapping]


class PlantingZonesRequest(BaseModel):
    zones: list[PlantingZoneAssignment] = Field(min_length=1, max_length=40)


class PlanObjectCreate(BaseModel):
    kind: Literal["tree", "shrub"]
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    radius: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    layout_radius_m: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    pattern_id: str | None = None
    group_ids: list[str] = Field(default_factory=list, max_length=50)
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"
    locked: bool = False


class PlanObjectUpdate(BaseModel):
    x: float | None = Field(default=None, allow_inf_nan=False)
    y: float | None = Field(default=None, allow_inf_nan=False)
    radius: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    layout_radius_m: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    size_class: Literal["unspecified", "sapling", "standard", "large"] | None = None
    species_revision_id: str | None = None
    pattern_id: str | None = None
    group_ids: list[str] | None = Field(default=None, max_length=50)
    spacing_policy: Literal["open", "balanced", "canopy"] | None = None
    locked: bool | None = None


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
    source: Literal["manual", "group", "pattern", "recommendation", "brush", "system"] = "manual"
    label: str = Field(min_length=1, max_length=160)
    policy: Literal["all_or_nothing"] = "all_or_nothing"
    operations: list[PlanChangeOperation] = Field(min_length=1, max_length=5000)


class ChangeSetCandidateResult(BaseModel):
    operation_index: int = Field(ge=0)
    type: Literal["add", "update", "delete"]
    status: Literal["allowed", "blocked", "soft_conflict", "unknown"]
    code: str
    category: Literal["accepted", "constraint", "growth", "data", "spacing", "operation"]
    reason: str
    object_id: str | None = None
    rule_id: str | None = None
    source_layer: str | None = None
    source_feature_ids: list[str] = Field(default_factory=list)
    actual_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    required_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    suggested_action: str | None = None
    zone_id: str | None = None


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
    layout_radius_m: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"

    @model_validator(mode="after")
    def validate_side_offset(self) -> "RowPatternRequest":
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
    layout: Literal["regular", "staggered", "natural"] = "staggered"
    spacing_m: float = Field(default=6, ge=0.5, le=100, allow_inf_nan=False)
    edge_offset_m: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    angle_deg: float = Field(default=0, ge=-180, le=180, allow_inf_nan=False)
    seed: int = Field(default=1, ge=0, le=2_147_483_647)
    layout_radius_m: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    tree_species_revision_id: str | None = None
    shrub_species_revision_id: str | None = None
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"

    @model_validator(mode="after")
    def normalize_composition(self) -> "FillPatternRequest":
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
    mask_id: Literal["road_edges", "regular_grid", "cluster_groves"]
    base_plan_version: int = Field(ge=1)
    plant_kind: Literal["tree", "shrub"] = "tree"
    composition: Literal["trees", "shrubs", "mixed"] | None = None
    tree_share: float = Field(default=0.65, ge=0, le=1, allow_inf_nan=False)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    placement_mode: Literal["count", "spacing"] = "count"
    target_count: int = Field(default=40, ge=1, le=5000)
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
    layout_radius_m: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    species_revision_id: str | None = None
    tree_species_revision_id: str | None = None
    shrub_species_revision_id: str | None = None
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"

    @model_validator(mode="after")
    def normalize_composition(self) -> "PlacementMaskRequest":
        if self.composition is None:
            self.composition = "shrubs" if self.plant_kind == "shrub" else "trees"
        if self.composition == "shrubs":
            self.plant_kind = "shrub"
        else:
            self.plant_kind = "tree"
        return self


PatternPreviewRequest = Annotated[RowPatternRequest | FillPatternRequest | PlacementMaskRequest, Field(discriminator="type")]


class PlacementMaskPreset(BaseModel):
    id: Literal["road_edges", "regular_grid", "cluster_groves"]
    title: str
    description: str
    available: bool = True
    unavailable_reason: str | None = None


class PatternSkippedCandidate(BaseModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    status: Literal["blocked", "soft_conflict", "unknown"] = "blocked"
    code: str = "PLACEMENT_BLOCKED"
    category: Literal["constraint", "growth", "data", "spacing", "operation"] = "constraint"
    reason: str
    rule_id: str | None = None
    source_layer: str | None = None
    source_feature_ids: list[str] = Field(default_factory=list)
    actual_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    required_distance_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    suggested_action: str | None = None
    zone_id: str | None = None


class CandidateReasonSummary(BaseModel):
    status: Literal["blocked", "soft_conflict", "unknown"]
    code: str
    category: Literal["constraint", "growth", "data", "spacing", "operation"]
    count: int = Field(ge=1)
    message: str


class PatternPreview(BaseModel):
    pattern_id: str
    type: Literal["row", "fill", "mask"]
    mask_id: Literal["road_edges", "regular_grid", "cluster_groves"] | None = None
    requested_count: int = Field(ge=0)
    generated_count: int = Field(default=0, ge=0)
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(default=0, ge=0)
    capacity_shortfall: int = Field(default=0, ge=0)
    effective_spacing_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
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


class BrushPreview(BaseModel):
    brush_id: str
    requested_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    added_count: int = Field(ge=0)
    removed_count: int = Field(ge=0)
    skipped: list[PatternSkippedCandidate] = Field(default_factory=list)
    reason_summary: list[CandidateReasonSummary] = Field(default_factory=list)
    change_set: ChangeSetPreview | None = None


class SpeciesRevision(BaseModel):
    id: str
    species_id: str
    revision: int = Field(ge=1)
    common_name: str
    scientific_name: str
    kind: Literal["tree", "shrub"]
    crown_shape: Literal["columnar", "conical", "oval", "round", "spreading", "irregular"]
    mature_height_min_m: float = Field(gt=0)
    mature_height_max_m: float = Field(gt=0)
    mature_crown_diameter_min_m: float = Field(gt=0)
    mature_crown_diameter_max_m: float = Field(gt=0)
    growth_rate: Literal["slow", "moderate", "fast"]
    root_architecture: Literal["shallow", "mixed", "deep", "uncertain"]
    provenance: Literal["native", "introduced", "cultivar", "not_assessed"]
    territory_policy: Literal["general_draft", "specialist_review"] = "general_draft"
    risk_flags: list[str] = Field(default_factory=list)
    evidence_note: str
    source_urls: list[str] = Field(min_length=1)
    canopy_forecast: list[GrowthEnvelopeForecast] = Field(default_factory=list)
    root_forecast: list[GrowthEnvelopeForecast] = Field(default_factory=list)


class SpeciesShortlistRequest(BaseModel):
    object_ids: list[str] = Field(default_factory=list, max_length=5000)
    zone_ids: list[str] = Field(default_factory=list, max_length=40)
    kind: Literal["tree", "shrub"] | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> "SpeciesShortlistRequest":
        if self.object_ids:
            return self
        if self.zone_ids:
            return self
        raise ValueError("Выберите посадки либо участки")


class SpeciesShortlistItem(BaseModel):
    species: SpeciesRevision
    status: Literal["available", "review"]
    selected_area_m2: float | None = Field(default=None, ge=0)
    estimated_safe_area_m2: float | None = Field(default=None, ge=0)
    estimated_capacity: int | None = Field(default=None, ge=0)
    estimated_mature_diameter_m: float | None = Field(default=None, gt=0)
    reasons: list[str] = Field(default_factory=list)


class RecommendationRequest(BaseModel):
    base_plan_version: int = Field(ge=1)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    profile: Literal["balanced", "shade", "continuity", "low_future_conflict"] = "balanced"
    max_sites: int = Field(default=80, ge=1, le=500)


class EvidenceAssessment(BaseModel):
    spatial_constraints: Literal["verified", "partial", "missing"]
    species_catalog: Literal["verified", "partial", "missing"]
    sunlight: Literal["verified", "partial", "missing"] = "missing"
    soil: Literal["verified", "partial", "missing"] = "missing"
    hydrology: Literal["verified", "partial", "missing"] = "missing"
    note: str


class EffectEstimate(BaseModel):
    effect: Literal["shade", "continuity", "stormwater", "comfort"]
    status: Literal["estimated", "unknown"]
    value: float | None = None
    unit: str | None = None
    reason: str


class RecommendationExplanation(BaseModel):
    object_id: str
    rank: int = Field(ge=1)
    hard_constraints: list[str] = Field(default_factory=list)
    biological_risks: list[str] = Field(default_factory=list)
    effects: list[EffectEstimate] = Field(default_factory=list)


class RecommendationPreview(BaseModel):
    profile: Literal["balanced", "shade", "continuity", "low_future_conflict"]
    evidence: EvidenceAssessment
    change_set: ChangeSetPreview | None = None
    explanations: list[RecommendationExplanation] = Field(default_factory=list)
    skipped: list[PatternSkippedCandidate] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)


class ScenePlantObject(BaseModel):
    object_id: str
    kind: Literal["tree", "shrub"]
    species_revision_id: str | None = None
    local_x: float = Field(allow_inf_nan=False)
    local_y: float = Field(allow_inf_nan=False)
    crown_shape: Literal["columnar", "conical", "oval", "round", "spreading", "irregular", "placeholder"]
    canopy_radius_min_m: float = Field(ge=0, allow_inf_nan=False)
    canopy_radius_max_m: float = Field(ge=0, allow_inf_nan=False)
    height_min_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    height_max_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    root_radius_min_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    root_radius_max_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    confidence: Literal["unknown", "low", "medium", "high"] = "unknown"


class SceneContextFeature(BaseModel):
    """A lightweight, honest DXF footprint used as the 3D ground reference."""

    feature_id: str
    kind: str
    geometry: dict[str, Any]
    label: str | None = None


class SceneSnapshot(BaseModel):
    plan_version: int = Field(ge=1)
    horizon_year: int = Field(ge=0, le=40)
    coordinate_origin: list[float] = Field(min_length=2, max_length=2)
    completeness: Literal["partial"] = "partial"
    terrain_status: Literal["missing"] = "missing"
    building_heights_status: Literal["missing"] = "missing"
    note: str
    data_gaps: list[str] = Field(default_factory=list)
    objects: list[ScenePlantObject] = Field(default_factory=list)
    context_features: list[SceneContextFeature] = Field(default_factory=list)


class PlanMutationResult(BaseModel):
    change_set_id: str
    plan_version: int = Field(ge=1)
    state_version: int = Field(ge=1)
    added_ids: list[str] = Field(default_factory=list)
    updated_ids: list[str] = Field(default_factory=list)
    deleted_ids: list[str] = Field(default_factory=list)
    affected_bounds: list[float] | None = None
    plan: Plan


class PlanObjectsDeleteRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=5000)


class PlanHistoryEntry(BaseModel):
    id: str
    ordinal: int = Field(ge=1)
    label: str
    created_at: str
    author: str = "Локальная сессия"
    applied: bool = True


class PlanHistoryState(BaseModel):
    can_undo: bool = False
    can_redo: bool = False
    undo_label: str | None = None
    redo_label: str | None = None
    entries: list[PlanHistoryEntry] = Field(default_factory=list)


class ExportArtifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    filename: str
    status: Literal["ready", "failed"]
    size: int
    download_url: str
    kind: Literal["dxf"] = "dxf"
    media_type: str = "application/dxf"


class RegulatoryReleaseBasis(BaseModel):
    """Human-confirmed process basis attached to a final project revision.

    This is deliberately not a claim that the service issued a permit.  It
    makes the PP-616/PP-1160 decision explicit, attributable and auditable
    before a package may be labelled final.
    """

    pp616_status: Literal["pending", "not_applicable", "documented"] = "pending"
    pp616_reference: str = Field(default="", max_length=240)
    pp1160_status: Literal["pending", "not_required", "documented"] = "pending"
    pp1160_reference: str = Field(default="", max_length=240)
    confirmed_by: str = Field(default="", max_length=160)


class ReleaseCreateRequest(BaseModel):
    mode: Literal["draft", "final"] = "draft"
    scene_horizon: int = Field(default=20, ge=0, le=40)
    regulatory_basis: RegulatoryReleaseBasis | None = None


class ReleaseArtifact(BaseModel):
    id: str
    filename: str
    kind: Literal["bundle", "dxf", "schedule", "manifest", "scene", "dendroplan"]
    media_type: str
    size: int = Field(ge=0)
    sha256: str
    download_url: str


class ReleasePackage(BaseModel):
    id: str
    project_id: str
    plan_version: int = Field(ge=1)
    geometry_version: int = Field(ge=0)
    mode: Literal["draft", "final"]
    status: Literal["draft", "ready"]
    created_at: str
    rule_set_revision: str
    species_catalog_revision: str
    scene_horizon: int = Field(ge=0, le=40)
    warnings: list[str] = Field(default_factory=list)
    artifacts: list[ReleaseArtifact] = Field(default_factory=list)


class ApiError(BaseModel):
    code: str
    message: str
    field_errors: dict[str, list[str]] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
