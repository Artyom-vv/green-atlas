"""Versioned source inventory. Reference rows do not fabricate growth models."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AssortmentEntry(BaseModel):
    id: str
    name: str
    source_name: str
    tier: Literal["main", "additional"]
    section: str
    kind: Literal["tree", "shrub", "vine"]
    page: int = Field(ge=1)
    row: int = Field(ge=1)
    cells: str = Field(pattern=r"^[+?\-]{8}$")
    matrix_reviewed: bool
    conditions_reviewed: bool
    calculation_species_id: str | None = None
    notes: list[str] = Field(default_factory=list)
    requires_spread_control: bool = False


class AssortmentInventory(BaseModel):
    revision: str
    source_url: str
    source_sha256: str
    categories: list[str] = Field(min_length=8, max_length=8)
    special_territories_note: str
    entries: list[AssortmentEntry]

    @model_validator(mode="after")
    def unique_identity(self) -> "AssortmentInventory":
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("Duplicate assortment row ID")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("Duplicate assortment category")
        mapped = [
            e.calculation_species_id for e in self.entries if e.calculation_species_id
        ]
        if len(set(mapped)) != len(mapped):
            raise ValueError("Ambiguous calculation species mapping")
        return self


@lru_cache(maxsize=1)
def _inventory() -> AssortmentInventory:
    return AssortmentInventory.model_validate_json(
        Path(__file__).with_name("data").joinpath("moscow-assortment.json").read_bytes()
    )


def assortment_inventory(kind: str | None = None) -> AssortmentInventory:
    if kind not in {None, "tree", "shrub", "vine"}:
        raise ValueError("Неизвестный тип ассортимента")
    result = _inventory().model_copy(deep=True)
    if kind:
        result.entries = [entry for entry in result.entries if entry.kind == kind]
    return result


def assortment_entry(species_id: str) -> AssortmentEntry | None:
    return next(
        (
            entry.model_copy(deep=True)
            for entry in _inventory().entries
            if entry.calculation_species_id == species_id
        ),
        None,
    )


def category_cell(entry: AssortmentEntry, category: str) -> str:
    return entry.cells[_inventory().categories.index(category)]
