"""Read-only evidence for the candidate generator's precomputed domain."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchDomain(BaseModel):
    zone_id: str
    revision: str
    geometry: dict[str, Any]
    unresolved_geometry: dict[str, Any]
    pending_geometry: dict[str, Any] | None = None
    available_area_m2: float = Field(ge=0)
    excluded_area_m2: float = Field(ge=0)
    unresolved_area_m2: float = Field(ge=0)
    pending_area_m2: float = Field(default=0, ge=0)
    method: Literal["native_cells", "hybrid"] = "native_cells"
    final_check: Literal["autocad", "prepared_geometry"] = "autocad"
    minimum_cell_m: float | None = Field(default=None, gt=0)
    measured_cells: int = Field(ge=0)
    processed_objects: int = Field(default=0, ge=0)
    total_objects: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    source_issues: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_s: float = Field(ge=0)
    stop_reason: Literal["resolution", "time_limit", "probe_limit"]
    unresolved_reasons: dict[str, int] = Field(default_factory=dict)
    # Disjoint PRIMARY reason partition, not summed overlapping influences.
    unresolved_reason_areas_m2: dict[str, float] = Field(default_factory=dict)


def search_domain_details(zones):
    return [
        SearchDomain(zone_id=zone.id, **zone.geometry["ga_search_domain"])
        for zone in zones
        if "ga_search_domain" in zone.geometry
    ]
