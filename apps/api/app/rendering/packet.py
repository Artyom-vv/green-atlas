"""Геометрия отображения из SceneSnapshot; расчётный снимок не изменяется."""

from typing import Any, cast

from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, Polygon, shape
from shapely.geometry.base import BaseMultipartGeometry
from shapely.geometry.polygon import orient

from app.scene.contracts import SceneSnapshot


def compact_mesh(row: dict[str, Any]) -> dict[str, Any]:
    """Index identical XYZ positions within one plain mesh, without approximation.

    Face order, winding and corner positions are unchanged. This does not use
    a tolerance, join neighbouring meshes, or alter independent line paths and
    footprint rings. Consumers retain face-based normals/material coordinates.
    """
    vertices: list[list[float]] = []
    indices: dict[tuple[float, ...], int] = {}
    remap: list[int] = []
    for vertex in row["vertices"]:
        key = tuple(vertex)
        index = indices.get(key)
        if index is None:
            index = len(vertices)
            indices[key] = index
            vertices.append(list(vertex))
        remap.append(index)
    return {
        **row,
        "vertices": vertices,
        "faces": [[remap[index] for index in face] for face in row["faces"]],
    }


def display_meshes(snapshot: SceneSnapshot) -> list[dict[str, Any]]:
    triangles = []
    for primitive in snapshot.vertical_primitives:
        if (
            primitive.primitive_type == "surface_mesh"
            and primitive.terrain_mapping_status == "confirmed"
        ):
            for face in primitive.faces:
                for i in range(1, len(face) - 1):
                    triangles.append(
                        [primitive.vertices[j] for j in (face[0], face[i], face[i + 1])]
                    )

    def ground(x: float, y: float) -> float:
        for a, b, c in triangles:
            denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(denominator) < 1e-12:
                continue
            u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
            v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
            if u >= -1e-8 and v >= -1e-8 and u + v <= 1 + 1e-8:
                return u * a[2] + v * b[2] + (1 - u - v) * c[2]
        return 0

    meshes = []
    for feature in snapshot.context_features:
        if feature.kind in {"allowed", "planting_area"}:
            continue
        geometry = shape(feature.geometry)
        parts = (
            list(geometry.geoms)
            if isinstance(geometry, BaseMultipartGeometry)
            else [geometry]
        )
        vertices: list[list[float]] = []
        faces: list[list[int]] = []
        lines: list[list[list[float]]] = []
        height = (
            feature.height_m
            if feature.kind == "building"
            and feature.height_m
            and feature.height_status in {"confirmed", "estimated"}
            else 0
        )
        base = feature.base_elevation_m or 0
        footprint_rings = []
        for part in parts:
            if isinstance(part, Polygon):
                if not part.is_valid:
                    raise ValueError(f"Невалидный контур сцены: {feature.feature_id}")
                part = orient(part, sign=1.0)
                rings = [part.exterior, *part.interiors]
                if feature.kind in {"building", "road", "water", "lawn"}:
                    depth = height or (0.055 if feature.kind == "road" else 0)
                    polygon_base = (
                        ground(*part.exterior.coords[0][:2]) if base == 0 else base
                    )
                    top = polygon_base + (
                        depth or (0.008 if feature.kind == "water" else 0.012)
                    )
                    if height:
                        footprint_rings.extend(
                            [
                                [
                                    [x, y, polygon_base]
                                    for x, y, *_ in list(ring.coords)[:-1]
                                ]
                                for ring in rings
                            ]
                        )
                    triangulated = constrained_delaunay_triangles(part)
                    for member in triangulated.geoms:
                        # GEOS returns triangle polygons for a polygon input.
                        triangle = cast(Polygon, member)
                        start = len(vertices)
                        vertices.extend(
                            [
                                [x, y, top]
                                for x, y, *_ in list(triangle.exterior.coords)[:3]
                            ]
                        )
                        a, b, c = vertices[start : start + 3]
                        winding = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (
                            c[0] - a[0]
                        )
                        faces.append(
                            [start, start + 1, start + 2]
                            if winding > 0
                            else [start, start + 2, start + 1]
                        )
                    if depth:
                        for ring in rings:
                            for a, b in zip(ring.coords, list(ring.coords)[1:]):
                                start = len(vertices)
                                vertices.extend(
                                    [
                                        [a[0], a[1], polygon_base],
                                        [b[0], b[1], polygon_base],
                                        [b[0], b[1], top],
                                        [a[0], a[1], top],
                                    ]
                                )
                                faces.append([start, start + 1, start + 2, start + 3])
                lines.extend(
                    [
                        [
                            [
                                x,
                                y,
                                (ground(x, y) if base == 0 else base)
                                + (
                                    height + 0.008
                                    if height
                                    else 0.02
                                    if feature.kind == "building"
                                    else 0.065
                                    if feature.kind == "road"
                                    else 0.01
                                    if feature.kind == "water"
                                    else 0.035
                                ),
                            ]
                            for x, y, *_ in ring.coords
                        ]
                        for ring in rings
                    ]
                )
            elif isinstance(part, LineString):
                lines.append(
                    [
                        [
                            point[0],
                            point[1],
                            (ground(point[0], point[1]) if base == 0 else base) + 0.035,
                        ]
                        for point in part.coords
                    ]
                )
        meshes.append(
            compact_mesh(
                {
                    "id": feature.feature_id,
                    "kind": feature.kind,
                    "vertices": vertices,
                    "faces": faces,
                    "lines": lines,
                    "height_status": feature.height_status,
                    "height_m": height,
                    "footprint_rings": footprint_rings,
                }
            )
        )
    return meshes


def flat_building_surfaces(meshes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coalesce coincident display roofs; preserve their union and holes.

    CAD may contain several coincident outlines of the same unmeasured roof.
    Separate coplanar Cycles faces shadow one another. This is an appearance
    mesh only; all original feature meshes and IDs remain in the packet.
    """
    from shapely import union_all

    grouped: dict[float, list[Polygon]] = {}
    for row in meshes:
        if row["kind"] != "building" or row["height_m"]:
            continue
        for face in row["faces"]:
            vertices = [row["vertices"][i] for i in face]
            grouped.setdefault(vertices[0][2], []).append(
                Polygon([v[:2] for v in vertices])
            )
    result = []
    for elevation, polygons in grouped.items():
        geometry = union_all(polygons)
        if not geometry.is_valid:
            raise ValueError("Невалидная объединённая поверхность отображения зданий")
        parts = (
            list(geometry.geoms)
            if isinstance(geometry, BaseMultipartGeometry)
            else [geometry]
        )
        vertices = []
        faces = []
        for part in parts:
            if not isinstance(part, Polygon):
                continue
            for member in constrained_delaunay_triangles(part).geoms:
                triangle = cast(Polygon, member)
                points = [
                    [x, y, elevation] for x, y, *_ in list(triangle.exterior.coords)[:3]
                ]
                start = len(vertices)
                vertices.extend(points)
                a, b, c = points
                winding = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                faces.append(
                    [start, start + 1, start + 2]
                    if winding > 0
                    else [start, start + 2, start + 1]
                )
        result.append(
            compact_mesh(
                {"vertices": vertices, "faces": faces, "elevation_m": elevation}
            )
        )
    return result
