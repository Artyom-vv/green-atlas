"""Authored model-space candidates, not certified calculation boundaries."""

from typing import Literal

from pydantic import BaseModel, Field


class DrawingBoundaryCandidate(BaseModel):
    handle: str
    layer: str
    entity_type: Literal["LWPOLYLINE"] = "LWPOLYLINE"
    vertex_count: int = Field(ge=0)
    has_bulges: bool
    bounds_m: tuple[float, float, float, float] | None = None
    available_for_preview: bool
    reason: str | None = None


class DrawingBoundaryCatalog(BaseModel):
    schema_version: Literal["green-atlas-boundary-catalog-v1"] = (
        "green-atlas-boundary-catalog-v1"
    )
    candidates: list[DrawingBoundaryCandidate] = Field(default_factory=list)
    total_candidates: int = Field(ge=0)
    truncated: bool = False
