"""Calculation representations, distinct from import and map feature counts."""

from pydantic import BaseModel, Field


class GeometryCoverageIssue(BaseModel):
    routes: list[str] = Field(default_factory=list)
    reason: str
    detail: str


class LayerGeometryCoverage(BaseModel):
    area_count: int = Field(default=0, ge=0)
    linear_count: int = Field(default=0, ge=0)
    point_count: int = Field(default=0, ge=0)
    context_count: int = Field(default=0, ge=0)
    unresolved: list[GeometryCoverageIssue] = Field(default_factory=list)

    @property
    def represented(self):
        return bool(self.area_count or self.linear_count or self.point_count)


class GeometryCoverage(BaseModel):
    source_sha256: str
    layers: dict[str, LayerGeometryCoverage]
