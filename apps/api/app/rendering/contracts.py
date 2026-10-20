"""Камера Three.js и версионированное локальное задание Blender."""

from math import isclose, sqrt
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, FiniteFloat, model_validator


class RenderCamera(BaseModel):
    position: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    quaternion: tuple[FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat]
    fov_y_deg: float = Field(gt=5, lt=150, allow_inf_nan=False)
    aspect: float = Field(gt=0.1, lt=10, allow_inf_nan=False)
    near: float = Field(gt=0, allow_inf_nan=False)
    far: float = Field(gt=0, le=100000, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_pose(self) -> "RenderCamera":
        if not isclose(sqrt(sum(v * v for v in self.quaternion)), 1, abs_tol=0.001):
            raise ValueError("Камера должна иметь единичный quaternion")
        if self.far <= self.near:
            raise ValueError("Дальняя плоскость камеры должна быть дальше ближней")
        return self


class SceneRenderRequest(BaseModel):
    request_id: UUID
    scene_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    horizon_year: int = Field(ge=0, le=40)
    camera: RenderCamera
    resolution: int = Field(default=1280, ge=512, le=2048)
    preparation_id: UUID | None = None
    preparation_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_preparation(self) -> "SceneRenderRequest":
        if (self.preparation_id is None) != (self.preparation_sha256 is None):
            raise ValueError("Укажите идентификатор и SHA подготовленной сцены вместе")
        return self


class SceneRenderPreparationRequest(BaseModel):
    request_id: UUID
    scene_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    horizon_year: int = Field(ge=0, le=40)
    camera: RenderCamera
    preview_scene: bool = False


class RenderPreviewMesh(BaseModel):
    id: str
    kind: str
    source: Literal["cad_building", "cad_surface", "map_building"]
    vertices: list[tuple[FiniteFloat, FiniteFloat, FiniteFloat]]
    faces: list[list[int]]
    height_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    height_status: Literal["confirmed", "estimated", "missing"] | None = None
    material_key: str | None = None
    surface_geometry: dict[str, Any] | None = None
    surface_elevation_m: FiniteFloat = 0

    @model_validator(mode="after")
    def valid_faces(self) -> "RenderPreviewMesh":
        if any(
            len(face) < 3
            or any(index < 0 or index >= len(self.vertices) for index in face)
            for face in self.faces
        ):
            raise ValueError("Неверный индекс грани подготовленной сцены")
        return self


class RenderPreviewCurb(BaseModel):
    id: str
    start_scene_xyz_m: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    end_scene_xyz_m: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    width_m: float = Field(gt=0, allow_inf_nan=False)
    height_m: float = Field(gt=0, allow_inf_nan=False)
    source_handle: str


class RenderPreviewTree(BaseModel):
    object_id: str
    local_x: FiniteFloat
    local_y: FiniteFloat
    base_elevation_m: FiniteFloat
    height_m: float = Field(gt=0, allow_inf_nan=False)
    radius_m: float = Field(gt=0, allow_inf_nan=False)
    rotation_z: FiniteFloat
    asset_key: str | None = None
    model_asset_id: str | None = None
    far_model_asset_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class RenderPreviewAssetBounds(BaseModel):
    min: tuple[FiniteFloat, FiniteFloat, FiniteFloat]
    max: tuple[FiniteFloat, FiniteFloat, FiniteFloat]


class RenderPreviewAsset(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9]{64}$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=64 * 1024 * 1024)
    mime_type: Literal["model/gltf-binary", "image/jpeg", "image/png"]
    kind: Literal["model", "texture"]
    url: str
    lod: Literal["near", "far"] | None = None
    bounds: RenderPreviewAssetBounds | None = None


class RenderPreviewMaterialMaps(BaseModel):
    diffuse: str
    roughness: str
    normal_gl: str


class RenderPreviewMaterial(BaseModel):
    kind: Literal["road", "sidewalk", "lawn"]
    tile_width_m: float = Field(gt=0, allow_inf_nan=False)
    normal_strength: float = Field(ge=0, allow_inf_nan=False)
    maps: RenderPreviewMaterialMaps


class RenderPreparationPreview(BaseModel):
    meshes: list[RenderPreviewMesh]
    curbs: list[RenderPreviewCurb]
    context_trees: list[RenderPreviewTree]
    suppressed_source_ids: list[str]
    assets: list[RenderPreviewAsset] = Field(default_factory=list)
    materials: list[RenderPreviewMaterial] = Field(default_factory=list)
    planned_plants: list[RenderPreviewTree] = Field(default_factory=list)
    asset_version: Literal[0, 1] = 0


class RenderPreparationSummary(BaseModel):
    message: str
    context_tree_count: int = Field(ge=0)
    map_building_count: int = Field(ge=0)
    map_status: str
    conditional_building_count: int = Field(ge=0)
    osm_building_count: int = Field(ge=0)
    terrain_status: str


class SceneRenderPreparation(BaseModel):
    id: UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    project_id: str
    scene_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    horizon_year: int = Field(ge=0, le=40)
    geometry_version: int = Field(ge=0)
    plan_version: int = Field(ge=1)
    camera: RenderCamera
    preview: RenderPreparationPreview
    summary: RenderPreparationSummary
    provenance: dict[str, Any] = Field(default_factory=dict)


class SceneRenderJob(BaseModel):
    id: UUID
    project_id: str
    status: Literal["queued", "rendering", "completed", "failed", "cancelled"]
    stage: str
    scene_sha256: str
    width: int
    height: int
    message: str | None = None
    image_url: str | None = None
    receipt_url: str | None = None
    preparation_id: UUID | None = None
    preparation_sha256: str | None = None


class SceneView(BaseModel):
    scene: dict[str, Any]
    environment: SceneRenderPreparation
