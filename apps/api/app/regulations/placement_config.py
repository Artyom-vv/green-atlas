"""Versioned distances, with normative rows separate from project assumptions."""

from hashlib import sha256
from importlib.resources import files

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(StrictConfig):
    document: str
    edition: str
    clause: str
    url: str
    scope: str


class Setback(StrictConfig):
    id: str
    source: str
    label: str
    tree_m: float = Field(ge=0, allow_inf_nan=False)
    shrub_m: float | None = Field(ge=0, allow_inf_nan=False)

    def distance(self, kind: str) -> float | None:
        if kind not in {"tree", "shrub"}:
            raise ValueError("Неизвестный тип посадки")
        return self.tree_m if kind == "tree" else self.shrub_m


class WorkingGeometry(StrictConfig):
    unknown_network_setback_m: float = Field(gt=0, allow_inf_nan=False)
    unresolved_review_horizon_m: float = Field(gt=0, allow_inf_nan=False)
    basis: str
    base_tree_crown_diameter_m: float = Field(gt=0, allow_inf_nan=False)


class Technical(StrictConfig):
    distance_tolerance_m: float = Field(gt=0, allow_inf_nan=False)
    cell_certificate_margin_m: float = Field(gt=0, allow_inf_nan=False)


class HybridSearchConfig(StrictConfig):
    basis: str
    batch_objects: int = Field(gt=0)
    yield_seconds: float = Field(gt=0, allow_inf_nan=False)
    cached_domains: int = Field(gt=0)
    cached_masks: int = Field(gt=0)
    buffer_quadrant_segments: int = Field(ge=4)
    projection_area_relative_tolerance: float = Field(gt=0, allow_inf_nan=False)
    projection_area_absolute_tolerance_m2: float = Field(gt=0, allow_inf_nan=False)


class CandidateSearchConfig(StrictConfig):
    basis: str
    minimum_probes: int = Field(gt=0)
    maximum_probes: int = Field(gt=0)
    probes_per_requested_plant: int = Field(gt=0)
    batch_size: int = Field(gt=0)
    minimum_batch_size: int = Field(gt=0)
    probes_per_missing_plant: int = Field(gt=0)
    time_limit_s: float = Field(gt=0, allow_inf_nan=False)
    minimum_grid_cell_m: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_limits(self):
        if self.minimum_probes > self.maximum_probes or self.minimum_batch_size > self.batch_size:
            raise ValueError("Минимальный размер поиска превышает максимальный")
        return self


class Spacing(StrictConfig):
    minimum_m: float = Field(ge=0, allow_inf_nan=False)
    crown_multiplier: float = Field(gt=0, allow_inf_nan=False)
    extra_m: float = Field(ge=0, allow_inf_nan=False)

    def distance(self, radii_sum: float) -> float:
        return max(self.minimum_m, radii_sum * self.crown_multiplier + self.extra_m)


class Layout(StrictConfig):
    basis: str
    growth_horizon_year: int = Field(gt=0)
    tree_radius_m: float = Field(gt=0, allow_inf_nan=False)
    shrub_radius_m: float = Field(gt=0, allow_inf_nan=False)
    tree_canopy: Spacing
    tree_balanced: Spacing
    tree_open: Spacing
    shrub: Spacing
    mixed: Spacing
    road_edge_extra_m: float = Field(ge=0, allow_inf_nan=False)
    maximum_road_offset_m: float = Field(gt=0, allow_inf_nan=False)

    def maximum_spacing(self, radii_sum: float) -> float:
        return max(rule.distance(radii_sum) for rule in (
            self.tree_canopy, self.tree_balanced, self.tree_open, self.shrub, self.mixed,
        ))


class PlacementConfig(StrictConfig):
    revision: str
    sources: dict[str, Source]
    setbacks: dict[str, Setback]
    working_geometry: WorkingGeometry
    technical: Technical
    layout: Layout
    hybrid_search: HybridSearchConfig
    candidate_search: CandidateSearchConfig

    @model_validator(mode="after")
    def require_sources(self):
        if any(row.source not in self.sources for row in self.setbacks.values()):
            raise ValueError("У отступа отсутствует нормативный источник")
        if len({row.id for row in self.setbacks.values()}) != len(self.setbacks):
            raise ValueError("Повторный идентификатор правила")
        return self


_content = files(__package__).joinpath("placement_rules.json").read_bytes()
PLACEMENT_CONFIG = PlacementConfig.model_validate_json(_content)
PLACEMENT_RULES_REVISION = f"{PLACEMENT_CONFIG.revision}:{sha256(_content).hexdigest()}"
MAX_SETBACK_M = max(
    PLACEMENT_CONFIG.working_geometry.unknown_network_setback_m,
    *(value for rule in PLACEMENT_CONFIG.setbacks.values()
      for value in (rule.tree_m, rule.shrub_m) if value is not None),
)
