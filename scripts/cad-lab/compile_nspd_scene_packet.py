#!/usr/bin/env python3
"""Compile a deterministic street-level scene from reviewed external evidence.

Horizontal geometry comes from the NSPD/Overture/KartaView surface packet.  The
DXF topography contributes only a stable broad grade fitted to source elevation
annotations; no DXF plan geometry is admitted into this scene.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SURFACES = ROOT / ".runtime/nspd-surface-trace-kustanayskaya-20260920/surface-packet.json"
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
DEFAULT_ALIGNMENT = ROOT / ".runtime/deterministic-render-audit-20260919/candidate-osm-alignment.json"
DEFAULT_TERRAIN = ROOT / ".runtime/full-surface-terrain-20260920/terrain.json"
DEFAULT_OUTPUT = ROOT / ".runtime/nspd-scene-kustanayskaya-20260920"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surfaces", type=Path, default=DEFAULT_SURFACES)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--alignment", type=Path, default=DEFAULT_ALIGNMENT)
    parser.add_argument("--terrain", type=Path, default=DEFAULT_TERRAIN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--camera-sequence-index", type=int, default=13)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parts(geometry: Any) -> Iterable[Any]:
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for item in geometry.geoms:
            yield from parts(item)


def rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [polygon[0] for polygon in geometry["coordinates"]]
    return []


def main() -> None:
    args = parse_args()
    surfaces = json.loads(args.surfaces.read_text())
    world = json.loads(args.world.read_text())
    alignment = json.loads(args.alignment.read_text())
    terrain = json.loads(args.terrain.read_text())

    if surfaces["source"]["world_sha256"] != sha256(args.world):
        raise ValueError("surface packet and world hash differ")
    if surfaces["admission"]["status"] != "candidate_pending_overlay_review":
        raise ValueError("unexpected surface admission state")

    camera_source = next(
        (row for row in surfaces["kartaview_cameras"] if row["sequence_index"] == args.camera_sequence_index),
        None,
    )
    if camera_source is None:
        raise ValueError(f"camera sequence index {args.camera_sequence_index} not present")

    lon0, lat0 = map(float, world["coordinate_frame"]["origin_wgs84_lon_lat"])
    earth_radius = float(world["coordinate_frame"]["earth_radius_m"])
    reference_lon, reference_lat = map(float, alignment["projection_before_fit"]["reference_lon_lat"])
    similarity = alignment["similarity_transform_row_vector"]
    scale = float(similarity["scale"])
    rotation = similarity["rotation"]
    translation = similarity["translation"]
    grade = terrain["rules"]["grade_plane"]
    intercept, grade_dx, grade_dy = map(float, grade["coefficients_z_intercept_dx_dy"])
    grade_origin_x, grade_origin_y = map(float, grade["origin_xy"])

    def local_to_dxf(x: float, y: float) -> tuple[float, float]:
        lon = lon0 + math.degrees(x / (earth_radius * math.cos(math.radians(lat0))))
        lat = lat0 + math.degrees(y / earth_radius)
        tangent_x = math.radians(lon - reference_lon) * earth_radius * math.cos(math.radians(reference_lat))
        tangent_y = math.radians(lat - reference_lat) * earth_radius
        return (
            scale * (tangent_x * rotation[0][0] + tangent_y * rotation[1][0]) + translation[0],
            scale * (tangent_x * rotation[0][1] + tangent_y * rotation[1][1]) + translation[1],
        )

    def absolute_grade(x: float, y: float) -> float:
        dxf_x, dxf_y = local_to_dxf(x, y)
        return intercept + grade_dx * (dxf_x - grade_origin_x) + grade_dy * (dxf_y - grade_origin_y)

    scene_origin_xy = [float(value) for value in camera_source["local_xy_m"]]
    scene_origin_z = absolute_grade(*scene_origin_xy)

    def scene_xyz(x: float, y: float, offset: float = 0.0) -> list[float]:
        return [
            round(x - scene_origin_xy[0], 6),
            round(y - scene_origin_xy[1], 6),
            round(absolute_grade(x, y) - scene_origin_z + offset, 6),
        ]

    def triangulate_geometry(geometry: dict[str, Any], offset: float) -> tuple[list[list[list[float]]], float, float]:
        source = shape(geometry)
        triangles: list[list[list[float]]] = []
        triangulated_area = 0.0
        for polygon in parts(source):
            collection = constrained_delaunay_triangles(polygon)
            for triangle in collection.geoms:
                if not polygon.covers(triangle.representative_point()):
                    continue
                coords = list(triangle.exterior.coords)[:3]
                triangles.append([scene_xyz(float(x), float(y), offset) for x, y in coords])
                triangulated_area += triangle.area
        return triangles, source.area, triangulated_area

    offsets = {"road": 0.0, "parking_apron": 0.015, "sidewalk": 0.145, "lawn": 0.12}
    scene_surfaces = []
    road_feature = next(row for row in surfaces["referenced_world_features"] if row["semantic"] == "road")
    source_features = [
        {
            "id": road_feature["id"],
            "semantic": "road",
            "resolved_geometry_local": road_feature["geometry_local"],
            "review": {
                "status": road_feature["admission"],
                "horizontal_uncertainty_m": 0.75,
                "limitation": "Width is independently satellite-corroborated; curb line remains an appearance proxy.",
            },
        },
        *surfaces["traced_surfaces"],
    ]
    for feature in source_features:
        semantic = feature["semantic"]
        triangles, source_area, triangle_area = triangulate_geometry(
            feature["resolved_geometry_local"], offsets[semantic]
        )
        error = abs(source_area - triangle_area)
        if error > max(1e-5, source_area * 1e-8):
            raise ValueError(f"triangulation area mismatch for {feature['id']}: {error}")
        scene_surfaces.append(
            {
                "id": feature["id"],
                "semantic": semantic,
                "triangles_scene_xyz_m": triangles,
                "source_area_m2": round(source_area, 6),
                "triangulated_area_m2": round(triangle_area, 6),
                "z_offset_m": offsets[semantic],
                "review": feature["review"],
            }
        )

    # Curb segments follow the independently corroborated road edge.  Segments
    # created only by the ROI clip and the reviewed driveway opening are omitted.
    road = shape(road_feature["geometry_local"])
    roi = shape(surfaces["scene_roi"]["geometry_local"])
    parking = shape(next(
        row["resolved_geometry_local"]
        for row in surfaces["traced_surfaces"]
        if row["semantic"] == "parking_apron"
    ))
    curb_segments = []
    for polygon in parts(road):
        coordinates = list(polygon.exterior.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            segment = LineString([start, end])
            midpoint = segment.interpolate(0.5, normalized=True)
            if segment.length < 0.25:
                continue
            if midpoint.distance(roi.boundary) < 0.20:
                continue
            if midpoint.distance(parking) < 0.80:
                continue
            curb_segments.append(
                {
                    "start_scene_xyz_m": scene_xyz(*start, offsets["road"]),
                    "end_scene_xyz_m": scene_xyz(*end, offsets["road"]),
                    "width_m": 0.18,
                    "height_m": 0.145,
                    "status": "edge_from_satellite_width_road; dimensions_render_proxy",
                }
            )

    source_road = next(row for row in world["roads"] if row["id"] == road_feature["id"])
    road_centerline = shape(source_road["centerline_geometry_local"]).intersection(roi)
    road_markings = []
    for offset_m in (-0.13, 0.13):
        offset_line = road_centerline.offset_curve(offset_m)
        for item in getattr(offset_line, "geoms", [offset_line]):
            coordinates = list(item.coords)
            if len(coordinates) < 2:
                continue
            road_markings.append(
                {
                    "kind": "double_center_solid",
                    "points_scene_xyz_m": [scene_xyz(float(x), float(y), 0.022) for x, y in coordinates],
                    "width_m": 0.09,
                    "status": "axis source-backed; double-line layout appearance-confirmed by KartaView; exact paint width proxy",
                }
            )

    context_region = roi.buffer(120.0)
    context = context_region.envelope
    context_ring = list(context.exterior.coords)[:4]
    context_ground = {
        "triangles_scene_xyz_m": [
            [scene_xyz(*context_ring[index], -0.03) for index in (0, 1, 2)],
            [scene_xyz(*context_ring[index], -0.03) for index in (0, 2, 3)],
        ],
        "status": "source_tied_grade; surface semantic unresolved outside reviewed polygons",
    }

    # Automatic cartographic context.  Reviewed local surfaces form the override
    # mask; every other Overture/OSM feature is selected by bbox and retains its
    # geometry/width status instead of disappearing from the render.
    authority = unary_union(
        [road]
        + [shape(row["resolved_geometry_local"]) for row in surfaces["traced_surfaces"]]
    )
    automatic_surfaces = []
    automatic_road_geometries = []
    for feature in world["roads"]:
        if feature["id"] == road_feature["id"]:
            continue
        geometry = shape(feature["surface_geometry_local"]).intersection(context_region)
        if not geometry.is_empty and geometry.intersects(roi):
            geometry = geometry.difference(authority)
        if geometry.is_empty or geometry.area <= 0.01:
            continue
        road_class = str(feature["properties"].get("class") or "unknown")
        semantic = "context_sidewalk" if road_class in {"footway", "path", "steps", "pedestrian"} else "context_road"
        z_offset = 0.13 if semantic == "context_sidewalk" else 0.006
        triangles, source_area, triangle_area = triangulate_geometry(mapping(geometry), z_offset)
        automatic_surfaces.append(
            {
                "id": feature["id"],
                "semantic": semantic,
                "source_class": road_class,
                "triangles_scene_xyz_m": triangles,
                "source_area_m2": round(source_area, 6),
                "triangulated_area_m2": round(triangle_area, 6),
                "geometry_status": feature["properties"].get("width_status"),
                "source": feature["properties"].get("width_source"),
            }
        )
        automatic_road_geometries.append(geometry)

    road_union = unary_union(automatic_road_geometries) if automatic_road_geometries else None
    green_masks = [authority]
    if road_union is not None:
        green_masks.append(road_union)
    green_mask = unary_union(green_masks)
    for feature in world["green_areas"]:
        geometry = shape(feature["geometry_local"]).intersection(context_region).difference(green_mask)
        if geometry.is_empty or geometry.area <= 0.01:
            continue
        source_class = str(feature["properties"].get("class") or "green")
        semantic = {
            "wood": "context_wood",
            "playground": "context_playground",
            "pitch": "context_pitch",
        }.get(source_class, "context_green")
        triangles, source_area, triangle_area = triangulate_geometry(mapping(geometry), 0.10)
        automatic_surfaces.append(
            {
                "id": feature["id"],
                "semantic": semantic,
                "source_class": source_class,
                "triangles_scene_xyz_m": triangles,
                "source_area_m2": round(source_area, 6),
                "triangulated_area_m2": round(triangle_area, 6),
                "geometry_status": feature["properties"].get("geometry_status"),
                "source": feature["properties"].get("source_tag"),
            }
        )

    automatic_infrastructure = []
    for feature in world["infrastructure"]:
        geometry = shape(feature["geometry_local"])
        if not geometry.intersects(context_region):
            continue
        properties = feature["properties"]
        row = {
            "id": feature["id"],
            "class": properties.get("class"),
            "placement_status": properties.get("placement_status"),
            "dimensions_status": properties.get("dimensions_status"),
            "source_tags": properties.get("source_tags"),
        }
        if geometry.geom_type == "Point":
            row["anchor_scene_xyz_m"] = scene_xyz(geometry.x, geometry.y, 0.14)
        elif geometry.geom_type == "LineString":
            midpoint = geometry.interpolate(0.5, normalized=True)
            row["anchor_scene_xyz_m"] = scene_xyz(midpoint.x, midpoint.y, 0.14)
            row["line_scene_xyz_m"] = [scene_xyz(float(x), float(y), 0.14) for x, y in geometry.coords]
        elif geometry.geom_type == "Polygon":
            midpoint = geometry.representative_point()
            row["anchor_scene_xyz_m"] = scene_xyz(midpoint.x, midpoint.y, 0.14)
            row["line_scene_xyz_m"] = [scene_xyz(float(x), float(y), 0.14) for x, y in geometry.exterior.coords]
        else:
            midpoint = geometry.representative_point()
            row["anchor_scene_xyz_m"] = scene_xyz(midpoint.x, midpoint.y, 0.14)
        automatic_infrastructure.append(row)
    buildings = []
    unresolved_building_footprints = []
    primary_id = next(row["id"] for row in surfaces["referenced_world_features"] if row["semantic"] == "building")
    for feature in world["buildings"]:
        geometry = shape(feature["geometry_local"])
        properties = feature["properties"]
        height = properties.get("height_m")
        if not geometry.intersects(context_region):
            continue
        local_rings = []
        for ring in rings(feature["geometry_local"]):
            points = ring[:-1] if ring and ring[0][:2] == ring[-1][:2] else ring
            local_rings.append([scene_xyz(float(x), float(y), 0.12) for x, y in points])
        if height is None:
            unresolved_building_footprints.append(
                {
                    "id": feature["id"],
                    "rings_scene_xyz_m": local_rings,
                    "class": properties.get("class"),
                    "height_status": properties.get("height_status"),
                    "status": "footprint_preserved_height_unknown_not_extruded",
                    "source": properties.get("sources"),
                }
            )
            continue
        if geometry.distance(roi) > 100.0:
            continue
        buildings.append(
            {
                "id": feature["id"],
                "rings_scene_xyz_m": local_rings,
                "height_m": float(height),
                "class": properties.get("class"),
                "height_status": properties.get("height_status"),
                "is_primary_retail": feature["id"] == primary_id,
                "appearance_evidence": properties.get("height_evidence"),
            }
        )

    vegetation = []
    for candidate in surfaces["vegetation_candidates"]:
        x, y = map(float, candidate["local_xy_m"])
        vegetation.append(
            {
                **candidate,
                "scene_xyz_m": scene_xyz(x, y, offsets["lawn"]),
            }
        )

    building_geometry = shape(next(
        row["geometry_local"] for row in surfaces["referenced_world_features"]
        if row["semantic"] == "building"
    ))
    building_centroid = building_geometry.centroid
    first_tree = Point(surfaces["vegetation_candidates"][0]["local_xy_m"])
    composition_x = (building_centroid.x + first_tree.x) * 0.5
    composition_y = (building_centroid.y + first_tree.y) * 0.5
    camera = {
        "source_photo_id": camera_source["photo_id"],
        "source_sequence_index": camera_source["sequence_index"],
        "position_scene_xyz_m": [0.0, 0.0, 1.65],
        "target_scene_xyz_m": scene_xyz(composition_x, composition_y, 3.2),
        "source_heading_deg": camera_source["heading_deg"],
        "lens_mm_proxy": 18.0,
        "sensor_width_mm": 36.0,
        "status": "XY source-backed; target is deterministic midpoint of reviewed tree and retail centroid; camera height and lens are explicit render proxies",
    }

    output = {
        "schema": "green-atlas.nspd-street-scene.v1",
        "scene_id": surfaces["scene_id"],
        "status": "deterministic_geometry_prototype_not_beauty_admitted",
        "source": {
            "surface_packet": str(args.surfaces.relative_to(ROOT)),
            "surface_packet_sha256": sha256(args.surfaces),
            "world": str(args.world.relative_to(ROOT)),
            "world_sha256": sha256(args.world),
            "alignment": str(args.alignment.relative_to(ROOT)),
            "alignment_sha256": sha256(args.alignment),
            "terrain": str(args.terrain.relative_to(ROOT)),
            "terrain_sha256": sha256(args.terrain),
        },
        "coordinate_frame": {
            "scene_origin_world_local_xy_m": scene_origin_xy,
            "scene_origin_grade_moscow_m": round(scene_origin_z, 6),
            "horizontal": "NSPD/Overture local metres; DXF plan geometry excluded",
            "vertical": "robust broad grade plane from 77 uniquely associated source elevation annotations",
            "horizontal_alignment_status": alignment["status"],
            "alignment_rmse_m": alignment["ransac"]["rmse_inliers_m"],
            "grade_plane_rmse_m": grade["rmse_m"],
            "grade_plane_max_abs_residual_m": grade["max_abs_residual_m"],
            "limitation": "Broad grade only: no authoritative TIN, curb micro-relief or survey-grade transform.",
        },
        "surfaces": scene_surfaces,
        "automatic_context_surfaces": automatic_surfaces,
        "automatic_infrastructure": automatic_infrastructure,
        "context_ground": context_ground,
        "curbs": curb_segments,
        "road_markings": road_markings,
        "buildings": buildings,
        "unresolved_building_footprints": unresolved_building_footprints,
        "vegetation": vegetation,
        "camera": camera,
        "lighting": {
            "preset": "clear_day_neutral",
            "sun_elevation_deg": 36.0,
            "sun_azimuth_deg": 138.0,
            "status": "locked_render_preset_not_weather_reconstruction",
        },
        "neural_finish": {"status": "disabled", "reason": "geometry prototype must pass visual audit first"},
    }

    args.output.mkdir(parents=True, exist_ok=True)
    packet_path = args.output / "scene-packet.json"
    packet_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    receipt = {
        "schema": "green-atlas.nspd-street-scene-receipt.v1",
        "packet_sha256": sha256(packet_path),
        "surface_count": len(scene_surfaces),
        "surface_triangle_count": sum(len(row["triangles_scene_xyz_m"]) for row in scene_surfaces),
        "curb_segment_count": len(curb_segments),
        "building_count": len(buildings),
        "unresolved_building_footprint_count": len(unresolved_building_footprints),
        "vegetation_count": len(vegetation),
        "automatic_context_surface_count": len(automatic_surfaces),
        "automatic_context_triangle_count": sum(len(row["triangles_scene_xyz_m"]) for row in automatic_surfaces),
        "automatic_infrastructure_count": len(automatic_infrastructure),
        "camera_source_photo_id": camera["source_photo_id"],
    }
    (args.output / "compile-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
