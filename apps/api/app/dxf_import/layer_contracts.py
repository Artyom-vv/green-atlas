from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.geometry.axis_contracts import UtilityAxisBinding
from app.geometry.utility_contracts import UtilityContext


class LayerKind(StrEnum):
    SITE_BORDER = "site_border"
    BUILDING = "building"
    ROAD = "road"
    UTILITY = "utility"
    EXISTING_GREEN = "existing_green"
    WATER = "water"
    RESTRICTED = "restricted"
    IGNORE = "ignore"


class Layer(BaseModel):
    id: str
    source_name: str
    suggested_kind: LayerKind
    mapped_kind: LayerKind | None = None
    object_count: int
    color: str
    linetype: str = "CONTINUOUS"
    lineweight_mm: float | None = None
    entity_types: dict[str, int] = Field(default_factory=dict)
    # A saturated import can retain only a bounded map representation. This
    # distinguishes a safely complete layer from a preview that must not be
    # used as a planting constraint.
    geometry_complete: bool = True
    required: bool = False
    visible: bool = True
    utility_context: UtilityContext | None = None
    utility_axis_bindings: list[UtilityAxisBinding] = Field(default_factory=list)


class LayerMapping(BaseModel):
    layer_id: str
    kind: LayerKind
    visible: bool = True
    utility_context: UtilityContext | None = None
    utility_axis_bindings: list[UtilityAxisBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_utility_layer(self) -> Self:
        if (
            self.utility_context is not None or self.utility_axis_bindings
        ) and self.kind != LayerKind.UTILITY:
            raise ValueError(
                "Параметры сети допустимы только для слоя инженерных сетей"
            )
        return self


class LayerMappingRequest(BaseModel):
    mappings: list[LayerMapping]
