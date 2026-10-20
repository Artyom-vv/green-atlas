"""One render-only envelope for curb tops, without changing source centre lines."""

from collections import defaultdict
from collections.abc import Iterator
from math import hypot, isfinite
from typing import Any, cast

from shapely import (
    constrained_delaunay_triangles,
    get_coordinates,
    line_merge,
    union_all,
)
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient


def _oriented_triangle_points(polygon: Polygon) -> Iterator[list[tuple[float, float]]]:
    """Read existing GEOS triangles in bulk, retaining the first vertex.

    GEOS returns closed three-vertex rings. Positive winding is the same
    a,c,b reversal as orient(sign=1), without a GEOS wrapper per triangle.
    The constrained triangulation and all input coordinates remain unchanged.
    """
    coordinates = get_coordinates(constrained_delaunay_triangles(polygon))
    if len(coordinates) % 4:
        raise ValueError("Неверная триангуляция объёма бордюра")
    for offset in range(0, len(coordinates), 4):
        a, b, c = [
            (float(point[0]), float(point[1]))
            for point in coordinates[offset : offset + 3]
        ]
        winding = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        yield [a, b, c] if winding >= 0 else [a, c, b]


def curb_envelopes(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[float, Any]], dict[str, Any]]:
    """Union only equal base planes/heights; retain slopes and different levels.

    A line's base is its exact linear grade, constant across its visual width.
    Sloping and level curbs therefore never acquire a fabricated flat elevation.
    """
    groups: dict[tuple[float, float, float, float], list[BaseGeometry]] = defaultdict(
        list
    )
    sources: dict[tuple[float, float, float, float], set[str]] = defaultdict(set)
    chains: dict[
        tuple[tuple[float, float, float, float], float, str], list[LineString]
    ] = defaultdict(list)
    input_area = 0.0
    for row in rows:
        a, b = row["start_scene_xyz_m"], row["end_scene_xyz_m"]
        width, height = float(row["width_m"]), float(row["height_m"])
        if not all(isfinite(float(v)) for v in (*a, *b, width, height)):
            raise ValueError("Невалидные координаты бордюра")
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = hypot(dx, dy)
        if length <= 1e-8:
            continue
        if width <= 0 or height <= 0:
            raise ValueError("Невалидные размеры бордюра")
        slope = (b[2] - a[2]) / (length * length)
        gx, gy = slope * dx, slope * dy
        base = a[2] - gx * a[0] - gy * a[1]
        key = (gx, gy, base, height)
        line = LineString([a[:2], b[:2]])
        input_area += line.length * width
        chains[key, width, row["source_handle"]].append(line)
        sources[key].add(row["source_handle"])
    for (key, width, _), lines in chains.items():
        # Join exact endpoints within one source path. Buffering each segment
        # independently leaves wedges at bends and seams in a closed rim.
        # No snapping or joining of unrelated source IDs takes place.
        chain = line_merge(union_all(lines))
        groups[key].append(
            chain.buffer(width / 2, cap_style="flat", join_style="mitre", mitre_limit=2)
        )
    meshes = []
    flat_tops = []
    output_area = 0.0
    for index, (key, polygons) in enumerate(groups.items()):
        gx, gy, base, height = key
        geometry = union_all(polygons)
        if not geometry.is_valid:
            raise ValueError("Невалидный объём бордюра")
        output_area += geometry.area
        if gx == 0 and gy == 0:
            flat_tops.append((base + height, geometry))
        vertices: list[list[float]] = []
        faces: list[list[int]] = []
        parts = (
            list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
        )

        def z(x: float, y: float) -> float:
            return gx * x + gy * y + base

        for raw in parts:
            part = orient(cast(Polygon, raw), sign=1.0)
            for points in _oriented_triangle_points(part):
                offset = len(vertices)
                vertices.extend([[x, y, z(x, y)] for x, y, *_ in points])
                vertices.extend([[x, y, z(x, y) + height] for x, y, *_ in points])
                faces.extend(
                    [
                        [offset + 2, offset + 1, offset],
                        [offset + 3, offset + 4, offset + 5],
                    ]
                )
            for ring in [part.exterior, *part.interiors]:
                coords = list(ring.coords)
                for a, b in zip(coords, coords[1:]):
                    offset = len(vertices)
                    vertices.extend(
                        [
                            [a[0], a[1], z(a[0], a[1])],
                            [b[0], b[1], z(b[0], b[1])],
                            [b[0], b[1], z(b[0], b[1]) + height],
                            [a[0], a[1], z(a[0], a[1]) + height],
                        ]
                    )
                    faces.append([offset, offset + 1, offset + 2, offset + 3])
        meshes.append(
            {
                "id": f"environment:curb-envelope:{index}",
                "kind": "curb",
                "source": "cad_surface",
                "source_ids": sorted(sources[key]),
                "vertices": vertices,
                "faces": faces,
                "base_plane": [gx, gy, base],
                "height_m": height,
                "status": "illustrative dimensions; union of source-positioned curb footprints",
            }
        )
    return (
        meshes,
        flat_tops,
        {
            "policy": "single envelope per exact base plane and height; source centre lines preserved",
            "envelope_count": len(meshes),
            "input_strip_area_m2": input_area,
            "envelope_footprint_area_m2": output_area,
            "removed_duplicate_strip_area_m2": max(0.0, input_area - output_area),
            "continuous_source_join_area_m2": max(0.0, output_area - input_area),
        },
    )
