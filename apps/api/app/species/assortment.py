"""Reviewed tree rows of the municipal assortment; not a planting permit."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

TerritoryCategory = Literal[
    "courtyard",
    "preschool",
    "school_sport",
    "healthcare",
    "major_road",
    "public_square",
    "park",
    "industrial",
]
AssortmentStatus = Literal[
    "listed", "not_recommended", "unreviewed", "individual_review"
]


class TerritoryContext(BaseModel):
    category: TerritoryCategory
    regime: Literal["ordinary", "individual_project", "unknown"]
    basis: str = Field(min_length=1, max_length=500)


ASSORTMENT_SOURCE = "https://www.mos.ru/upload/content/files/c6ee55bb75792e008a490f28cfe834fb/Osnovnoiidopolnitelniiassortimentderevevkystarnikovilian.pdf"
ASSORTMENT_SHA256 = "114fe5b7598f32ca0b13162985cc80d14be569d8898aacbcd148fd08571a4b67"
ASSORTMENT_REVISION = "mos-assortment-trees@2026-09-17.1"


@dataclass(frozen=True)
class AssortmentRow:
    page: int
    row: int
    section: str
    excluded: frozenset[TerritoryCategory] = frozenset()
    notes: tuple[str, ...] = ()


# Explicit reviewed rows, not inferred from common names or a neighbouring
# species. Ulmus laevis has no reviewed row here and must remain unreviewed.
TREE_ASSORTMENT = {
    "betula-pendula": AssortmentRow(
        3, 2, "Лиственные деревья", frozenset({"healthcare"})
    ),
    "quercus-robur": AssortmentRow(
        4,
        17,
        "Лиственные деревья",
        frozenset({"preschool", "school_sport", "major_road", "industrial"}),
    ),
    "acer-platanoides": AssortmentRow(5, 26, "Лиственные деревья"),
    "tilia-cordata": AssortmentRow(
        5,
        33,
        "Лиственные деревья",
        notes=(
            "Для придорожных посадок таблица рекомендует уход для удаления солевой взвеси с крон.",
        ),
    ),
    "sorbus-aucuparia": AssortmentRow(
        6,
        42,
        "Лиственные деревья",
        frozenset({"preschool", "school_sport"}),
        (
            "Размещать на газоне с учётом удаления от твёрдых покрытий и детских/спортивных площадок; расстояние таблицей здесь не задано.",
        ),
    ),
    "picea-abies": AssortmentRow(
        1,
        2,
        "Хвойные деревья",
        frozenset({"major_road"}),
        ("Требует оценки газовой нагрузки, уплотнения почвы и условий ухода.",),
    ),
    "pinus-sylvestris": AssortmentRow(
        1,
        7,
        "Хвойные деревья",
        notes=(
            "Требует оценки газовой нагрузки, уплотнения почвы и ухода; примечание строки продолжается на странице 2.",
        ),
    ),
}


def assortment_status(species_id: str, context: TerritoryContext) -> AssortmentStatus:
    if context.regime != "ordinary":
        return "individual_review"
    row = TREE_ASSORTMENT.get(species_id)
    if row is None:
        return "unreviewed"
    return "not_recommended" if context.category in row.excluded else "listed"
