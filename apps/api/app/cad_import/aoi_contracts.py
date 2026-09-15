"""Explicit source, boundary and influence provenance for a preview workpackage."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AoiSource(BaseModel):
    original_path: Path
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    converted_path: Path
    converted_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_path: Path | None = None


class InfluenceScope(BaseModel):
    radius_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    provenance: list[str] = Field(default_factory=list)
    rules_version: str | None = None
    verified: bool = False

    @model_validator(mode="after")
    def validate_verified_scope(self) -> "InfluenceScope":
        if self.verified and (
            self.radius_m is None or not self.provenance or not self.rules_version
        ):
            raise ValueError(
                "Подтверждённая маска требует расстояния и происхождения правил"
            )
        return self


class AoiRequest(BaseModel):
    source: AoiSource
    boundary_source: AoiSource
    boundary_handle: str
    influence: InfluenceScope = Field(default_factory=InfluenceScope)


class AoiEntityLink(BaseModel):
    source_sha256: str
    source_handle: str
    output_handle: str
    entity_type: str
    insert_chain: list[str] = Field(default_factory=list)
    world_transform: list[float] = Field(default_factory=list)
    provenance_appid: str | None = None
    exported_in_dxf: bool = True


class AoiBoundaryEvidence(BaseModel):
    method: Literal["straight_segments", "bulge_arc_sagitta"]
    sagitta_tolerance_m: float
    outward_margin_m: float
    original_vertices: int
    mask_vertices: int
    vertex_budget: int


class AoiStyleRepair(BaseModel):
    policy: Literal["canonical_raw_dimstyle_aci_v1"] = "canonical_raw_dimstyle_aci_v1"
    source_sha256: str
    source_handle: str
    source_name: str
    output_handle: str
    output_name: str
    group: int
    original_value: int
    normalized_value: int


class AoiAnnotationRepair(BaseModel):
    policy: Literal["inactive_mleader_attachment_v1"] = "inactive_mleader_attachment_v1"
    compatibility_only: Literal[True] = True
    source_sha256: str
    source_handle: str
    output_handle: str
    insert_chain: list[str]
    group: Literal[273] = 273
    original_value: int
    normalized_value: int
    global_attachment_direction: Literal[0] = 0
    leader_attachment_directions: list[int]
    property_override_flags: int


class AoiExportOmission(BaseModel):
    reason: Literal["source_acis_payload_missing"] = "source_acis_payload_missing"
    evidence_scope: Literal["converted_dxf"] = "converted_dxf"
    source: AoiEntityLink


class AoiManifest(BaseModel):
    schema_version: Literal["green-atlas-aoi-v1"] = "green-atlas-aoi-v1"
    status: Literal["preview_only"] = "preview_only"
    calculation_ready: Literal[False] = False
    derivation: Literal["preserved_instances", "world_space_leaves"] = (
        "preserved_instances"
    )
    selection: Literal["conservative_bounds_intersection"] = (
        "conservative_bounds_intersection"
    )
    request: AoiRequest
    output_sha256: str
    output_bytes: int
    output_format: Literal["ascii_dxf", "binary_dxf"] = "ascii_dxf"
    source_units: int
    boundary_area_m2: float
    boundary_approximation: AoiBoundaryEvidence | None = None
    selected_modelspace_entities: int
    excluded_by_bounds: int
    unknown_bounds: int
    source_links: list[AoiEntityLink]
    warnings: list[str]
    stage_seconds: dict[str, float] = Field(default_factory=dict)
    stage_rss_bytes: dict[str, int] = Field(default_factory=dict)
    selection_metrics: dict[str, int] = Field(default_factory=dict)
    diagnostics: list[str] = Field(default_factory=list)
    style_repairs: list[AoiStyleRepair] = Field(default_factory=list)
    annotation_repairs: list[AoiAnnotationRepair] = Field(default_factory=list)
    export_omissions: list[AoiExportOmission] = Field(default_factory=list)


class PreparedAoi(BaseModel):
    drawing_path: Path
    manifest_path: Path
    manifest: AoiManifest
    elapsed_seconds: float
    peak_memory_bytes: int
