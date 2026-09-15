from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator


class UtilityType(StrEnum):
    UNKNOWN = "unknown"
    GAS = "gas"
    SEWER = "sewer"
    HEAT = "heat"
    WATER = "water"
    DRAINAGE = "drainage"
    POWER_CABLE = "power_cable"
    COMMUNICATION_CABLE = "communication_cable"


class UtilityGeometryReference(StrEnum):
    UNKNOWN = "unknown"
    AXIS = "axis"
    OUTER_SURFACE = "outer_surface"
    CHANNEL_WALL = "channel_wall"
    PROTECTIVE_CASING = "protective_casing"


class UtilityContext(BaseModel):
    """Explicit source interpretation; a layer-name guess is not confirmation."""

    network_type: UtilityType = UtilityType.UNKNOWN
    geometry_reference: UtilityGeometryReference = UtilityGeometryReference.UNKNOWN
    installation: Literal["unknown", "underground", "aboveground"] = "unknown"
    review_status: Literal["unconfirmed", "confirmed"] = "unconfirmed"
    source_reference: str = Field(default="", max_length=500)
    confirmed_by: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def require_confirmation_basis(self) -> Self:
        if self.review_status == "confirmed" and (
            self.network_type == UtilityType.UNKNOWN
            or self.geometry_reference == UtilityGeometryReference.UNKNOWN
            or self.installation == "unknown"
            or not self.source_reference.strip()
            or not self.confirmed_by.strip()
        ):
            raise ValueError(
                "Для подтверждения сети укажите её тип, способ прокладки, смысл геометрии, "
                "источник и ответственного"
            )
        return self
