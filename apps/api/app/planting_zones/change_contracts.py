"""Geometry-free public contracts for saved planting-zone proposals."""

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.planting_zones.contracts import PlantingZonePreview
from app.species.assortment import TerritoryContext
from app.species.site_contracts import SiteConditions


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ZoneChangeDraft(_Closed):
    operation: Literal["create", "update", "delete"]
    base_state_version: int = Field(ge=1, strict=True)
    zone_id: str | None = Field(default=None, min_length=1, max_length=200)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    geometry: dict[str, Any] | None = None

    @field_validator("label")
    @classmethod
    def nonblank_label(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Название участка не может быть пустым")
        return value

    @field_validator("geometry")
    @classmethod
    def json_geometry(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            # Reject NaN/Infinity and non-JSON objects before any geometry is
            # inspected or a hash can claim to represent the supplied shape.
            json.dumps(value, allow_nan=False)
        return value

    @model_validator(mode="after")
    def operation_fields(self) -> "ZoneChangeDraft":
        if self.operation == "create":
            if self.zone_id is not None or self.label is None or self.geometry is None:
                raise ValueError(
                    "Создание требует название и контур; ID назначает сервер"
                )
        elif self.zone_id is None:
            raise ValueError("Укажите точный ID существующего участка")
        elif self.operation == "delete" and (
            self.label is not None or self.geometry is not None
        ):
            raise ValueError("Удаление не принимает новое название или контур")
        elif (
            self.operation == "update" and self.label is None and self.geometry is None
        ):
            raise ValueError("Укажите новое название или контур участка")
        return self


class ZoneSnapshot(_Closed):
    id: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=160)
    geometry: dict[str, Any]
    territory: TerritoryContext | None = None
    site_conditions: SiteConditions | None = None


class ZoneChangePreview(_Closed):
    id: str
    project_id: str
    draft: ZoneChangeDraft
    operation: Literal["create", "update", "delete"]
    target_zone_id: str
    base_state_version: int = Field(ge=1)
    base_geometry_version: int = Field(ge=0)
    base_plan_version: int | None = Field(default=None, ge=1)
    before_zones: tuple[ZoneSnapshot, ...] = Field(max_length=40)
    after_zones: tuple[ZoneSnapshot, ...] = Field(max_length=40)
    before_area_m2: float | None = Field(ge=0, allow_inf_nan=False)
    after_area_m2: float | None = Field(ge=0, allow_inf_nan=False)
    planting_digest: str = Field(pattern="^[0-9a-f]{64}$")
    can_apply: bool
    blockers: tuple[str, ...] = ()
    affected_planting_ids: tuple[str, ...] = ()
    preflight: PlantingZonePreview | None = None
    created_at: datetime
    expires_at: datetime
    digest: str = Field(pattern="^[0-9a-f]{64}$")


class ZoneChangeCommit(_Closed):
    preview_id: str = Field(min_length=1, max_length=200)
    digest: str = Field(pattern="^[0-9a-f]{64}$")
    base_state_version: int = Field(ge=1, strict=True)


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
