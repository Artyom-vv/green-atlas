from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.planning.change_contracts import ChangeSetPreview
from app.planning.pattern_contracts import PatternSkippedCandidate


class RecommendationRequest(BaseModel):
    base_plan_version: int = Field(ge=1)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    profile: Literal["balanced", "shade", "continuity", "low_future_conflict"] = (
        "balanced"
    )
    max_sites: int = Field(default=80, ge=1, le=500)


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


class RecommendationPreview(BaseModel):
    arrangement: Literal["area", "building_screen"] = "area"
    target_geometry: dict[str, Any] | None = None
    profile: Literal["balanced", "shade", "continuity", "low_future_conflict"]
    evidence: EvidenceAssessment
    change_set: ChangeSetPreview | None = None
    explanations: list[RecommendationExplanation] = Field(default_factory=list)
    skipped: list[PatternSkippedCandidate] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
