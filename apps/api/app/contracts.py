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
    IGNORE = "ignore"


class SourceFile(BaseModel):
    name: str
    size: int
    imported_at: str
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

    horizon_year: Literal[5, 10, 20]
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
    status: Literal["allowed", "blocked", "unknown"]
    reason: str
    object_id: str | None = None


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
    axis: dict[str, Any]
    spacing_m: float = Field(default=6, ge=0.5, le=100, allow_inf_nan=False)
    start_offset_m: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    end_offset_m: float = Field(default=0, ge=0, le=1000, allow_inf_nan=False)
    side: Literal["center", "left", "right", "both"] = "center"
    lateral_offset_m: float = Field(default=0, ge=0, le=100, allow_inf_nan=False)
    layout_radius_m: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"

    @model_validator(mode="after")
    def validate_side_offset(self) -> "RowPatternRequest":
        if self.side != "center" and self.lateral_offset_m <= 0:
            raise ValueError("Для бокового ряда укажите поперечный отступ")
        return self


class FillPatternRequest(BaseModel):
    type: Literal["fill"] = "fill"
    base_plan_version: int = Field(ge=1)
    plant_kind: Literal["tree", "shrub"] = "tree"
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    layout: Literal["regular", "staggered", "natural"] = "staggered"
    spacing_m: float = Field(default=6, ge=0.5, le=100, allow_inf_nan=False)
    edge_offset_m: float = Field(default=1, ge=0, le=100, allow_inf_nan=False)
    angle_deg: float = Field(default=0, ge=-180, le=180, allow_inf_nan=False)
    seed: int = Field(default=1, ge=0, le=2_147_483_647)
    layout_radius_m: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"


PatternPreviewRequest = Annotated[RowPatternRequest | FillPatternRequest, Field(discriminator="type")]


class PatternSkippedCandidate(BaseModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    reason: str


class PatternPreview(BaseModel):
    pattern_id: str
    type: Literal["row", "fill"]
    requested_count: int = Field(ge=0)
    accepted_count: int = Field(ge=0)
    skipped: list[PatternSkippedCandidate] = Field(default_factory=list)
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


class SpeciesShortlistRequest(BaseModel):
    object_ids: list[str] = Field(min_length=1, max_length=5000)


class SpeciesShortlistItem(BaseModel):
    species: SpeciesRevision
    status: Literal["available", "review"]
    reasons: list[str] = Field(default_factory=list)


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


class PlanHistoryState(BaseModel):
    can_undo: bool = False
    can_redo: bool = False
    undo_label: str | None = None
    redo_label: str | None = None


class ExportArtifact(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    filename: str
    status: Literal["ready", "failed"]
    size: int
    download_url: str
    kind: Literal["dxf"] = "dxf"
    media_type: str = "application/dxf"


class ApiError(BaseModel):
    code: str
    message: str
    field_errors: dict[str, list[str]] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
