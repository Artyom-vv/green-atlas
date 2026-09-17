from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.planning.change_contracts import ChangeSetPreview
from app.planning.pattern_contracts import PatternSkippedCandidate
from app.species.assortment import AssortmentStatus, TerritoryContext
from app.species.site_contracts import SiteConditions, SiteSuitability


class RecommendationRequest(BaseModel):
    base_plan_version: int = Field(ge=1)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    profile: Literal["balanced", "shade", "continuity", "low_future_conflict"] = (
        "balanced"
    )
    max_sites: int = Field(default=80, ge=1, le=500)
    territory: TerritoryContext | None = None
    plant_kind: Literal["tree", "shrub"] | None = None
    site_conditions: SiteConditions | None = Field(
        default=None,
        description="Явно заданные одинаковые условия всех выбранных участков. Для разных условий нужны отдельные запросы; null означает отсутствие данных.",
    )

    @property
    def effective_plant_kind(self) -> Literal["tree", "shrub"]:
        return self.plant_kind or "tree"

    @model_validator(mode="after")
    def require_shrub_context(self) -> RecommendationRequest:
        if self.plant_kind == "shrub" and self.territory is None:
            raise ValueError("Для подбора кустарников укажите контекст территории")
        if self.site_conditions is not None and self.territory is None:
            raise ValueError(
                "Для подбора по условиям участка укажите контекст территории"
            )
        return self


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


class RecommendationSpeciesOption(BaseModel):
    species_revision_id: str
    assortment_status: AssortmentStatus
    accepted_count: int = 0
    crown_projection_sum_m2: float = 0
    source_url: str | None = None
    source_page: int | None = None
    source_row: int | None = None
    source_row_id: str | None = None
    source_tier: Literal["main", "additional"] | None = None
    notes: list[str] = Field(default_factory=list)
    site_suitability: SiteSuitability | None = None


class RecommendationPreview(BaseModel):
    arrangement: Literal["area", "building_screen"] = "area"
    target_geometry: dict[str, Any] | None = None
    profile: Literal["balanced", "shade", "continuity", "low_future_conflict"]
    evidence: EvidenceAssessment
    change_set: ChangeSetPreview | None = None
    explanations: list[RecommendationExplanation] = Field(default_factory=list)
    skipped: list[PatternSkippedCandidate] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    species_options: list[RecommendationSpeciesOption] = Field(default_factory=list)
    selection_reason: str | None = None
    assortment_revision: str | None = None
    site_conditions: SiteConditions | None = None
    site_evidence_revision: str | None = None
