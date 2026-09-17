"""Offline automatic composition, with an explicit placement scenario."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.planning.allocation import composition_targets
from app.planning.pattern_contracts import FillPatternRequest, PatternPreview
from app.planning.recommendation_contracts import QualifiedSpeciesOption
from app.species.assortment import TerritoryContext
from app.species.site_contracts import SiteConditions


class CompositionSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    placement: FillPatternRequest
    territory: TerritoryContext
    site_conditions: SiteConditions | None = None
    objective: Literal["balanced", "shade", "low_future_conflict"] = "balanced"

    @model_validator(mode="after")
    def require_automatic_mixture(self) -> Self:
        p = self.placement
        if p.composition != "mixed" or p.placement_mode != "count":
            raise ValueError("Automatic composition requires mixed/count placement")
        if not all(composition_targets(p.target_count, p.tree_share).values()):
            raise ValueError(
                "Automatic composition requires a nonzero quota for both kinds"
            )
        if any(
            (
                p.species_revision_id,
                p.tree_species_revision_id,
                p.shrub_species_revision_id,
            )
        ):
            raise ValueError("Automatic selection cannot accept preselected species")
        return self


class CompositionPairTrial(BaseModel):
    tree_species_revision_id: str
    shrub_species_revision_id: str
    trees: int
    shrubs: int
    minimum_quota_fraction: float
    crown_projection_sum_m2: float
    maximum_crown_radius_m: float


class CompositionSelectionResult(BaseModel):
    species_options: list[QualifiedSpeciesOption]
    pairs: list[CompositionPairTrial] = Field(default_factory=list)
    selected_pair_index: int | None = None
    preview: PatternPreview | None = None
    assortment_revision: str
    site_evidence_revision: str
    selection_reason: str
    data_gaps: list[str] = Field(default_factory=list)
