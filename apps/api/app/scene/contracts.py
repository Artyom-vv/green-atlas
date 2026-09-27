from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.geometry.contracts import CoordinateReference
from app.scene.types import (
    BaseElevationSource,
    EvidenceStatus,
    GeometrySource,
    GeoreferenceStatus,
    GrowthStage,
    HeightSource,
)


class ScenePlantObject(BaseModel):
    object_id: str
    kind: Literal["tree", "shrub"]
    species_revision_id: str | None = None
    species_id: str | None = None
    common_name: str | None = None
    scientific_name: str | None = None
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "unspecified"
    model_variant_key: str | None = None
    growth_stage: GrowthStage = "planting"
    growth_stage_status: EvidenceStatus = "missing"
    forecast_horizon_year: int = Field(default=0, ge=0, le=40)
    local_x: float = Field(allow_inf_nan=False)
    local_y: float = Field(allow_inf_nan=False)
    crown_shape: Literal[
        "columnar", "conical", "oval", "round", "spreading", "irregular", "placeholder"
    ]
    canopy_radius_min_m: float = Field(ge=0, allow_inf_nan=False)
    canopy_radius_max_m: float = Field(ge=0, allow_inf_nan=False)
    height_min_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    height_max_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    root_radius_min_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    root_radius_max_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    height_status: EvidenceStatus = "missing"
    confidence: Literal["unknown", "low", "medium", "high"] = "unknown"
    status: Literal["valid", "warning", "error"] = "valid"
    planting_zone_id: str | None = None
    pattern_id: str | None = None
    group_ids: list[str] = Field(default_factory=list)
    locked: bool = False


class SceneContextFeature(BaseModel):
    """A lightweight, honest DXF footprint used as the 3D ground reference."""

    feature_id: str
    kind: str
    geometry: dict[str, Any]
    label: str | None = None
    source_layer: str | None = None
    source_entity_type: str | None = None
    source_handle: str | None = None
    base_elevation_m: float | None = Field(default=None, allow_inf_nan=False)
    base_elevation_status: Literal["confirmed", "missing"] = "missing"
    base_elevation_source: BaseElevationSource | None = None
    height_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    height_status: EvidenceStatus | None = None
    height_source: HeightSource | None = None


class SceneVerticalPrimitive(BaseModel):
    """Source-proven XYZ translated only in XY into the scene frame."""

    primitive_id: str
    primitive_type: Literal["point", "polyline", "surface_mesh"]
    vertices: list[list[float]] = Field(default_factory=list)
    faces: list[list[int]] = Field(default_factory=list)
    source_layer: str
    source_entity_type: str
    source_handle: str | None = None
    source_file_units: str
    unit_scale_to_m: float = Field(gt=0, allow_inf_nan=False)
    source_space: Literal["dxf_wcs"] = "dxf_wcs"
    vertical_evidence: Literal[
        "explicit_xyz", "explicit_elevation", "explicit_extrusion"
    ]
    extrusion_vector_m: list[float] | None = Field(
        default=None, min_length=3, max_length=3
    )
    terrain_mapping_status: Literal["unmapped", "confirmed"] = "unmapped"
    terrain_mapping_basis: Literal["dxf_document_metadata"] | None = None
    terrain_confidence: Literal["surveyed", "estimated"] | None = None
    source_dataset: str | None = None
    source_url: str | None = None
    source_attribution: str | None = None
    vertical_datum: str | None = None
    vertical_datum_offset_m: float | None = Field(default=None, allow_inf_nan=False)


class SceneEvidence(BaseModel):
    """Provenance and coverage are separate from whether a value exists."""

    status: EvidenceStatus = "missing"
    coverage: Literal["full", "partial", "none"] = "none"
    source: str | None = None
    note: str


class SceneSnapshot(BaseModel):
    plan_version: int = Field(ge=1)
    horizon_year: int = Field(ge=0, le=40)
    coordinate_origin: list[float] = Field(min_length=2, max_length=2)
    coordinate_reference: CoordinateReference = Field(
        default_factory=CoordinateReference
    )
    georeference_status: GeoreferenceStatus = "missing"
    georeference_evidence: SceneEvidence | None = None
    geometry_source: GeometrySource = "missing"
    geometry_source_file_name: str | None = None
    completeness: Literal["partial"] = "partial"
    terrain_status: EvidenceStatus = "missing"
    terrain_elevation_m: float | None = Field(default=None, allow_inf_nan=False)
    terrain_evidence: SceneEvidence | None = None
    building_heights_status: EvidenceStatus = "missing"
    building_height_evidence: SceneEvidence | None = None
    building_feature_count: int = Field(default=0, ge=0)
    building_height_confirmed_count: int = Field(default=0, ge=0)
    note: str
    data_gaps: list[str] = Field(default_factory=list)
    objects: list[ScenePlantObject] = Field(default_factory=list)
    context_features: list[SceneContextFeature] = Field(default_factory=list)
    vertical_primitives: list[SceneVerticalPrimitive] = Field(default_factory=list)
