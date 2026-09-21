"""Source references for a per-zone species eligibility decision."""

from pydantic import BaseModel, Field

from app.species.assortment import ASSORTMENT_REVISION, ASSORTMENT_SOURCE


class PlantEligibility(BaseModel):
    allowed: bool
    code: str
    reason: str
    zone_id: str | None = None
    category: str | None = None
    source_url: str = ASSORTMENT_SOURCE
    source_revision: str = ASSORTMENT_REVISION
    source_page: int | None = None
    source_row: int | None = None
    source_row_id: str | None = None
    notes: list[str] = Field(default_factory=list)
