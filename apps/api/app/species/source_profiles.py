"""Published dimensions and qualitative growth labels, separate from our curve."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator

METRES_PER_FOOT = 0.3048
SOURCE_GROWTH_LABELS = {"Slow": "slow", "Medium": "moderate", "Rapid": "fast"}


class SourceSpeciesProfile(BaseModel):
    species_id: str
    common_name: str
    scientific_name: str
    kind: Literal["tree", "shrub"]
    crown_shape: Literal["conical", "irregular", "spreading"]
    height_ft: tuple[float, float]
    width_ft: tuple[float, float]
    growth_label: Literal["Slow", "Medium", "Rapid"]
    source_url: str
    checked_on: str
    revision_tag: str
    site_notes: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_dimensions(self) -> "SourceSpeciesProfile":
        for lower, upper in (self.height_ft, self.width_ft):
            if not 0 < lower <= upper < float("inf"):
                raise ValueError("Invalid published dimension range")
        return self

    @property
    def height_m(self) -> tuple[float, float]:
        return tuple_metres(self.height_ft)

    @property
    def width_m(self) -> tuple[float, float]:
        return tuple_metres(self.width_ft)

    @property
    def evidence_note(self) -> str:
        return (
            f"Размеры и категория темпа роста — NC State Extension, проверено {self.checked_on}. "
            "Прогноз по годам использует прежнюю сценарную кривую сервиса, не измерения роста в Москве. "
            "Корневая архитектура неизвестна; корневой диапазон — прежнее сценарное допущение, "
            "не доказанный предел корней. Требуется проверка дендрологом. "
            + " ".join(self.site_notes)
        )


def tuple_metres(values: tuple[float, float]) -> tuple[float, float]:
    return round(values[0] * METRES_PER_FOOT, 4), round(values[1] * METRES_PER_FOOT, 4)


@lru_cache(maxsize=1)
def _profiles() -> list[SourceSpeciesProfile]:
    return TypeAdapter(list[SourceSpeciesProfile]).validate_json(
        Path(__file__).with_name("data").joinpath("source-profiles.json").read_bytes()
    )


def source_profiles() -> list[SourceSpeciesProfile]:
    return [p.model_copy(deep=True) for p in _profiles()]
