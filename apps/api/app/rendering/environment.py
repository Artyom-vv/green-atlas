"""Render-only CAD surroundings and bounded neighbouring map buildings.

The original packet, prepared geometry and project remain immutable. Layer
evidence controls appearance; an absent assignment is never guessed from an
address or silently promoted to calculation geometry.
"""

from collections import Counter
from math import hypot, radians, tan
from pathlib import Path
from typing import Any, cast

from shapely import constrained_delaunay_triangles, union_all
from shapely.geometry import MultiLineString, MultiPolygon, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely.strtree import STRtree

from app.rendering.building_heights import CLASS_HEIGHTS, footprint_candidate
from app.rendering.curb_geometry import curb_envelopes
from app.rendering.curb_semantics import (
    projected_curb_line,
    suppress_native_curb_graphics,
)
from app.rendering.facade_geometry import prepare_facades
from app.rendering.georeference import RenderGeoreferenceError, render_frame
from app.rendering.location_source import location_buildings
from app.rendering.osm import fetch_buildings, parse_height, prepare_candidates
from app.rendering.packet import compact_mesh, display_meshes
from app.scene.contracts import SceneContextFeature, SceneSnapshot


def _surface_mesh(
    polygons: list[Polygon], elevation: float, role: str
) -> dict[str, Any]:
    geometry = union_all(polygons)
    if not geometry.is_valid:
        raise ValueError(f"Невалидное покрытие окружения: {role}")
    parts = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
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
    return {
        "kind": role,
        "vertices": vertices,
        "faces": faces,
        "area_m2": geometry.area,
    }


