"""Apply reviewed assortment rows and conditions, without claiming a planting permit."""

from typing import Literal

from pydantic import BaseModel, Field

from app.species.assortment_inventory import assortment_entry, category_cell

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
    spread_control_confirmed: bool = False


ASSORTMENT_SOURCE = "https://www.mos.ru/upload/content/files/c6ee55bb75792e008a490f28cfe834fb/Osnovnoiidopolnitelniiassortimentderevevkystarnikovilian.pdf"
ASSORTMENT_SHA256 = "114fe5b7598f32ca0b13162985cc80d14be569d8898aacbcd148fd08571a4b67"
ASSORTMENT_REVISION = "mos-assortment@2026-09-17.3"


def assortment_status(species_id: str, context: TerritoryContext) -> AssortmentStatus:
    if context.regime != "ordinary":
        return "individual_review"
    row = assortment_entry(species_id)
    if row is None or not row.matrix_reviewed or not row.conditions_reviewed:
        return "unreviewed"
    cell = category_cell(row, context.category)
    if cell == "-":
        return "not_recommended"
    if cell != "+":
        return "unreviewed"
    if row.requires_spread_control and not context.spread_control_confirmed:
        return "individual_review"
    return "listed"
