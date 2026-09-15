"""Complete planar MPOLYGON boundaries with explicit OCS and offset handling."""

from __future__ import annotations

from collections.abc import Callable
from math import isfinite
from typing import Any

from ezdxf.math import Vec3
from ezdxf.path import from_hatch
from shapely.geometry import Polygon, shape

FLATTENING_DISTANCE = 0.1
MIN_CURVE_SEGMENTS = 16
COORDINATE_DECIMALS = 6
PolygonRings = list[list[list[float]]]
PolygonGeometry = dict[str, Any]


def mpolygon_geometry(
    entity: Any,
    factor: float,
    assemble_rings: Callable[[PolygonRings], PolygonGeometry | None],
) -> PolygonGeometry | None:
    """Reject incomplete/invalid areas instead of inventing a repaired footprint.

    MPOLYGON uses nested ring parity regardless of boundary style flags.
    ezdxf's shared path reader applies extrusion/elevation, but its separate
    offset argument must be supplied explicitly for MPOLYGON.
    """
    loops: PolygonRings = []
    try:
        offset = Vec3(entity.dxf.get("offset_vector", (0, 0, 0)))
        for path in from_hatch(entity, offset=offset):
            loop = [
                [
                    round(point.x * factor, COORDINATE_DECIMALS),
                    round(point.y * factor, COORDINATE_DECIMALS),
                ]
                for point in path.flattening(
                    FLATTENING_DISTANCE, segments=MIN_CURVE_SEGMENTS
                )
            ]
            if len(loop) < 3 or not all(
                isfinite(coordinate) for point in loop for coordinate in point
            ):
                return None
            if loop[-1] != loop[0]:
                loop.append(loop[0])
            polygon = Polygon(loop)
            if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
                return None
            loops.append(loop)
        geometry = assemble_rings(loops)
        if geometry is None or not shape(geometry).is_valid:
            return None
        return geometry
    except Exception:
        # The reader records this as unrenderable geometry and marks the
        # effective layer incomplete. No source entity is removed or modified.
        return None