def _curb_aliases(
    rows: list[dict[str, Any]],
    source_lines: dict[str, list[list[list[float]]]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Give a whole longer display curb priority over a covered short glyph.

    No source coordinates are snapped, repaired or deleted. Partial overlaps
    and differing elevations remain separate; suppressed source IDs are kept.
    """
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    varying_bases = set()
    whole_geometries = {}
    for handle, chains in (source_lines or {}).items():
        whole_geometries[handle] = MultiLineString(
            [[p[:2] for p in chain] for chain in chains if len(chain) >= 2]
        )
        if len({p[2] for chain in chains for p in chain}) > 1:
            varying_bases.add(handle)
    for row in rows:
        a, b = row["start_scene_xyz_m"], row["end_scene_xyz_m"]
        if abs(a[2] - b[2]) > 1e-8:
            varying_bases.add(row["source_handle"])
            continue
        key = (
            row["source_handle"],
            a[2],
            row["width_m"],
            row["height_m"],
            row["curb_class"],
        )
        groups.setdefault(key, []).append(row)
    keys = list(groups)
    varying_bases.update(
        handle for handle, count in Counter(key[0] for key in keys).items() if count > 1
    )
    geometries = [
        MultiLineString(
            [
                [r["start_scene_xyz_m"][:2], r["end_scene_xyz_m"][:2]]
                for r in groups[key]
            ]
        )
        for key in keys
    ]
    index = STRtree(geometries)
    suppressed = set()
    aliases = []
    for i in sorted(
        range(len(keys)), key=lambda i: (-geometries[i].length, str(keys[i]))
    ):
        geometry, key = geometries[i], keys[i]
        whole = whole_geometries.get(key[0], geometry)
        if whole.length > 1 or whole.length <= 0 or key[0] in varying_bases:
            continue
        tolerance = key[2] * 0.25
        candidates = sorted(
            (int(j) for j in index.query(geometry.buffer(tolerance)) if int(j) != i),
            key=lambda j: (-geometries[j].length, str(keys[j])),
        )
        for j in candidates:
            other = keys[j]
            if (
                other in suppressed
                or other[0] in varying_bases
                or other[1:] != key[1:]
                or geometries[j].length < whole.length * 2
            ):
                continue
            if geometries[j].buffer(tolerance).covers(whole):
                suppressed.add(key)
                aliases.append(
                    {
                        "source_handle": key[0],
                        "retained_source_handle": other[0],
                        "reason": "whole_short_glyph_covered_by_longer_display_curb",
                        "centerline_tolerance_m": tolerance,
                        "source_length_m": whole.length,
                        "status": "appearance_only_source_geometry_preserved",
                    }
                )
                break
    retained = [
        r
        for r in rows
        if r["source_handle"] in varying_bases
        or (
            r["source_handle"],
            r["start_scene_xyz_m"][2],
            r["width_m"],
            r["height_m"],
            r["curb_class"],
        )
        not in suppressed
    ]
    return retained, aliases


def _neighbours(
    packet: dict[str, Any], directory: Path, mask: Any, *, allow_network: bool = True
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    snapshot = SceneSnapshot.model_validate(packet["scene"])
    query: dict[str, Any] = {
        "status": "unaligned",
        "reason": "Нет привязки CAD к карте",
    }
    try:
        frame = render_frame(snapshot, directory)
    except RenderGeoreferenceError as error:
        return [], {"status": "invalid_georeference", "reason": str(error)}
    if frame is None:
        return [], query
    selected = location_buildings(snapshot, directory, frame)
    if selected is None and not allow_network:
        return [], {
            "status": "not_loaded",
            "reason": "Карта загружается в местоположении проекта",
        }
    # A bounded view region is sufficient for the native image. Candidate fits
    # cannot be extrapolated outside their declared control area.
    camera = packet["camera"]
    cx, cy = camera["position"][0], -camera["position"][2]
    ox, oy = snapshot.coordinate_origin
    extent = [cx + ox - 450, cy + oy - 450, cx + ox + 450, cy + oy + 450]
    if frame.valid_extent_cad_xy is not None:
        limit = frame.valid_extent_cad_xy
        extent = [
            max(extent[0], limit[0]),
            max(extent[1], limit[1]),
            min(extent[2], limit[2]),
            min(extent[3], limit[3]),
        ]
    if extent[0] >= extent[2] or extent[1] >= extent[3]:
        return [], {
            "status": "outside_extent",
            "reason": "Ракурс вне области привязки",
            "georeference": frame.metadata,
        }
    corners = [
        frame.cad_to_wgs84(x, y)
        for x, y in [
            (extent[0], extent[1]),
            (extent[0], extent[3]),
            (extent[2], extent[1]),
            (extent[2], extent[3]),
        ]
    ]
    bbox = (
        min(p[0] for p in corners),
        min(p[1] for p in corners),
        max(p[0] for p in corners),
        max(p[1] for p in corners),
    )

    def project_point(lon: Any, lat: Any, *_: Any) -> tuple[float, float]:
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            raise TypeError("Scalar coordinate projection")
        return frame.wgs84_to_local(lon, lat)

    try:
        data = (
            selected
            if selected is not None
            else fetch_buildings(bbox, directory / "osm-cache")
        )
        converted = []
        outside = []
        outside_view = []
        view = box(extent[0] - ox, extent[1] - oy, extent[2] - ox, extent[3] - oy)
        for row in data["features"]:
            try:
                geometry = transform(project_point, shape(row["geometry"]))
            except RenderGeoreferenceError:
                outside.append(row["id"])
                continue
            if selected is not None and not geometry.intersects(view):
                outside_view.append(row["id"])
                continue
            converted.append(
                {
                    "id": row["id"],
                    "geometry": mapping(geometry),
                    "tags": row["properties"]["tags"],
                }
            )
        candidates = prepare_candidates(converted)
    except (ValueError, OSError) as error:
        return [], {
            "status": "unavailable",
            "reason": str(error),
            "georeference": frame.metadata,
        }
    features = []
    envelopes = []
    excluded = []
    for index, (row, polygon) in enumerate(zip(candidates.rows, candidates.geometries)):
        if not footprint_candidate(polygon):
            excluded.append(
                {"id": row["id"], "reason": "small_or_narrow_cartographic_footprint"}
            )
            continue
        # Whole external houses are admitted only outside CAD evidence. Never
        # trim a conflicting footprint into a plausible-looking partial house.
        if not mask.is_empty and polygon.intersection(mask).area > 1e-6:
            excluded.append({"id": row["id"], "reason": "overlaps_authoritative_CAD"})
            continue
        overlaps = [
            int(i)
            for i in candidates.tree.query(polygon)
            if int(i) != index
            and polygon.intersection(candidates.geometries[int(i)]).area > 1e-6
        ]
        if any(not polygon.equals(candidates.geometries[i]) for i in overlaps):
            excluded.append({"id": row["id"], "reason": "ambiguous_map_overlap"})
            continue
        duplicate = next(
            (
                i
                for i in overlaps
                if i < index and polygon.equals(candidates.geometries[i])
            ),
            None,
        )
        if duplicate is not None:
            excluded.append(
                {
                    "id": row["id"],
                    "reason": "duplicate_map_geometry",
                    "duplicate_of": candidates.rows[duplicate]["id"],
                }
            )
            continue
        height, source = parse_height(row["tags"])
        building_class = row["tags"].get("building", "building")
        height = height or CLASS_HEIGHTS.get(building_class, 6.0)
        features.append(
            {
                "osm_id": row["id"],
                "geometry": mapping(polygon),
                "osm_tags": row["tags"],
                "height_m": height,
                "height_source": source or "render_class_proxy",
                "height_status": "estimated",
                "building_class": building_class,
            }
        )
        envelopes.append(
            SceneContextFeature(
                feature_id=f"environment:{row['id']}",
                kind="building",
                geometry=mapping(polygon),
                height_m=height,
                height_status="estimated",
            )
        )
    meshes = display_meshes(snapshot.model_copy(update={"context_features": envelopes}))
    for mesh, row in zip(meshes, features):
        mesh["building_class"] = row["building_class"]
    return meshes, {
        "status": "loaded",
        "provenance": data["provenance"],
        "georeference": frame.metadata,
        "query_extent_cad_xy": extent,
        "features": features,
        "excluded": excluded,
        "outside_transform_extent": outside,
        "outside_view": outside_view,
        "rejected_geometry": data.get("rejected", []),
        "policy": "whole neighbour footprints outside CAD surfaces and buildings; never used for calculations",
    }


def prepare_environment(packet: dict[str, Any], directory: Path) -> dict[str, Any]:
    result = dict(packet)
    inputs = packet.get("environment_inputs", {})
    roles = inputs.get("roles", {})
    evidence = {r["feature_id"]: r for r in inputs.get("role_evidence", [])}
    camera = packet["camera"]
    cx, cy = camera["position"][0], -camera["position"][2]
    # Keep distant chains in the immutable input. Image-only view culling has
    # an explicit count; it does not change source coverage or CAD remarks.
    radius = max(
        200.0,
        min(
            1200.0,
            abs(camera["position"][1])
            * tan(radians(camera.get("fov_y_deg", 42) / 2))
            * max(1, camera.get("aspect", 1))
            * 2.5,
        ),
    )
    view = box(cx - radius, cy - radius, cx + radius, cy + radius)
    grouped: dict[tuple[str, float, float], list[Polygon]] = {}
    replaced = []
    mask_parts: list[BaseGeometry] = []
    curb_rows = []
    curb_source_lines = {}
    curb_keys = set()
    culled_curbs = 0
    # Surface elevations contain rendering lifts, not a change in CAD grade.
    # Match the original base before applying any display-only precedence.
    lawns_by_base: dict[float, list[Polygon]] = {}
    for row in packet["meshes"]:
        role = roles.get(row["id"], row["kind"])
        if row["kind"] not in {"road", "lawn"} or role not in {"lawn", "sidewalk"}:
            continue
        lift = 0.055 if row["kind"] == "road" else 0.012
        for face in row["faces"]:
            if len(face) != 3:
                continue
            points = [row["vertices"][i] for i in face]
            polygon = Polygon([point[:2] for point in points])
            if polygon.area == 0:
                continue
            base = round(points[0][2] - lift, 9)
            if role == "lawn":
                lawns_by_base.setdefault(base, []).append(polygon)
    lawn_masks = {base: union_all(rows) for base, rows in lawns_by_base.items()}
    projected_curbs = []
    for row in packet["meshes"]:
        if row["kind"] not in {"road", "lawn"}:
            continue
        replaced.append(row["id"])
        role = roles.get(row["id"], row["kind"])
        detail = evidence.get(row["id"], {})
        # Render packets deliberately omit context_features. Reconstruct the
        # accepted footprint from their unchanged source triangles, including
        # holes, rather than treating missing context as an unrestricted slab.
        source_geometry = (
            union_all(
                [
                    Polygon([row["vertices"][i][:2] for i in face])
                    for face in row["faces"]
                    if len(face) == 3
                ]
            )
            if role == "curb" and row["faces"]
            else None
        )
        lift = 0.055 if row["kind"] == "road" else 0.012
        if source_geometry is not None and row["vertices"]:
            base = round(row["vertices"][0][2] - lift, 9)
            lawn = lawn_masks.get(base)
            detail = {
                **detail,
                "lawn_boundary_evidence": bool(
                    lawn is not None
                    and source_geometry.area > 0
                    and source_geometry.intersection(lawn).area
                    >= source_geometry.area * 0.8
                ),
            }
        boundary_only = role == "curb" and projected_curb_line(detail, source_geometry)
        if boundary_only:
            projected_curbs.append(
                {
                    "source_handle": row["id"],
                    "source_entity_type": detail.get("source_entity_type"),
                    "interpretation": "native_projected_closed_line_perimeter",
                    "status": "appearance_only_source_geometry_preserved",
                    "width_m": 0.15,
                    "height_m": 0.18,
                }
            )
        for face in row["faces"]:
            if len(face) != 3:
                continue
            points = [row["vertices"][i] for i in face]
            polygon = Polygon([p[:2] for p in points])
            if polygon.area == 0:
                continue
            elevation = points[0][2] + (
                0.025 if role == "sidewalk" else 0.125 if role == "curb" else 0
            )
            mask_parts.append(polygon)
            if not boundary_only:
                base = round(points[0][2] - lift, 9)
                grouped.setdefault((role, elevation, base), []).append(polygon)
        if role == "curb":
            # Polygon outlines already show both sides of a curb footprint;
            # native projected closed lines describe a perimeter, not a slab.
            if not boundary_only and detail.get("geometry_type") not in {
                "LineString",
                "MultiLineString",
            }:
                continue
            curb_source_lines[row["id"]] = row["lines"]
            for chain in row["lines"]:
                for start, end in zip(chain, chain[1:]):
                    if hypot(end[0] - start[0], end[1] - start[1]) < 1e-8:
                        continue
                    segment = shape(
                        {"type": "LineString", "coordinates": [start[:2], end[:2]]}
                    )
                    if not segment.intersects(view):
                        culled_curbs += 1
                        continue
                    line_lift = (
                        (0.065 if row["kind"] == "road" else 0.035)
                        if boundary_only
                        else 0.035
                    )
                    a, b = (
                        [start[0], start[1], start[2] - line_lift],
                        [end[0], end[1], end[2] - line_lift],
                    )
                    key = tuple(sorted((tuple(a), tuple(b))))
                    if key in curb_keys:
                        continue
                    curb_keys.add(key)
                    curb_rows.append(
                        {
                            "start_scene_xyz_m": a,
                            "end_scene_xyz_m": b,
                            "source_handle": row["id"],
                            "curb_class": "illustrative_CAD_line_curb",
                            "width_m": 0.15,
                            "height_m": 0.18,
                        }
                    )
    raw_curb_count = len(curb_rows)
    native_curb_lines = {
        identifier: [detail["source_native_curve_scene_xyz_m"]]
        if "source_native_curve_scene_xyz_m" in detail
        else []
        for identifier, detail in evidence.items()
        if roles.get(identifier) == "curb"
    }
    curb_rows, graphic_interpretations, symbol_evidence = suppress_native_curb_graphics(
        curb_rows, native_curb_lines, evidence
    )
    # Regular disconnected native block strokes are 2D graphics. Their
    # interpretation precedes both volume construction and view-only aliases.
    curb_rows, curb_aliases = _curb_aliases(curb_rows, curb_source_lines)
    # Bound the geometry that will actually be extruded. Native 2D strokes
    # and covered aliases do not consume the physical-curb mesh budget.
    if (
        len(curb_rows) > 60000
        or sum(
            hypot(
                r["end_scene_xyz_m"][0] - r["start_scene_xyz_m"][0],
                r["end_scene_xyz_m"][1] - r["start_scene_xyz_m"][1],
            )
            for r in curb_rows
        )
        > 100000
    ):
        raise ValueError(
            "Слишком много бордюров в выбранном кадре; выберите ближний ракурс"
        )
    curb_meshes, curb_tops, curb_composition = curb_envelopes(curb_rows)
    curb_composition["projected_closed_line_perimeters"] = projected_curbs
    curb_composition["native_graphic_interpretation"] = symbol_evidence
    surfaces = []
    removed_coplanar_area = 0.0
    removed_hard_cover: dict[str, float] = {}
    for (role, z, base), polygons in grouped.items():
        if role in {"road", "sidewalk"}:
            lawn = lawn_masks.get(base)
            if lawn is not None:
                original = union_all(polygons)
                visible = original.difference(lawn)
                removed_hard_cover[role] = removed_hard_cover.get(role, 0.0) + (
                    original.area - visible.area
                )
                if visible.is_empty:
                    continue
                polygons = cast(
                    list[Polygon],
                    list(visible.geoms)
                    if isinstance(visible, MultiPolygon)
                    else [visible],
                )
        surface = _surface_mesh(polygons, z, role)
        if role == "curb":
            geometry = union_all(polygons)
            top_masks = [footprint for top, footprint in curb_tops if top == z]
            if top_masks:
                visible = geometry.difference(union_all(top_masks))
                removed_coplanar_area += geometry.area - visible.area
                if visible.is_empty:
                    continue
                parts = (
                    list(visible.geoms)
                    if isinstance(visible, MultiPolygon)
                    else [visible]
                )
                surface = _surface_mesh(cast(list[Polygon], parts), z, role)
        surfaces.append(surface)
    curb_composition["removed_coplanar_polygon_area_m2"] = removed_coplanar_area
    curb_composition["surface_precedence"] = {
        "policy": "accepted lawn excludes road and sidewalk fill at the same CAD base; source geometry preserved",
        "removed_hard_cover_area_m2": dict(removed_hard_cover),
    }
    for row in packet.get("building_inputs", []):
        geometry = shape(row["geometry"])
        if footprint_candidate(geometry) or row.get("height_m"):
            mask_parts.append(geometry)
    neighbours, map_evidence = _neighbours(packet, directory, union_all(mask_parts))
    cad_buildings, cad_facades, cad_facade_evidence = prepare_facades(
        packet.get("building_display_meshes", []), "cad_building"
    )
    neighbours, neighbour_facades, neighbour_facade_evidence = prepare_facades(
        neighbours, "map_building"
    )
    facade_evidence = [*cad_facade_evidence, *neighbour_facade_evidence]
    result["building_display_meshes"] = cad_buildings
    result["building_facade_meshes"] = [*cad_facades, *neighbour_facades]
    result["building_facade_details"] = {
        "windows": sum(row["windows"] for row in facade_evidence),
        "entries": sum(row["entries"] for row in facade_evidence),
        "facade_joints": sum(row["facade_joints"] for row in facade_evidence),
        "buildings": facade_evidence,
        "status": "shared frozen illustrative facade geometry; source class and height unchanged",
    }
    markers = inputs.get("existing_tree_markers", [])
    admitted_markers = [
        r
        for r in markers
        if hypot(r["local_x"] - cx, r["local_y"] - cy) <= radius + r["radius_m"]
    ]
    if len(admitted_markers) > 3000:
        raise ValueError("Слишком много условных деревьев в выбранном кадре")
    result["environment_surfaces"] = surfaces
    result["environment_curbs"] = curb_rows
    result["environment_curb_meshes"] = curb_meshes
    result["environment_trees"] = admitted_markers
    result["environment_buildings"] = neighbours
    result["environment_replaced_ids"] = replaced
    result["environment"] = {
        "schema": "green-atlas.render-environment/1",
        "scope": "image only; unchanged source scene and prepared geometry",
        "layer_evidence": inputs.get("role_evidence", []),
        "input_metadata": inputs.get("metadata", {}),
        "omissions": inputs.get("omissions", []),
        "surface_counts": dict(Counter(r["kind"] for r in surfaces)),
        "surface_area_m2": {
            role: sum(r["area_m2"] for r in surfaces if r["kind"] == role)
            for role in {r["kind"] for r in surfaces}
        },
        "curb_segments": len(curb_rows),
        "curb_segments_before_display_priority": raw_curb_count,
        "curb_display_aliases": curb_aliases,
        "curb_graphic_interpretations": graphic_interpretations,
        "curb_composition": curb_composition,
        "curb_proxy_dimensions_m": {
            "width": 0.15,
            "height": 0.18,
            "status": "illustrative",
        },
        "existing_tree_markers": admitted_markers,
        "view_culling": {
            "radius_m": radius,
            "curb_segments_outside_view": culled_curbs,
            "tree_markers_outside_view": len(markers) - len(admitted_markers),
        },
        "map_buildings": map_evidence,
    }
    # Index exact repeated XYZ inside each derived mesh. Keep face coordinates,
    # winding, material boundaries and immutable source packet untouched.
    for name in (
        "environment_surfaces",
        "environment_curb_meshes",
        "environment_buildings",
        "building_display_meshes",
        "building_facade_meshes",
    ):
        result[name] = [compact_mesh(mesh) for mesh in result[name]]
    return result
