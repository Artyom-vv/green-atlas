"""Portable provenance for a derived CAD preview, independent of its sidecar."""

from math import isfinite
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CadPreviewProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["green-atlas-cad-preview-v1"] = "green-atlas-cad-preview-v1"
    status: Literal["preview_only"] = "preview_only"
    calculation_ready: Literal[False] = False
    original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    converted_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    boundary_original_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    boundary_handle: str = Field(pattern=r"^[A-Fa-f0-9]+$", max_length=32)
    influence_radius_m: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    boundary_bounds_m: tuple[float, float, float, float] | None = None
    omitted_entity_count: int = Field(default=0, ge=0)

    @field_validator("boundary_bounds_m")
    @classmethod
    def valid_bounds(
        cls, value: tuple[float, float, float, float] | None
    ) -> tuple[float, float, float, float] | None:
        if value is not None and (
            not all(isfinite(coordinate) for coordinate in value)
            or value[0] >= value[2]
            or value[1] >= value[3]
        ):
            raise ValueError("Некорректная область предварительного просмотра")
        return value


CAD_PREVIEW_MESSAGE = (
    "Предварительная карта выбранной территории. Полнота внешних ссылок, "
    "область влияния и преобразование CAD ещё не подтверждены; "
    "расчёт и выпуск по этому файлу недоступны."
)
