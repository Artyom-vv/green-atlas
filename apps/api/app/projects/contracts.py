from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.dxf_import.contracts import ImportStatus, SourceFile
from app.dxf_import.layer_contracts import Layer
from app.dxf_import.review_contracts import SourceReview
from app.geometry.contracts import CoordinateReference, GeometrySnapshot
from app.planning.contracts import Plan
from app.planting_zones.contracts import PlantingZoneAssignment


class ProjectStatus(StrEnum):
    EMPTY = "empty"
    IMPORTED = "imported"
    MAPPED = "mapped"
    ZONES_SELECTED = "zones_selected"
    EDITING = "editing"


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    status: ProjectStatus = ProjectStatus.EMPTY
    source_file: SourceFile | None = None
    # The source DXF remains the immutable CAD input.  ``import_status``
    # tells clients whether the current payload also carries the release
    # semantics required for another editable revision.
    import_status: ImportStatus = Field(default_factory=ImportStatus)
    # An editable source draft is not evidence of completed constraint analysis.
    # Kept outside the map payload so lightweight HTTP reads retain this state.
    source_review: SourceReview | None = None
    layers: list[Layer] = Field(default_factory=list)
    coordinate_reference: CoordinateReference = Field(
        default_factory=CoordinateReference
    )
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
    def infer_map_readiness_for_existing_projects(self) -> Project:
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
