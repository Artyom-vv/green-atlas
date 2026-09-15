from __future__ import annotations

from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class PlantingZoneAssignment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    label: str = Field(min_length=1, max_length=160)
    geometry: dict[str, Any]


class PlantingZonesRequest(BaseModel):
    zones: list[PlantingZoneAssignment] = Field(min_length=1, max_length=40)


class PlantingZoneOverlap(BaseModel):
    zone_id: str
    label: str
    area_m2: float
    geometry: dict[str, Any]


class PlantingZonePreview(BaseModel):
    can_save: bool
    area_m2: float
    error: str | None = None
    overlaps: list[PlantingZoneOverlap] = Field(default_factory=list)
