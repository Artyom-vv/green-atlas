from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BuildingScreenRequest(BaseModel):
    base_plan_version: int = Field(ge=1)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    screen_side: Literal["perimeter", "roads"] = "perimeter"
    max_sites: int | None = Field(default=None, ge=1, le=500)
    arrangement: Literal["groves", "contour"] = "groves"
    species_revision_id: str = "tilia-cordata@2026-08-28.1"
    size_class: Literal["unspecified", "sapling", "standard", "large"] = "standard"
    spacing_m: float = Field(default=7, ge=0.5, le=100, allow_inf_nan=False)
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"
    building_offset_m: float | None = Field(
        default=None, ge=0.5, le=60, allow_inf_nan=False
    )


class BuildingScreenTargets(BaseModel):
    geometry: dict[str, Any] | None = None
    has_roads: bool = False
