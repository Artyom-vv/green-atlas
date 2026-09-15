from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CoordinateReference(BaseModel):
    status: Literal["unknown", "local", "declared", "verified"] = "unknown"
    crs_id: str | None = None
    name: str | None = None
    source: Literal[
        "none",
        "dxf_geodata",
        "dxf_custom_georeference",
        "user_declared",
        "control_points",
    ] = "none"
    axis_order: Literal["xy", "yx"] | None = None
    origin_wgs84: list[float] | None = Field(default=None, min_length=2, max_length=2)
    local_projection: Literal["local_equirectangular_wgs84"] | None = None
    earth_radius_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    horizontal_source: str | None = None
    control_points_count: int = Field(default=0, ge=0)
    evidence: str = "Система координат не указана"
    updated_at: str | None = None


class GeometrySnapshot(BaseModel):
    feature_collection: dict[str, Any]
    # Metric XYZ evidence is deliberately stored beside, rather than inside,
    # the 2D GeoJSON projection. GeoJSON consumers may continue to reason in
    # XY while the scene can reproduce genuine CAD elevations and surfaces.
    vertical_primitives: list[DxfVerticalPrimitive] = Field(default_factory=list)
    site_area_m2: float | None = Field(default=None, ge=0)
    planning_area_m2: float | None = Field(default=None, ge=0)
    allowed_area_m2: float | None = Field(default=None, ge=0)


class DxfVerticalPrimitive(BaseModel):
    primitive_id: str
    primitive_type: Literal["point", "polyline", "surface_mesh"]
    vertices_m: list[list[float]] = Field(default_factory=list)
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


GeometrySnapshot.model_rebuild()
