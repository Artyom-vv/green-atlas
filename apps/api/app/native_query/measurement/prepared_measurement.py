"""Локальные измерения подготовленной геометрии с собственным результатом; ответы
AutoCAD не имитируются.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from shapely.geometry import Point

from app.native_query.policy.live_rules import MeasuredObstacle
from app.regulations.placement_config import PLACEMENT_CONFIG

if TYPE_CHECKING:
    from app.native_query.live.live_inventory import QueryObject
    from app.native_query.search.hybrid_geometry import Projection

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


def measure(
    item: QueryObject,
    projection: Projection,
    x: float,
    y: float,
    factor: float,
    *,
    site: bool = False,
) -> MeasuredObstacle:
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
    # Расстояние до границы сохраняет отверстия. Достаточно принадлежности любой
    # исходной территории, но каждое близкое препятствие остаётся ограничением.
    distances = [
        (g.boundary if g.geom_type in {"Polygon", "MultiPolygon"} else g).distance(
            point
        )
        for g in projection.geometries
    ]
    inside = [
        g.covers(point) if g.geom_type in {"Polygon", "MultiPolygon"} else False
        for g in projection.geometries
    ]
    distance = min(distances)
    interior_known = area or any(inside)
    if not area and any(inside):
        # Известная площадь подтверждает занятость точки независимо от линейного
        # остатка. Расстояние до содержащей площади не заменяется соседней линией.
        distance = max(d for d, contained in zip(distances, inside) if contained)
    membership: Literal["occupied", "edge", "outside", "unknown"]
    if site:
        certain = [
            d for d, contained in zip(distances, inside) if contained and d > margin
        ]
        if certain:
            membership, distance = "occupied", max(certain)
        elif distance <= margin:
            membership = "unknown"  # В полосе ошибки принадлежность территории не подтверждается.
        else:
            membership = "outside"
    else:
        membership = (
            "occupied" if any(inside) else "edge" if distance <= margin else "outside"
        )
    # Нижняя граница расстояния не даёт аппроксимации разрешить ложный отступ.
    distance = max(0.0, distance - margin) / factor
    return MeasuredObstacle(
        item,
        LocalMeasurement(interior_known, "area" if interior_known else "curve"),
        LocalAnswer(membership, distance),
    )
