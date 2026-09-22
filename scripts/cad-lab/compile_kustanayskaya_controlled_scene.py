#!/usr/bin/env python3
"""Compile a controlled street scene from exact project CAD and mapped context.

The project DXF owns road, sidewalk, lawn, curb and project-MAF geometry inside
the scene window.  OSM/Overture contributes building context and the named road
axis used to choose a repeatable camera.  Inventory vegetation is admitted only
when an actual marker was matched and the trunk lies on an authored lawn HATCH.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import ezdxf
from ezdxf import bbox as ezdxf_bbox
from ezdxf.path import make_path
from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, Point, Polygon, box, mapping, shape
from shapely.ops import nearest_points, transform, unary_union


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.dxf_import.adapters import _hatch_polygon_geometry  # noqa: E402
from app.dxf_import.polygons import hatch_geometry  # noqa: E402
DEFAULT_WORLD = ROOT / ".runtime/kustanayskaya-working-prototype-20260920/prototype-world.json"
DEFAULT_SURFACES = ROOT / ".runtime/kustanayskaya-target-surfaces-20260920/authored-surfaces.geojson"
DEFAULT_AUDIT = ROOT / ".runtime/kustanayskaya-target-surfaces-20260920/placement-audit/project-surface-placement-audit.json"
DEFAULT_ALIGNMENT = ROOT / ".runtime/deterministic-render-audit-20260919/candidate-osm-alignment.json"
DEFAULT_PROJECT_DXF = next((ROOT / ".runtime/kustanayskaya-mac-ready-20260917/dxf").rglob("03_10004141_Проектные решения.dxf"))
DEFAULT_MATERIAL_PACKET = ROOT / ".runtime/deterministic-render-pipeline-20260920-expanded/scene-packet.json"
DEFAULT_OUTPUT = ROOT / ".runtime/kustanayskaya-controlled-scene-20260920"
PRESENTATION_ASSET_DIR = ROOT / ".runtime/presentation-assets-20260920"
CURB_LAYERS = {
    "ДВ_Борт_БР100.30.15": ("standard", 0.15, 0.18),
    "ДВ_ГП_П_Борт_Пониженный": ("lowered", 0.04, 0.18),
    "ДВ_Борт_БР100.45.18": ("heavy", 0.18, 0.20),
}

# Missing height must never make a mapped footprint disappear: the footprint
# still blocks lawn/planting, while this bounded proxy controls visualization
# only. Exact/cartographic height always wins when present.
BUILDING_CLASS_HEIGHT_PROXY_M = {
    "apartments": 27.0,
    "retail": 9.0,
    "school": 9.0,
    "kindergarten": 7.0,
    "service": 4.2,
    "building": 6.0,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--surfaces", type=Path, default=DEFAULT_SURFACES)
    parser.add_argument("--placement-audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--alignment", type=Path, default=DEFAULT_ALIGNMENT)
    parser.add_argument("--project-dxf", type=Path, default=DEFAULT_PROJECT_DXF)
    parser.add_argument("--material-packet", type=Path, default=DEFAULT_MATERIAL_PACKET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scene-radius-m", type=float, default=230.0)
    parser.add_argument("--vegetation-radius-m", type=float, default=145.0)
    return parser.parse_args()


def polygon_parts(geometry: Any) -> Iterable[Polygon]:
    if geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygon_parts(part)


def line_parts(geometry: Any) -> Iterable[LineString]:
    if geometry.is_empty:
        return
    if geometry.geom_type == "LineString":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from line_parts(part)


def point_along(points: list[list[float]], fraction: float) -> tuple[float, float, float, float]:
    lengths = [math.dist(a[:2], b[:2]) for a, b in zip(points, points[1:])]
    target = sum(lengths) * fraction
    for (start, end), length in zip(zip(points, points[1:]), lengths):
        if length <= 1e-9:
            continue
        if target <= length:
            ratio = target / length
            dx, dy = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
            return float(start[0]) + dx * ratio, float(start[1]) + dy * ratio, dx / length, dy / length
        target -= length
    start, end = points[-2], points[-1]
    length = max(math.dist(start[:2], end[:2]), 1e-9)
    return float(end[0]), float(end[1]), (float(end[0]) - float(start[0])) / length, (float(end[1]) - float(start[1])) / length


def subdivide_triangle(coordinates: list[tuple[float, float]], max_edge_m: float = 4.0):
    stack = [coordinates]
    while stack:
        triangle = stack.pop()
        edges = [(math.dist(triangle[index], triangle[(index + 1) % 3]), index) for index in range(3)]
        length, index = max(edges)
        if length <= max_edge_m:
            yield triangle
            continue
        a, b, c = triangle[index], triangle[(index + 1) % 3], triangle[(index + 2) % 3]
        midpoint = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
        stack.extend(([a, midpoint, c], [midpoint, b, c]))


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = json.loads(args.world.read_text())
    surfaces = json.loads(args.surfaces.read_text())
    audit = json.loads(args.placement_audit.read_text())
    alignment = json.loads(args.alignment.read_text())
    material_packet = json.loads(args.material_packet.read_text())

    lon0, lat0 = map(float, world["coordinate_frame"]["origin_wgs84_lon_lat"])
    earth_radius = float(world["coordinate_frame"]["earth_radius_m"])
    transform_spec = alignment["similarity_transform_row_vector"]
    rotation = transform_spec["rotation"]
    determinant = rotation[0][0] * rotation[1][1] - rotation[0][1] * rotation[1][0]
    inverse = [
        [rotation[1][1] / determinant, -rotation[0][1] / determinant],
        [-rotation[1][0] / determinant, rotation[0][0] / determinant],
    ]
    scale = float(transform_spec["scale"])
    tx, ty = map(float, transform_spec["translation"])
    reference_lon, reference_lat = map(float, alignment["projection_before_fit"]["reference_lon_lat"])
    alignment_radius = float(alignment["projection_before_fit"]["earth_radius_m"])

    def dxf_to_world(x: float, y: float) -> tuple[float, float]:
        dx, dy = float(x) - tx, float(y) - ty
        tangent_x = (dx * inverse[0][0] + dy * inverse[1][0]) / scale
        tangent_y = (dx * inverse[0][1] + dy * inverse[1][1]) / scale
        lon = reference_lon + math.degrees(tangent_x / (alignment_radius * math.cos(math.radians(reference_lat))))
        lat = reference_lat + math.degrees(tangent_y / alignment_radius)
        return (
            earth_radius * math.radians(lon - lon0) * math.cos(math.radians(lat0)),
            earth_radius * math.radians(lat - lat0),
        )

    terrain = world["terrain"]
    rows, columns = int(terrain["rows"]), int(terrain["columns"])
    terrain_vertices = [row["xyz_local_m"] for row in terrain["vertices"]]
    xs = [float(terrain_vertices[column][0]) for column in range(columns)]
    ys = [float(terrain_vertices[row * columns][1]) for row in range(rows)]

    def terrain_z(x: float, y: float) -> float:
        fx = (x - xs[0]) / (xs[-1] - xs[0]) * (columns - 1)
        fy = (y - ys[0]) / (ys[-1] - ys[0]) * (rows - 1)
        x0 = min(max(math.floor(fx), 0), columns - 2)
        y0 = min(max(math.floor(fy), 0), rows - 2)
        dx, dy = min(max(fx - x0, 0.0), 1.0), min(max(fy - y0, 0.0), 1.0)
        values = [
            float(terrain_vertices[y0 * columns + x0][2]),
            float(terrain_vertices[y0 * columns + x0 + 1][2]),
            float(terrain_vertices[(y0 + 1) * columns + x0][2]),
            float(terrain_vertices[(y0 + 1) * columns + x0 + 1][2]),
        ]
        return (values[0] * (1 - dx) + values[1] * dx) * (1 - dy) + (values[2] * (1 - dx) + values[3] * dx) * dy

    axes = [row for row in world["named_reference_axes"] if row.get("name") == "Кустанайская улица" and row.get("project_geometry_local")]
    if not axes:
        raise RuntimeError("Kustanayskaya named reference axis is absent")
    axis = max(axes, key=lambda row: float(row.get("length_in_project_m") or 0.0))
    axis_points = axis["project_geometry_local"]["coordinates"]
    camera_source = point_along(axis_points, 0.48)
    target_source = point_along(axis_points, 0.61)
    origin_x, origin_y = camera_source[:2]
    origin_z = terrain_z(origin_x, origin_y)
    scene_window = Point(origin_x, origin_y).buffer(args.scene_radius_m)

    def scene_xyz(x: float, y: float, offset: float = 0.0) -> list[float]:
        return [round(x - origin_x, 6), round(y - origin_y, 6), round(terrain_z(x, y) - origin_z + offset, 6)]

    # OSM confirms that this mid-corridor segment carries two opposing lanes.
    # It does not survey individual paint dashes, so the centreline is an
    # explicit presentation proxy with a stable phase, never exact geometry.
    inferred_road_markings = []
    if int(float(axis.get("lanes") or 0)) == 2:
        axis_line = LineString(axis_points)
        dash_length_m, period_m = 3.0, 9.0
        cursor = 0.0
        while cursor < axis_line.length:
            start = axis_line.interpolate(cursor)
            end = axis_line.interpolate(min(cursor + dash_length_m, axis_line.length))
            midpoint = axis_line.interpolate(min(cursor + dash_length_m * 0.5, axis_line.length))
            if scene_window.covers(midpoint):
                inferred_road_markings.append({
                    "id": f"osm-lanes-centreline:{len(inferred_road_markings):03d}",
                    "points_scene_xyz_m": [
                        scene_xyz(float(start.x), float(start.y), 0.035),
                        scene_xyz(float(end.x), float(end.y), 0.035),
                    ],
                    "width_m": 0.12,
                    "status": "render_estimate_from_osm_two_opposing_lanes",
                    "authority": "cartographic_lane_count_inference",
                    "scenario_visibility": ["existing_context", "project"],
                    "source_id": axis["id"],
                    "source_lanes": float(axis["lanes"]),
                    "pattern_proxy": {"dash_m": dash_length_m, "period_m": period_m},
                })
            cursor += period_m

    stabilized_triangles = 0
    stabilized_area_m2 = 0.0

    def stable_scene_triangle(coordinates: list[tuple[float, float]], offset: float) -> list[list[float]]:
        nonlocal stabilized_triangles, stabilized_area_m2
        points = [scene_xyz(x, y, offset) for x, y in coordinates]
        a, b, c = points
        denominator = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
        slope = 0.0
        if abs(denominator) > 1e-12:
            dzdx = ((b[2] - a[2]) * (c[1] - a[1]) - (c[2] - a[2]) * (b[1] - a[1])) / denominator
            dzdy = ((b[0] - a[0]) * (c[2] - a[2]) - (c[0] - a[0]) * (b[2] - a[2])) / denominator
            slope = math.hypot(dzdx, dzdy)
        if slope > 0.055:
            average_z = round(sum(point[2] for point in points) / 3.0, 6)
            for point in points:
                point[2] = average_z
            stabilized_triangles += 1
            stabilized_area_m2 += Polygon(coordinates).area
        return points

    def dxf_geometry_to_world(geometry: Any) -> Any:
        return transform(lambda x, y, z=None: dxf_to_world(x, y), geometry)

    by_class: dict[str, list[Any]] = defaultdict(list)
    for feature in surfaces["features"]:
        semantic = feature["properties"]["class"]
        if semantic not in {"road", "sidewalk", "lawn", "special_surface", "stairs"}:
            continue
        geometry = dxf_geometry_to_world(shape(feature["geometry"])).intersection(scene_window)
        if not geometry.is_empty:
            by_class[semantic].append(geometry)

    raw = {key: unary_union(value).buffer(0) for key, value in by_class.items()}

    # Cartographic building completeness is a planting-safety constraint, not
    # merely a visual concern. Every mapped footprint removes candidate lawn
    # even when its height is unknown and it can only be rendered as a proxy.
    mapped_building_footprints = []
    for row in world["buildings"]:
        geometry = shape(row["geometry_local"]).intersection(scene_window)
        if not geometry.is_empty:
            mapped_building_footprints.append(geometry)
    mapped_building_union = (
        unary_union(mapped_building_footprints).buffer(0)
        if mapped_building_footprints else Polygon()
    )
    lawn_before_buildings_m2 = raw.get("lawn", Polygon()).area
    if "lawn" in raw and not mapped_building_union.is_empty:
        # Small tolerance prevents raster/neural grass leaking onto a facade
        # due to sub-pixel disagreement between independent map sources.
        raw["lawn"] = raw["lawn"].difference(mapped_building_union.buffer(0.15)).buffer(0)
    lawn_blocked_by_buildings_m2 = lawn_before_buildings_m2 - raw.get("lawn", Polygon()).area
    resolved: dict[str, Any] = {}
    occupied: Any = Polygon()
    for semantic in ("road", "sidewalk", "stairs", "special_surface", "lawn"):
        geometry = raw.get(semantic, Polygon()).difference(occupied).buffer(0)
        resolved[semantic] = geometry
        occupied = unary_union([occupied, geometry]).buffer(0)
    resolved["sidewalk"] = unary_union([resolved["sidewalk"], resolved["stairs"], resolved["special_surface"]]).buffer(0)

    offsets = {"road": 0.02, "sidewalk": 0.14, "lawn": 0.10}
    scene_surfaces = []
    surface_stats = {}
    for semantic in ("road", "sidewalk", "lawn"):
        geometry = resolved[semantic]
        triangles = []
        area = 0.0
        for polygon in polygon_parts(geometry):
            for candidate in constrained_delaunay_triangles(polygon).geoms:
                if not polygon.covers(candidate.representative_point()):
                    continue
                base = [(float(x), float(y)) for x, y in list(candidate.exterior.coords)[:3]]
                for triangle in subdivide_triangle(base):
                    triangles.append(stable_scene_triangle(triangle, offsets[semantic]))
                    area += Polygon(triangle).area
        if not triangles:
            continue
        scene_surfaces.append({
            "id": f"project:{semantic}",
            "semantic": semantic,
            "triangles_scene_xyz_m": triangles,
            "source_area_m2": round(geometry.area, 6),
            "triangulated_area_m2": round(area, 6),
            "z_offset_m": offsets[semantic],
            "review": {
                "status": "exact_project_hatch_xy_source_topography_z",
                "horizontal_uncertainty_m": 0.0,
                "limitation": "Surface offsets expose the curb in the diagnostic; admitted local TIN supersedes them.",
            },
        })
        surface_stats[semantic] = {"area_m2": round(geometry.area, 3), "triangles": len(triangles)}

    context_half = args.scene_radius_m + 35.0
    context_step = 10.0
    context_triangles = []
    grid_count = int(math.ceil(context_half * 2 / context_step)) + 1
    for row in range(grid_count - 1):
        for column in range(grid_count - 1):
            x0 = origin_x - context_half + column * context_step
            y0 = origin_y - context_half + row * context_step
            points = [(x0, y0), (x0 + context_step, y0), (x0 + context_step, y0 + context_step), (x0, y0 + context_step)]
            context_triangles.extend([
                [scene_xyz(*points[index], -0.02) for index in (0, 1, 2)],
                [scene_xyz(*points[index], -0.02) for index in (0, 2, 3)],
            ])

    document = ezdxf.readfile(args.project_dxf)
    modelspace = document.modelspace()
    curbs = []
    curb_counts: Counter[str] = Counter()
    for entity in modelspace.query("LWPOLYLINE"):
        specification = CURB_LAYERS.get(entity.dxf.layer)
        if specification is None:
            continue
        curb_class, height, width = specification
        source_points = [(float(point.x), float(point.y)) for point in make_path(entity).flattening(0.08)]
        if len(source_points) < 2:
            continue
        world_line = LineString([dxf_to_world(x, y) for x, y in source_points]).intersection(scene_window)
        for line in line_parts(world_line):
            coordinates = list(line.coords)
            for start, end in zip(coordinates, coordinates[1:]):
                length = math.dist(start, end)
                pieces = max(1, math.ceil(length / 3.0))
                for index in range(pieces):
                    a = index / pieces
                    b = (index + 1) / pieces
                    point_a = (start[0] * (1 - a) + end[0] * a, start[1] * (1 - a) + end[1] * a)
                    point_b = (start[0] * (1 - b) + end[0] * b, start[1] * (1 - b) + end[1] * b)
                    curbs.append({
                        "start_scene_xyz_m": scene_xyz(*point_a, offsets["road"]),
                        "end_scene_xyz_m": scene_xyz(*point_b, offsets["road"]),
                        "width_m": width,
                        "height_m": height,
                        "curb_class": curb_class,
                        "source_handle": entity.dxf.handle,
                        "status": "exact_project_polyline_xy; class_dimension_render_proxy",
                    })
                    curb_counts[curb_class] += 1

    infrastructure = []
    infrastructure_counts: Counter[str] = Counter()
    for entity in modelspace.query("INSERT"):
        item_class = None
        if entity.dxf.layer == "ДВ_ГП_П_МАФ" and entity.dxf.name == "лавочка 1":
            item_class = "bench"
        elif entity.dxf.layer == "ДВ_ГП_П_МАФ" and entity.dxf.name == "урна2":
            item_class = "waste_basket"
        elif entity.dxf.layer == "ДВ_ГП_П_Павильон_ООТ":
            item_class = "transit_shelter"
        if item_class is None:
            continue
        extents = ezdxf_bbox.extents([entity], fast=True)
        if not extents.has_data:
            continue
        centre = extents.center
        x, y = dxf_to_world(float(centre.x), float(centre.y))
        if not scene_window.covers(Point(x, y)):
            continue
        angle = math.radians(float(entity.dxf.rotation or 0.0))
        direction_point = dxf_to_world(float(centre.x) + math.cos(angle), float(centre.y) + math.sin(angle))
        rotation_world = math.atan2(direction_point[1] - y, direction_point[0] - x)
        infrastructure.append({
            "id": f"project-maf:{entity.dxf.handle}",
            "class": item_class,
            "anchor_scene_xyz_m": scene_xyz(x, y, offsets["sidewalk"]),
            "rotation_rad": round(rotation_world, 9),
            "placement_status": "exact_transformed_project_block_geometry_centre",
            "dimensions_status": "render_dimensions_proxy",
            "source_tags": {"layer": entity.dxf.layer, "block": entity.dxf.name, "handle": entity.dxf.handle},
        })
        infrastructure_counts[item_class] += 1

    marking_geometry = []
    marking_source_hatches = 0
    for insert in modelspace.query("INSERT"):
        if insert.dxf.layer != "разметка":
            continue
        for virtual in insert.virtual_entities():
            if virtual.dxftype() != "HATCH":
                continue
            extracted = hatch_geometry(virtual, 1.0, _hatch_polygon_geometry)
            if extracted is None:
                continue
            geometry = dxf_geometry_to_world(shape(extracted)).intersection(scene_window)
            if geometry.is_empty:
                continue
            marking_geometry.append(geometry)
            marking_source_hatches += 1
    marking_triangles = []
    marking_area = 0.0
    if marking_geometry:
        combined_marking = unary_union(marking_geometry).buffer(0)
        for polygon in polygon_parts(combined_marking):
            for triangle in constrained_delaunay_triangles(polygon).geoms:
                if not polygon.covers(triangle.representative_point()):
                    continue
                coordinates = [(float(x), float(y)) for x, y in list(triangle.exterior.coords)[:3]]
                marking_triangles.append(stable_scene_triangle(coordinates, offsets["road"] + 0.025))
                marking_area += triangle.area
        if marking_triangles:
            scene_surfaces.append({
                "id": "project:road-marking",
                "semantic": "marking",
                "triangles_scene_xyz_m": marking_triangles,
                "source_area_m2": round(unary_union(marking_geometry).area, 6),
                "triangulated_area_m2": round(marking_area, 6),
                "z_offset_m": offsets["road"] + 0.025,
                "review": {
                    "status": "exact_project_hatch_from_marking_insert",
                    "horizontal_uncertainty_m": 0.0,
                    "limitation": "Only authored marking HATCH is rendered; absent paint is not generated.",
                },
                "authority": "authored_project_dxf",
                "scenario_visibility": ["project"],
            })

    buildings = []
    building_skipped = Counter()
    building_height_proxies = Counter()
    for row in world["buildings"]:
        height = row["properties"].get("height_m")
        if height is None:
            building_class = row["properties"].get("class") or "building"
            height = BUILDING_CLASS_HEIGHT_PROXY_M.get(building_class, 6.0)
            height_status = "bounded_render_proxy_from_building_class"
            building_height_proxies[building_class] += 1
        else:
            building_class = row["properties"].get("class") or "building"
            height_status = row["properties"].get("height_status")
        geometry = shape(row["geometry_local"])
        if geometry.distance(Point(origin_x, origin_y)) > args.scene_radius_m + 45.0:
            building_skipped["outside_scene"] += 1
            continue
        rings_scene = []
        for polygon in polygon_parts(geometry):
            ring = list(polygon.exterior.coords)[:-1]
            rings_scene.append([scene_xyz(float(x), float(y), 0.0) for x, y in ring])
        if not rings_scene:
            continue
        buildings.append({
            "id": row["id"],
            "class": building_class,
            "height_m": float(height),
            "height_status": height_status,
            "rings_scene_xyz_m": rings_scene,
            "appearance_role": "retail" if building_class == "retail" else "context",
            "appearance_evidence": (
                {"status": "class_based_visual_proxy", "source_height": None}
                if height_status == "bounded_render_proxy_from_building_class" else None
            ),
        })

    by_inventory_id = {int(row["inventory_id"]): row for row in world["inventory_vegetation"]}
    vegetation = []
    vegetation_excluded: Counter[str] = Counter()

    def asset_species(source_species: str) -> str:
        value = source_species.casefold().replace("ё", "е")
        if "лип" in value:
            return "Tilia-europaea"
        if any(token in value for token in ("ел", "пихт", "сосн", "туя", "листвен")):
            return "Picea-abies"
        if "берез" in value:
            return "Betula-pendula"
        return "Acer-pseudoplatanus"

    for record in audit["records"]:
        source = by_inventory_id[int(record["inventory_id"])]
        if source["kind"] != "tree":
            vegetation_excluded["shrub_or_stump_without_authored_footprint"] += 1
            continue
        if source["position_status"] != "matched_marker":
            vegetation_excluded[f"position_{source['position_status']}"] += 1
            continue
        if record["project_surface"] != "lawn":
            vegetation_excluded[f"surface_{record['project_surface']}"] += 1
            continue
        height = source.get("height_m")
        if height is None or float(height) <= 0:
            vegetation_excluded["missing_height"] += 1
            continue
        x, y, _ = map(float, source["position_local_xyz_m"])
        if math.hypot(x - origin_x, y - origin_y) > args.vegetation_radius_m:
            vegetation_excluded["outside_vegetation_window"] += 1
            continue
        vegetation.append({
            "id": f"inventory:{source['inventory_id']}",
            "inventory_id": int(source["inventory_id"]),
            "source_species": source["species"],
            "asset_species": asset_species(source["species"]),
            "height_m": float(height),
            "height_status": "inventory_measured_or_authored",
            "position_status": "matched_marker_on_exact_project_lawn",
            "scene_xyz_m": scene_xyz(x, y, offsets["lawn"]),
            "condition_description": source.get("condition_description"),
        })

    # Optional street-life anchors are derived from admitted geometry and are
    # explicitly excluded from factual inventory.  Their locations are stable
    # and reviewable; the layer remains off unless the render asks for it.
    population_anchors = {"passenger_car": [], "pedestrian": []}
    camera_measure = axis_line.project(Point(origin_x, origin_y))

    def axis_frame(distance_ahead_m: float) -> tuple[Point, float, float, float]:
        measure = min(max(camera_measure + distance_ahead_m, 0.5), axis_line.length - 0.5)
        point = axis_line.interpolate(measure)
        before = axis_line.interpolate(max(0.0, measure - 0.5))
        after = axis_line.interpolate(min(axis_line.length, measure + 0.5))
        dx, dy = after.x - before.x, after.y - before.y
        length = max(math.hypot(dx, dy), 1e-9)
        return point, dx / length, dy / length, math.atan2(dy, dx)

    road_geometry = resolved.get("road", Polygon())
    car_axis, car_tx, car_ty, car_heading = axis_frame(48.0)
    car_candidate = Point(car_axis.x - car_ty * 2.65, car_axis.y + car_tx * 2.65)
    marking_clear = not marking_geometry or combined_marking.distance(car_candidate) > 5.0
    if road_geometry.covers(car_candidate) and marking_clear:
        population_anchors["passenger_car"].append({
            "id": "presentation-car-01",
            "scene_xyz_m": scene_xyz(car_candidate.x, car_candidate.y, offsets["road"] + 0.01),
            "road_heading_rad": round(car_heading + math.pi, 9),
            "placement_status": "deterministic_road_axis_offset_with_project_marking_clearance",
            "observed": False,
        })

    sidewalk_geometry = resolved.get("sidewalk", Polygon())
    sidewalk_interior = sidewalk_geometry.buffer(-0.75)
    if sidewalk_interior.is_empty:
        sidewalk_interior = sidewalk_geometry
    vegetation_points = [Point(float(row["scene_xyz_m"][0]), float(row["scene_xyz_m"][1])) for row in vegetation]
    for index, (distance_ahead, side) in enumerate(((30.0, 1.0), (73.0, -1.0)), start=1):
        axis_point, tx_axis, ty_axis, heading = axis_frame(distance_ahead)
        desired = Point(axis_point.x - ty_axis * side * 9.0, axis_point.y + tx_axis * side * 9.0)
        if sidewalk_geometry.is_empty:
            continue
        candidate_world = nearest_points(desired, sidewalk_interior)[1]
        if mapped_building_union.distance(candidate_world) < 2.5:
            continue
        candidate_scene = Point(candidate_world.x - origin_x, candidate_world.y - origin_y)
        if any(candidate_scene.distance(tree) < 2.2 for tree in vegetation_points):
            continue
        population_anchors["pedestrian"].append({
            "id": f"presentation-pedestrian-{index:02d}",
            "scene_xyz_m": scene_xyz(candidate_world.x, candidate_world.y, offsets["sidewalk"] + 0.01),
            "walking_heading_rad": round(heading if index % 2 else heading + math.pi, 9),
            "placement_status": "deterministic_nearest_admitted_sidewalk_with_building_and_tree_clearance",
            "observed": False,
        })

    presentation_assets = {
        "pedestrian": PRESENTATION_ASSET_DIR / "pedestrian-walking-trimmed.png",
        "passenger_car": PRESENTATION_ASSET_DIR / "compact-sedan-trimmed.png",
    }
    available_presentation_assets = {
        name: path for name, path in presentation_assets.items() if path.is_file()
    }
    missing_presentation_assets = sorted(set(presentation_assets) - set(available_presentation_assets))

    packet = {
        "schema": "green-atlas.controlled-project-street-scene.v1",
        "status": "deterministic_project_geometry_prototype",
        "scene_id": "kustanayskaya-controlled-mid-corridor-v1",
        "source": {
            "world": str(args.world.resolve()), "world_sha256": sha256(args.world),
            "surfaces": str(args.surfaces.resolve()), "surfaces_sha256": sha256(args.surfaces),
            "placement_audit": str(args.placement_audit.resolve()), "placement_audit_sha256": sha256(args.placement_audit),
            "project_dxf": str(args.project_dxf.resolve()), "project_dxf_sha256": sha256(args.project_dxf),
            "alignment": str(args.alignment.resolve()), "alignment_sha256": sha256(args.alignment),
        },
        "coordinate_frame": {
            "horizontal": "world local metres from candidate DXF-to-WGS84 similarity transform",
            "horizontal_alignment_status": "candidate_four_of_seven_inliers",
            "alignment_rmse_m": alignment.get("fit", {}).get("rmse_m", 0.852763),
            "vertical": world["coordinate_frame"]["vertical"],
            "scene_origin_world_local_xy_m": [round(origin_x, 6), round(origin_y, 6)],
            "scene_origin_grade_moscow_m": round(origin_z + float(world["coordinate_frame"]["origin_topography_moscow_m"]), 6),
            "limitation": "Authored XY is exact in DXF; map overlay remains candidate until independent geodetic checkpoints are supplied.",
        },
        "context_ground": {"triangles_scene_xyz_m": context_triangles, "status": "source_topography_quadratic_surface"},
        "surfaces": scene_surfaces,
        "automatic_context_surfaces": [],
        "curbs": curbs,
        "road_markings": inferred_road_markings,
        "automatic_infrastructure": infrastructure,
        "buildings": buildings,
        "unresolved_building_footprints": [],
        "vegetation": vegetation,
        "camera": {
            "position_scene_xyz_m": scene_xyz(origin_x, origin_y, 1.68),
            "target_scene_xyz_m": scene_xyz(target_source[0], target_source[1], 1.72),
            "lens_mm_proxy": 32.0,
            "sensor_width_mm": 36.0,
            "status": "position and direction from named mapped street axis; height and lens fixed render preset",
        },
        "lighting": {"preset": "clear_day_neutral", "status": "locked_render_preset", "sun_azimuth_deg": 138.0, "sun_elevation_deg": 36.0},
        "render_scenarios": {
            "project": {
                "description": "Project surfaces and authored project road marking are visible.",
                "project_marking_visible": True,
            },
            "existing_context": {
                "description": "Authored project road marking is hidden; OSM lane-count inference remains visibly labelled as an estimate.",
                "project_marking_visible": False,
            },
        },
        "presentation_population": {
            "default_enabled": False,
            "status": "optional_deterministic_non_observed_layer",
            "allowed_classes": ["pedestrian", "passenger_car"],
            "rules": {
                "pedestrian": "sidewalk only; outside glazing/entrance clearance; fixed seed and asset manifest required",
                "passenger_car": "road or parking only; aligned to mapped road tangent; crossing clearance required",
            },
            "truth_contract": "Presentation population is never treated as observed inventory or used by landscaping calculations.",
            "anchors": population_anchors,
            "assets": {
                name: {
                    "path": str(path.resolve()),
                    "sha256": sha256(path),
                    "source": "generated_once_then_hash_locked_transparent_cutout",
                }
                for name, path in available_presentation_assets.items()
            },
            "missing_optional_assets": missing_presentation_assets,
        },
        "material_registry": material_packet["material_registry"],
        "neural_finish": {"status": "disabled", "reason": "geometry and semantic render are audited before neural finishing"},
        "admission": {
            "project_surfaces": "exact HATCH XY",
            "project_curbs": "exact polyline XY, class dimensions are explicit proxies",
            "project_maf": "transformed visible block-centre XY, dimensions are explicit proxies",
            "vegetation": "matched marker + exact project lawn + known height + tree only",
            "osm_inside_project": "disabled",
            "building_detail_proxies": True,
        },
        "audit": {
            "surface_stats": surface_stats,
            "curb_segments": len(curbs),
            "curb_segment_counts": dict(curb_counts),
            "project_infrastructure": dict(infrastructure_counts),
            "marking_source_hatches": marking_source_hatches,
            "marking_triangles": len(marking_triangles),
            "inferred_osm_lane_marking_dashes": len(inferred_road_markings),
            "stabilized_sliver_triangles": stabilized_triangles,
            "stabilized_sliver_area_m2": round(stabilized_area_m2, 9),
            "buildings_rendered": len(buildings),
            "buildings_skipped": dict(building_skipped),
            "building_height_proxies": dict(building_height_proxies),
            "mapped_building_footprints_in_scene": len(mapped_building_footprints),
            "lawn_blocked_by_mapped_buildings_m2": round(lawn_blocked_by_buildings_m2, 6),
            "vegetation_rendered": len(vegetation),
            "vegetation_excluded": dict(vegetation_excluded),
        },
    }
    output_path = args.output / "scene-packet.json"
    output_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output_path.resolve()), "audit": packet["audit"], "camera": packet["camera"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
