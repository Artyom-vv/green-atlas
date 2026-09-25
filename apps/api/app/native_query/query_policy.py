"""Local-query bounds, not normative permissions or geometry tolerances."""

from app.regulations.placement_config import MAX_SETBACK_M, PLACEMENT_CONFIG

# Local review of actual unreadable objects, separate from network setbacks.
UNRESOLVED_REVIEW_HORIZON_M = PLACEMENT_CONFIG.working_geometry.unresolved_review_horizon_m
# Broad phase must include the largest supported building/road/network row.
BASE_QUERY_REACH_M = MAX_SETBACK_M


def query_reach_m(*plant_radii: float) -> float:
    return max(BASE_QUERY_REACH_M, UNRESOLVED_REVIEW_HORIZON_M, *plant_radii)


def review_reach_m(*plant_radii: float) -> float:
    return max(UNRESOLVED_REVIEW_HORIZON_M, *plant_radii)
