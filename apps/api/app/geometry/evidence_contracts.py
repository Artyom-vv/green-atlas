"""Read-only, object-addressed explanations; never a placement permission."""

from typing import Literal

from pydantic import BaseModel, Field


class GeometryEvidenceItem(BaseModel):
    code: str
    stage: Literal["mapping", "inventory", "query", "area", "rule", "clearance", "site"]
    outcome: Literal["excluded", "unknown", "boundary"]
    message: str
    action: str
    source_layer: str | None = None
    source_feature_ids: list[str] = Field(default_factory=list)
    measured_distance_m: float | None = None
    required_distance_m: float | None = None
    requirement_basis: Literal["rule", "canopy", "roots"] | None = None
    native_status: int | None = None
    native_error: str | None = None
    query_sent: bool = False


class GeometryEvidence(BaseModel):
    state: Literal["available", "excluded", "unknown", "boundary"]
    radius_m: float
    canopy_radius_m: float
    root_radius_m: float
    considered_objects: int
    unlocated_objects: int
    causes: list[GeometryEvidenceItem] = Field(default_factory=list)
