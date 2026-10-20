"""Неизменяемые квитанции применённых изменений рабочих зон посадки."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ZoneSnapshot(_Closed):
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=160)
    geometry: dict[str, Any]


class ZoneChangeResult(_Closed):
    status: Literal["applied"] = "applied"
    preview_id: str
    digest: str
    project_id: str
    operation: Literal["create", "update", "delete"]
    target_zone_id: str
    base_state_version: int
    state_version: int
    geometry_version: int
    plan_version: int | None = None
    after_zones: tuple[ZoneSnapshot, ...]
    plantings_unchanged: Literal[True] = True
