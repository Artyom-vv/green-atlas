"""Свидетельства точной прямой оси из исходного DXF; кривые и вложенные компоненты не
объявляются такими осями.
"""

from typing import Any

from app.geometry.networks.axis_geometry import (
    AXIS_PROVENANCE_VERSION,
    geometry_fingerprint,
)


def straight_axis_provenance(entity: Any, geometry: dict) -> dict:
    if geometry.get("type") != "LineString" or not entity.dxf.get("handle"):
        return {}
    kind = entity.dxftype()
    if kind == "LINE":
        if entity.dxf.start.z != entity.dxf.end.z:
            return {}
    elif kind == "LWPOLYLINE":
        if entity.closed or any(point[2] != 0 for point in entity.get_points("xyb")):
            return {}
    elif kind == "POLYLINE":
        if (
            not entity.is_2d_polyline
            or entity.is_closed
            or int(entity.dxf.get("flags", 0))
            & 6  # Вершины аппроксимации кривой или сплайна не задают точную прямую ось.
            or any(vertex.dxf.get("bulge", 0) != 0 for vertex in entity.vertices)
        ):
            return {}
    else:
        return {}
    if kind != "LINE" and tuple(entity.dxf.get("extrusion", (0, 0, 1))) != (0, 0, 1):
        return {}
    try:
        fingerprint = geometry_fingerprint(geometry)
    except ValueError:
        # Отклонение неконечных координат и диагностика принадлежат reader.
        # Повреждённая геометрия не получает доказательство и не срывает весь импорт.
        return {}
    return {
        "source_axis_provenance": {
            "version": AXIS_PROVENANCE_VERSION,
            "source_handle": str(entity.dxf.handle),
            "insert_chain": [],
            "geometry_sha256": fingerprint,
        }
    }
