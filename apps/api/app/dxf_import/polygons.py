"""Complete CAD filled areas using SDK boundaries and explicit OCS handling."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from math import isfinite
from typing import Any

from ezdxf.math import Vec3
from ezdxf.path import Path, from_hatch, from_hatch_boundary_path
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
    try:
        offset = Vec3(entity.dxf.get("offset_vector", (0, 0, 0)))
        return _polygon_geometry(from_hatch(entity, offset=offset), factor, assemble_rings)
    except Exception:
        return None


def hatch_geometry(
    entity: Any,
    factor: float,
    assemble_rings: Callable[[PolygonRings], PolygonGeometry | None],
) -> PolygonGeometry | None:
    """Respect fill style without losing disconnected external boundaries.

    Unlike MPOLYGON, HATCH chooses active boundaries by hatch_style. Let the
    SDK select them; retain our multi-polygon assembly rather than assuming
    the first external path is the only exterior. Reject partial footprints.
    """
    def paths() -> Iterator[Path]:
        ocs = entity.ocs()
        elevation = entity.dxf.elevation.z
        for boundary in entity.paths.rendering_paths(entity.dxf.hatch_style):
            path = from_hatch_boundary_path(boundary, ocs, elevation=elevation)
            if path.has_sub_paths:
                yield from path.sub_paths()
            else:
                yield path

    return _polygon_geometry(paths(), factor, assemble_rings)


def _polygon_geometry(
    paths: Iterable[Path],
    factor: float,
    assemble_rings: Callable[[PolygonRings], PolygonGeometry | None],
) -> PolygonGeometry | None:
    loops: PolygonRings = []
    try:
        for path in paths:
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
