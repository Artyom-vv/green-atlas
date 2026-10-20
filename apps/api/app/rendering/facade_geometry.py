"""Frozen illustrative facade meshes shared by WebGL and Blender.

Openings modify only prepared display walls. The original CAD footprint, roof,
height, class and accepted source packet remain unchanged. Recessed glass and
flush borders stay inside the envelope instead of overlaying a closed wall.
"""

from hashlib import sha256
from math import hypot
from typing import Any, cast

from shapely import STRtree, constrained_delaunay_triangles
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

_FACADE_PLANE_TOLERANCE_M = 1e-7


def _opposite_detail_edges(
    rings: list[list[list[float]]], height: float
) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Find opposite wall sides indistinguishable at display-plane precision.

    Accepted CAD rings can contain a numerically thin return without being
    invalid. Keep that source boundary and its plain walls, but do not place
    different illustrative frame materials against the opposite wall. This is
    only an appearance eligibility check, never a repair of the CAD polygon.
    """
    edges = []
    for ring_index, ring in enumerate(rings):
        for edge_index, a in enumerate(ring):
            b = ring[(edge_index + 1) % len(ring)]
            length = hypot(b[0] - a[0], b[1] - a[1])
            if length > 1e-8:
                edges.append(
                    (
                        (ring_index, edge_index),
                        a,
                        b,
                        length,
                        (b[0] - a[0]) / length,
                        (b[1] - a[1]) / length,
                    )
                )
    lines = [LineString([a[:2], b[:2]]) for _, a, b, *_ in edges]
    index = STRtree(lines)
    blocked: dict[tuple[int, int], list[tuple[int, int]]] = {}
    tolerance = _FACADE_PLANE_TOLERANCE_M
    for i, (address, a, b, length, ux, uy) in enumerate(edges):
        for candidate in index.query(lines[i].buffer(tolerance, cap_style="flat")):
            j = int(candidate)
            if j <= i:
                continue
            other, c, d, _, vx, vy = edges[j]
            if ux * vx + uy * vy >= 0:
                continue
            if any(
                abs((p[0] - a[0]) * uy - (p[1] - a[1]) * ux) > tolerance for p in (c, d)
            ):
                continue
            along = [(p[0] - a[0]) * ux + (p[1] - a[1]) * uy for p in (c, d)]
            if min(length, max(along)) - max(0.0, min(along)) <= tolerance:
                continue
            if min(max(a[2], b[2]), max(c[2], d[2])) + height <= max(
                min(a[2], b[2]), min(c[2], d[2])
            ):
                continue
            blocked.setdefault(address, []).append(other)
            blocked.setdefault(other, []).append(address)
    return blocked


def _style(building_class: str, height: float) -> str:
    if building_class == "retail":
        return "retail_glazing"
    if building_class == "apartments" and height >= 9:
        return "residential_grid"
    if building_class in {"school", "kindergarten"}:
        return "civic_lowrise"
    if building_class in {"service", "kiosk", "garage", "garages"}:
        return "service_lowrise"
    return "generic_lowrise"


def prepare_facades(
    rows: list[dict[str, Any]], source: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    prepared: list[dict[str, Any]] = []
    detail_meshes: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for row in rows:
        if row.get("kind") != "building" or not row.get("height_m"):
            prepared.append(row)
            continue
        height = float(row["height_m"])
        source_rings = row.get("footprint_rings", [])
        rings = []
        for index, ring in enumerate(source_rings):
            signed = sum(
                a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, [*ring[1:], ring[0]])
            )
            rings.append(ring if (signed > 0) == (index == 0) else list(reversed(ring)))
        if not rings:
            prepared.append(row)
            continue
        polygon = orient(
            Polygon([p[:2] for p in rings[0]], [[p[:2] for p in r] for r in rings[1:]]),
            sign=1.0,
        )
        if not polygon.is_valid:
            raise ValueError("Невалидный контур оформления фасада")
        omitted_detail_edges = _opposite_detail_edges(rings, height)
        # Footprint rings are already oriented by display_meshes. Preserve
        # their exact vertex XYZ rather than resampling or simplifying them.
        style = _style(str(row.get("building_class", "building")), height)
        roof_vertices: list[list[float]] = []
        wall_faces: list[list[int]] = []
        for face in row["faces"]:
            points = [row["vertices"][i] for i in face]
            if max(p[2] for p in points) - min(p[2] for p in points) <= 1e-9:
                offset = len(roof_vertices)
                roof_vertices.extend([list(p) for p in points])
                wall_faces.append([offset + i for i in range(len(points))])
        meshes = {
            kind: {
                "id": f"{row['id']}:{kind}",
                "kind": kind,
                "source": source,
                "source_building_id": row["id"],
                "material_key": kind.removeprefix("facade_"),
                "vertices": [],
                "faces": [],
            }
            for kind in (
                "facade_glass",
                "facade_frame",
                "facade_joint",
                "facade_plinth",
            )
        }
        windows = entries = joints = 0
        exterior = rings[0]
        entry_edge = max(
            range(len(exterior)),
            key=lambda i: hypot(
                exterior[(i + 1) % len(exterior)][0] - exterior[i][0],
                exterior[(i + 1) % len(exterior)][1] - exterior[i][1],
            ),
        )
        for ring_index, ring in enumerate(rings):
            for edge_index, a in enumerate(ring):
                b = ring[(edge_index + 1) % len(ring)]
                dx, dy = b[0] - a[0], b[1] - a[1]
                length = hypot(dx, dy)
                if length <= 1e-8:
                    continue
                ux, uy = dx / length, dy / length
                # Right of the oriented boundary is outside the occupied
                # building, including courtyard rings. Recess points left.
                nx, ny = uy, -ux

                def point(s: float, z: float, recess: float = 0.0) -> list[float]:
                    return [
                        a[0] + ux * s - nx * recess,
                        a[1] + uy * s - ny * recess,
                        a[2] + (b[2] - a[2]) * s / length + z,
                    ]

                def add_face(kind: str, coordinates: list[list[float]]) -> None:
                    mesh = meshes[kind]
                    offset = len(mesh["vertices"])
                    mesh["vertices"].extend(coordinates)
                    mesh["faces"].append([offset + i for i in range(len(coordinates))])

                openings: list[tuple[float, float, float, float, str]] = []
                details_allowed = (ring_index, edge_index) not in omitted_detail_edges
                if details_allowed and height >= 1.2 and length >= 2.0:
                    spacing = 3.45 if style == "residential_grid" else 3.7
                    columns = max(1, int((length - 0.5) / spacing))
                    floors = (
                        1
                        if style == "retail_glazing"
                        # Count openings that fit, not complete storeys below
                        # a subtracted roof allowance. The latter dropped the
                        # upper row on every 3/6/9-m envelope and left a blank
                        # half-height facade on ordinary 6-m context buildings.
                        else max(1, int((height - 0.85 - 1.50 - 0.22) // 3) + 1)
                    )
                    for column in range(columns):
                        center = (column + 0.5) * length / columns
                        variation = int(
                            sha256(
                                f"{row['id']}:{ring_index}:{edge_index}:{column}".encode()
                            ).hexdigest()[:8],
                            16,
                        )
                        width = min(
                            length / columns - 0.65,
                            2.7
                            if style == "retail_glazing"
                            else 1.35 + (variation % 7) * 0.035,
                        )
                        for floor in range(floors):
                            is_entry = (
                                ring_index == 0
                                and edge_index == entry_edge
                                and column == 0
                                and floor == 0
                            )
                            lower = (
                                0.08
                                if is_entry
                                else min(0.85, height * 0.28) + floor * 3
                            )
                            upper = (
                                min(2.25, height - 0.22)
                                if is_entry
                                else min(
                                    lower
                                    + (2.2 if style == "retail_glazing" else 1.50),
                                    height - 0.22,
                                )
                            )
                            half = min(width / 2, 0.50 if is_entry else width / 2)
                            left, right = center - half, center + half
                            if (
                                upper - lower < 0.4
                                or left < 0.2
                                or right > length - 0.2
                            ):
                                continue
                            # Extremely narrow or concave source envelopes can
                            # lack room for the shallow recess. Do not push an
                            # opening outside the known footprint.
                            if not all(
                                polygon.covers(Point(point(s, z, 0.045)[:2]))
                                for s in (left, right)
                                for z in (lower, upper)
                            ):
                                continue
                            openings.append(
                                (
                                    left,
                                    right,
                                    lower,
                                    upper,
                                    "entry" if is_entry else "window",
                                )
                            )
                if details_allowed and length >= 0.8 and height >= 0.8:
                    # A flush roof edge occupies a real display wall cutout,
                    # never an extra coplanar overlay on the original wall.
                    openings.append(
                        (0.12, length - 0.12, height - 0.16, height - 0.10, "joint")
                    )
                    if style == "residential_grid":
                        for floor in range(1, int(height / 3)):
                            z = floor * 3.0
                            if z < height - 0.25:
                                openings.append(
                                    (0.12, length - 0.12, z - 0.012, z + 0.012, "joint")
                                )
                holes = [
                    [(left, lower), (right, lower), (right, upper), (left, upper)]
                    for left, right, lower, upper, _ in openings
                ]
                wall: BaseGeometry = Polygon(
                    [(0, 0), (length, 0), (length, height), (0, height)], holes
                )
                if not wall.is_valid:
                    raise ValueError("Пересекающиеся детали оформления фасада")
                # The base course occupies the existing wall, rather than a
                # second offset shell. Cut its door openings from the same
                # polygon, and keep exact source height/footprint/roof intact.
                if details_allowed:
                    plinth_height = min(0.36, height * 0.12)
                    plinth = wall.intersection(
                        Polygon(
                            [
                                (0, 0),
                                (length, 0),
                                (length, plinth_height),
                                (0, plinth_height),
                            ]
                        )
                    )
                    wall = wall.difference(plinth)
                    for member in constrained_delaunay_triangles(plinth).geoms:
                        triangle = orient(cast(Polygon, member), sign=1.0)
                        add_face(
                            "facade_plinth",
                            [
                                point(s, z)
                                for s, z, *_ in list(triangle.exterior.coords)[:3]
                            ],
                        )
                for member in constrained_delaunay_triangles(wall).geoms:
                    triangle = orient(cast(Polygon, member), sign=1.0)
                    coords = list(triangle.exterior.coords)[:3]
                    offset = len(roof_vertices)
                    roof_vertices.extend([point(s, z) for s, z, *_ in coords])
                    wall_faces.append([offset, offset + 1, offset + 2])
                for left, right, lower, upper, kind in openings:
                    rect = [
                        (left, lower),
                        (right, lower),
                        (right, upper),
                        (left, upper),
                    ]
                    if kind == "joint":
                        add_face("facade_joint", [point(s, z) for s, z in rect])
                        joints += 1
                        continue
                    border = min(0.075, (right - left) * 0.12, (upper - lower) * 0.12)
                    inner = [
                        (left + border, lower + border),
                        (right - border, lower + border),
                        (right - border, upper - border),
                        (left + border, upper - border),
                    ]
                    outer_xyz = [point(s, z) for s, z in rect]
                    inner_xyz = [point(s, z) for s, z in inner]
                    glass_xyz = [point(s, z, 0.045) for s, z in inner]
                    # A metric central mullion gives ordinary windows readable
                    # proportions at street distance. The two panes end at the
                    # mullion; no glass/frame coplanar overlap or invented
                    # reflection is introduced.
                    if kind == "window" and right - left >= 0.9:
                        middle = (left + right) / 2
                        half_bar = 0.0275
                        for lo, hi in (
                            (left + border, middle - half_bar),
                            (middle + half_bar, right - border),
                        ):
                            add_face(
                                "facade_glass",
                                [
                                    point(lo, lower + border, 0.045),
                                    point(hi, lower + border, 0.045),
                                    point(hi, upper - border, 0.045),
                                    point(lo, upper - border, 0.045),
                                ],
                            )
                        bar = [
                            (middle - half_bar, lower + border),
                            (middle + half_bar, lower + border),
                            (middle + half_bar, upper - border),
                            (middle - half_bar, upper - border),
                        ]
                        add_face("facade_frame", [point(s, z) for s, z in bar])
                        for i, j in ((1, 2), (3, 0)):
                            add_face(
                                "facade_frame",
                                [
                                    point(*bar[i]),
                                    point(*bar[j]),
                                    point(*bar[j], 0.045),
                                    point(*bar[i], 0.045),
                                ],
                            )
                    else:
                        add_face("facade_glass", glass_xyz)
                    for i in range(4):
                        j = (i + 1) % 4
                        add_face(
                            "facade_frame",
                            [outer_xyz[i], outer_xyz[j], inner_xyz[j], inner_xyz[i]],
                        )
                        add_face(
                            "facade_frame",
                            [inner_xyz[i], inner_xyz[j], glass_xyz[j], glass_xyz[i]],
                        )
                    windows += kind == "window"
                    entries += kind == "entry"
        prepared.append({**row, "vertices": roof_vertices, "faces": wall_faces})
        detail_meshes.extend(mesh for mesh in meshes.values() if mesh["faces"])
        evidence.append(
            {
                "building_id": row["id"],
                "source": source,
                "building_class": row.get("building_class", "building"),
                "height_m": height,
                "facade_style": style,
                "windows": windows,
                "entries": entries,
                "facade_joints": joints,
                "appearance": "recessed divided panes and base course inside the source envelope",
                "omitted_detail_edges": [
                    {
                        "ring_index": ring_index,
                        "edge_index": edge_index,
                        "reason": "opposite_boundary_below_display_separation",
                        "opposite_edges": [list(address) for address in sorted(others)],
                        "display_plane_tolerance_m": _FACADE_PLANE_TOLERANCE_M,
                    }
                    for (ring_index, edge_index), others in sorted(
                        omitted_detail_edges.items()
                    )
                ],
                "status": "illustrative openings; exact facade and opening positions not surveyed",
            }
        )
    return prepared, detail_meshes, evidence
