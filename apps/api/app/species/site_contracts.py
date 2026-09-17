"""Explicit site observations and bounded, source-backed compatibility evidence."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LightCondition = Literal["full_sun", "partial_shade", "full_shade"]
MoistureCondition = Literal[
    "moist", "occasionally_dry", "occasionally_wet", "persistently_wet"
]
DrainageCondition = Literal["well_drained", "poorly_drained"]
SiteDimension = Literal["light", "moisture", "drainage"]
SiteCheckStatus = Literal[
    "not_provided", "documented_match", "documented_conflict", "unknown"
]


class SiteConditions(BaseModel):
    """One homogeneous observation context for all zones in this request.

    Unknown dimensions stay null; drainage and moisture are independent.
    Separate requests are necessary for zones with different conditions.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    light: LightCondition | None = None
    moisture: MoistureCondition | None = None
    drainage: DrainageCondition | None = None
    basis: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def require_observation(self) -> "SiteConditions":
        if self.light is None and self.moisture is None and self.drainage is None:
            raise ValueError("Укажите хотя бы одно известное условие участка")
        return self


class SiteSpeciesProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species_id: str
    source_url: str
    checked_on: str
    light: list[LightCondition]
    moisture: list[MoistureCondition]
    drainage: list[DrainageCondition]
    avoid_light: list[LightCondition] = Field(default_factory=list)
    avoid_moisture: list[MoistureCondition] = Field(default_factory=list)
    avoid_drainage: list[DrainageCondition] = Field(default_factory=list)
    notes: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def disjoint_evidence(self) -> "SiteSpeciesProfile":
        for dimension in ("light", "moisture", "drainage"):
            if set(getattr(self, dimension)) & set(getattr(self, f"avoid_{dimension}")):
                raise ValueError(f"Conflicting evidence for {dimension}")
        return self


class SiteConditionCheck(BaseModel):
    dimension: SiteDimension
    value: str | None = None
    status: SiteCheckStatus
    reason: str


class SiteSuitability(BaseModel):
    status: Literal["documented_match", "documented_conflict", "unknown"]
    checks: list[SiteConditionCheck]
    source_url: str | None = None
    checked_on: str | None = None
    notes: list[str] = Field(default_factory=list)


class SiteProfileInventory(BaseModel):
    revision: str
    profiles: list[SiteSpeciesProfile]

    @model_validator(mode="after")
    def unique_species(self) -> "SiteProfileInventory":
        ids = [profile.species_id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate species site profile")
        return self
