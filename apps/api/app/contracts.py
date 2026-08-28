from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
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


class PlanObject(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    kind: Literal["tree", "shrub"]
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)
    radius: float = Field(gt=0, le=25, allow_inf_nan=False)
    status: Literal["valid", "warning", "error"] = "valid"
    planting_zone_id: str | None = None


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


class PlanObjectUpdate(BaseModel):
    x: float | None = Field(default=None, allow_inf_nan=False)
    y: float | None = Field(default=None, allow_inf_nan=False)
    radius: float | None = Field(default=None, gt=0, le=25, allow_inf_nan=False)


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
