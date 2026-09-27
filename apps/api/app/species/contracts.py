from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class GrowthEnvelopeForecast(BaseModel):
    """A bounded biological forecast, never a regulatory exclusion zone."""

    horizon_year: int = Field(ge=0, le=40)
    radius_min_m: float = Field(ge=0, allow_inf_nan=False)
    radius_max_m: float = Field(ge=0, allow_inf_nan=False)
    confidence: Literal["low", "medium", "high"]
    basis: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def validate_range(self) -> GrowthEnvelopeForecast:
        if self.radius_max_m < self.radius_min_m:
            raise ValueError("Верхняя граница прогноза должна быть не меньше нижней")
        return self


class SpeciesRevision(BaseModel):
    id: str
    species_id: str
    revision: int = Field(ge=1)
    common_name: str
    scientific_name: str
    kind: Literal["tree", "shrub"]
    crown_shape: Literal[
        "columnar", "conical", "oval", "round", "spreading", "irregular"
    ]
    mature_height_min_m: float = Field(gt=0)
    mature_height_max_m: float = Field(gt=0)
    mature_crown_diameter_min_m: float = Field(gt=0)
    mature_crown_diameter_max_m: float = Field(gt=0)
    growth_rate: Literal["slow", "moderate", "fast"]
    root_architecture: Literal["shallow", "mixed", "deep", "uncertain"]
    provenance: Literal["native", "introduced", "cultivar", "not_assessed"]
    territory_policy: Literal["general_draft", "specialist_review"] = "general_draft"
    risk_flags: list[str] = Field(default_factory=list)
    evidence_note: str
    source_urls: list[str] = Field(min_length=1)
    canopy_forecast: list[GrowthEnvelopeForecast] = Field(default_factory=list)
    root_forecast: list[GrowthEnvelopeForecast] = Field(default_factory=list)


class SpeciesShortlistRequest(BaseModel):
    object_ids: list[str] = Field(default_factory=list, max_length=5000)
    zone_ids: list[str] = Field(default_factory=list, max_length=40)
    kind: Literal["tree", "shrub"] | None = None

    @model_validator(mode="after")
    def validate_scope(self) -> SpeciesShortlistRequest:
        if self.object_ids:
            return self
        if self.zone_ids:
            return self
        raise ValueError("Выберите посадки либо участки")


class SpeciesShortlistItem(BaseModel):
    species: SpeciesRevision
    status: Literal["available", "review"]
    selected_area_m2: float | None = Field(default=None, ge=0)
    estimated_safe_area_m2: float | None = Field(default=None, ge=0)
    estimated_capacity: int | None = Field(default=None, ge=0)
    estimated_mature_diameter_m: float | None = Field(default=None, gt=0)
    reasons: list[str] = Field(default_factory=list)
