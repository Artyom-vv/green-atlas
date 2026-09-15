"""Geometry-free typed intent and tool slots for planting-zone actions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_runtime.history_budget import MAX_HISTORY_CHARS, MAX_HISTORY_TURNS
from app.planting_zones.change_contracts import ZoneChangeDraft


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ZoneGeometryReference(_Closed):
    project_id: str = Field(min_length=1)
    state_version: int = Field(ge=1, strict=True)
    geometry_version: int = Field(ge=0, strict=True)
    feature_id: str = Field(min_length=1, max_length=200)
    geometry_digest: str = Field(pattern="^[0-9a-f]{64}$")


class ZoneIntent(_Closed):
    operation: Literal["create", "update", "delete"]
    target_zone_id: str | None = Field(default=None, min_length=1, max_length=200)
    label: str | None = Field(default=None, min_length=1, max_length=160)
    geometry_reference: ZoneGeometryReference | None = None

    @model_validator(mode="after")
    def action_payload(self):
        if self.operation == "create" and (
            self.target_zone_id is not None or self.geometry_reference is None
        ):
            raise ValueError(
                "Создание требует ссылку на контур проекта; ID нового участка задаёт сервер"
            )
        if self.operation == "delete" and (
            self.label is not None or self.geometry_reference is not None
        ):
            raise ValueError("Удаление не принимает новое название или геометрию")
        if (
            self.operation == "update"
            and self.label is None
            and self.geometry_reference is None
        ):
            raise ValueError("Укажите новое название или ссылку на новый контур")
        return self


class ZoneSourceAmendment(_Closed):
    """A source message supplies one typed slot; earlier slots stay intact."""

    turn_index: int = Field(ge=0, strict=True)
    slot: Literal["target_zone_id", "label", "geometry_reference"]
    value: str | ZoneGeometryReference


class BoundZoneIntent(_Closed):
    source_text: str = Field(min_length=1, max_length=MAX_HISTORY_CHARS)
    source_turns: list[str] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)
    amendments: list[ZoneSourceAmendment] = Field(
        default_factory=list, max_length=MAX_HISTORY_TURNS * 3
    )
    project_id: str
    intent: ZoneIntent
    draft: ZoneChangeDraft
    base_geometry_version: int = Field(ge=0)
    base_plan_version: int | None = Field(default=None, ge=1)
    before_zones_digest: str = Field(pattern="^[0-9a-f]{64}$")
    planting_digest: str = Field(pattern="^[0-9a-f]{64}$")


class ZonePrepareRequest(_Closed):
    """Only source-backed slots cross the tool boundary, never model polygons."""

    source_text: str = Field(min_length=1, max_length=MAX_HISTORY_CHARS)
    source_turns: list[str] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)
    intent: ZoneIntent
    base_state_version: int = Field(ge=1, strict=True)
