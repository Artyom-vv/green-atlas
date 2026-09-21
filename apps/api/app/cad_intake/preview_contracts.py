"""Compact public inputs/results; CAD paths and entity links stay server-side."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.cad_intake.contracts import relative_path


class CadDrawingSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=2048)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    _relative_path = field_validator("path")(relative_path)


class CadBoundarySelection(CadDrawingSelection):
    handle: str = Field(pattern=r"^[A-Fa-f0-9]+$", max_length=32)


class CadPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intake_operation_id: str = Field(
        min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$"
    )
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: CadDrawingSelection
    boundary: CadBoundarySelection


class CadPreviewResult(BaseModel):
    status: Literal["requires_review"] = "requires_review"
    calculation_ready: Literal[False] = False
    published_state_version: int = Field(ge=1)
    geometry_version: int = Field(ge=0)
    source_name: str
    source_original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bytes: int = Field(ge=1)
    source_count: int = Field(ge=1)
    source_bytes_total: int = Field(ge=1)
    feature_count: int = Field(ge=0)
    output_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_bytes: int = Field(ge=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_units: int
    # Mask evidence is not a calculated Project site/planning/allowed area.
    boundary_mask_area_m2: float = Field(gt=0, allow_inf_nan=False)
    selected_entities: int = Field(ge=0)
    unknown_bounds: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list, max_length=50)
    warning_count: int = Field(ge=0)


class CadPreviewRecord(BaseModel):
    request: CadPreviewRequest
    result: CadPreviewResult | None = None
