"""Metric sagitta-bounded sampling of authored bulges, only for the AOI mask."""

from math import asin, atan, atan2, ceil, cos, isfinite, sin, sqrt

from ezdxf.entities import LWPolyline
from ezdxf.math import Vec2, Vec3, bulge_to_arc

from app.cad_import.contracts import CadConversionError


def boundary_vertices(
    entity: LWPolyline,
    meters_per_unit: float,
    tolerance_m: float,
    max_vertices: int,
) -> tuple[list[Vec3], bool]:
    points = list(entity.get_points("xyb"))
    ocs = entity.ocs()
    elevation = entity.dxf.elevation
    tolerance = tolerance_m / meters_per_unit
    result: list[Vec3] = []
    curved = False
    for index, (x, y, bulge) in enumerate(points):
        start = Vec2(x, y)
        end = Vec2(points[(index + 1) % len(points)][:2])
        segments = 1
        center = Vec2()
        radius = angle = sweep = 0.0
        if bulge:
            curved = True
            if start.isclose(end):
                raise CadConversionError("Дуга границы имеет совпадающие концы")
            center, _, _, radius = bulge_to_arc(start, end, bulge)
            if not isfinite(radius) or radius <= 0:
                raise CadConversionError("Некорректный радиус дуги границы")
            angle = atan2(start.y - center.y, start.x - center.x)
            sweep = 4 * atan(bulge)
            # Stable equivalent of 2*acos(1-sagitta/radius), capped at pi.
            max_angle = 4 * asin(sqrt(min(tolerance / radius, 1) / 2))
            segments = max(1, ceil(abs(sweep) / max_angle))
        if len(result) + segments > max_vertices:
            raise CadConversionError("Превышен бюджет вершин маски рабочей территории")
        for step in range(segments):
            vertex = start
            if step:
                current = angle + sweep * step / segments
                vertex = center + Vec2(cos(current), sin(current)) * radius
            result.append(ocs.to_wcs((vertex.x, vertex.y, elevation)))
    return result, curved
