"""Local geometric measurements. These are not fabricated AutoCAD replies."""

from dataclasses import dataclass
from typing import Literal

from shapely.geometry import Point

from app.native_query.live_rules import MeasuredObstacle
from app.regulations.placement_config import PLACEMENT_CONFIG

MARGIN_M = PLACEMENT_CONFIG.technical.cell_certificate_margin_m


@dataclass(frozen=True)
class LocalMeasurement:
    interior_known: bool
    capability: Literal["area", "curve", "unavailable"]
    preparation_error: str = ""


@dataclass(frozen=True)
class LocalAnswer:
    membership: Literal["occupied", "edge", "outside", "unknown"]
    distance_units: float | None
    status: int = 0
    error: str = ""


def measure(item, projection, x, y, factor, *, site=False):
    if projection.reason:
        return MeasuredObstacle(
            item, LocalMeasurement(False, "unavailable", projection.detail), None
        )
    point = Point(x, y)
    margin = projection.tolerance_m + MARGIN_M
    area = all(
        g.geom_type in {"Polygon", "MultiPolygon"} for g in projection.geometries
    )
    if site and not area:
        return MeasuredObstacle(
            item,
            LocalMeasurement(
                False, "unavailable", "Граница территории не передана площадью"
            ),
            None,
        )
    # Boundary distance retains holes. For multiple source areas, membership
    # in any area suffices, whereas every nearby obstacle remains a constraint.
    distances = [
        (g.boundary if g.geom_type in {"Polygon", "MultiPolygon"} else g).distance(
            point
        )
        for g in projection.geometries
    ]
    inside = [g.covers(point) if area else False for g in projection.geometries]
    distance = min(distances)
    if site:
        certain = [
            d for d, contained in zip(distances, inside) if contained and d > margin
        ]
        if certain:
            membership, distance = "occupied", max(certain)
        elif distance <= margin:
            membership = "unknown"  # Never grant site membership inside the error band.
        else:
            membership = "outside"
    else:
        membership = (
            "occupied" if any(inside) else "edge" if distance <= margin else "outside"
        )
    # A lower distance bound prevents approximation from granting a clearance.
    distance = max(0.0, distance - margin) / factor
    return MeasuredObstacle(
        item,
        LocalMeasurement(area, "area" if area else "curve"),
        LocalAnswer(membership, distance),
    )
