"""Bridge an already processed metric packet to the retained beauty compiler.

No CAD file is read here. The historical compiler still consumes the authored
project DXF to recover curb, furniture and marking evidence for this example.
This bridge is a recovery experiment, not the product's CAD ingestion path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import mapping, shape
from shapely.ops import transform


ROOT = Path(__file__).resolve().parents[2]
EARTH_RADIUS_M = 6_378_137.0
PBR = ROOT / ".runtime/cad-vegetation-20260919/pbr"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--map-roads", type=Path, required=True)
    parser.add_argument("--map-buildings", type=Path, required=True)
    parser.add_argument("--street-name", default="Кустанайская улица")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text())
    alignment = json.loads(args.alignment.read_text())
    map_roads = json.loads(args.map_roads.read_text())
    map_buildings = json.loads(args.map_buildings.read_text())
    args.output.mkdir(parents=True, exist_ok=True)

    fit = alignment["similarity_transform_row_vector"]
    rotation = np.asarray(fit["rotation"], dtype=float)
    translation = np.asarray(fit["translation"], dtype=float)
    scale = float(fit["scale"])
    ref_lon, ref_lat = alignment["projection_before_fit"]["reference_lon_lat"]
    radius = float(alignment["projection_before_fit"]["earth_radius_m"])
    cad_origin = np.asarray(packet["origin_cad_xy_height_m"][:2], dtype=float)

    def cad_to_lonlat(cad_xy) -> tuple[float, float]:
        tangent = (np.asarray(cad_xy, dtype=float) - translation) @ np.linalg.inv(rotation) / scale
        return (
            ref_lon + math.degrees(tangent[0] / (radius * math.cos(math.radians(ref_lat)))),
            ref_lat + math.degrees(tangent[1] / radius),
        )

    origin_lon, origin_lat = cad_to_lonlat(cad_origin)

    def cad_to_world(cad_xy) -> tuple[float, float]:
        lon, lat = cad_to_lonlat(cad_xy)
        return (
            EARTH_RADIUS_M * math.radians(lon - origin_lon) * math.cos(math.radians(origin_lat)),
            EARTH_RADIUS_M * math.radians(lat - origin_lat),
        )

    origin_world = np.asarray(cad_to_world(cad_origin))
    axis_x = np.asarray(cad_to_world(cad_origin + np.array([1.0, 0.0]))) - origin_world
    axis_y = np.asarray(cad_to_world(cad_origin + np.array([0.0, 1.0]))) - origin_world
    cad_to_world_matrix = np.column_stack((axis_x, axis_y))
    world_to_cad_matrix = np.linalg.inv(cad_to_world_matrix)

    model = packet["terrain"]
    center = np.asarray(model["center_xy_m"], dtype=float)
    span = np.asarray(model["scale_xy_m"], dtype=float)
    coeff = np.asarray(model["coefficients_m"], dtype=float)
    low, high = map(float, model["observed_range_m"])

    def height_at_world(x: float, y: float) -> float:
        cad_xy = cad_origin + world_to_cad_matrix @ np.asarray([x, y], dtype=float)
        u, v = (cad_xy - center) / span
        return float(np.clip(coeff @ [1.0, u, v, u * u, u * v, v * v], low, high))

    origin_height = height_at_world(0.0, 0.0)
    road_names = {}
    for feature in map_roads["features"]:
        properties = feature.get("properties") or {}
        name = (properties.get("names") or {}).get("primary")
        if name:
            road_names[feature.get("id")] = name
    axes = []
    for row in packet["road_axes"]:
        if road_names.get(row["id"]) != args.street_name:
            continue
        points = [
            [*cad_to_world(cad_origin + np.asarray(point[:2], dtype=float)), 0.0]
            for point in row["xyz"]
        ]
        axes.append({
            "id": row["id"], "name": args.street_name,
            "project_geometry_local": {"type": "LineString", "coordinates": points},
            "length_in_project_m": sum(math.dist(a[:2], b[:2]) for a, b in zip(points, points[1:])),
            "lanes": None,
        })
    if not axes:
        raise RuntimeError(f"Named map axis absent: {args.street_name}")

    buildings = []
    for feature in map_buildings["features"]:
        geometry = transform(
            lambda lon, lat, z=None: (
                EARTH_RADIUS_M * math.radians(lon - origin_lon) * math.cos(math.radians(origin_lat)),
                EARTH_RADIUS_M * math.radians(lat - origin_lat),
            ), shape(feature["geometry"]),
        )
        if not geometry.is_valid or geometry.area <= 0:
            continue
        mapped = feature.get("properties") or {}
        mapped_class = mapped.get("class") or "building"
        height = None
        height_status = "unknown"
        for key in ("height", "height_m"):
            try:
                value = float(mapped[key])
                if math.isfinite(value) and 0 < value < 500:
                    height, height_status = value, "cartographic_height"
                    break
            except (KeyError, TypeError, ValueError):
                pass
        if height is None:
            for key in ("num_floors", "building:levels"):
                try:
                    floors = float(mapped[key])
                    if math.isfinite(floors) and 0 < floors < 150:
                        height, height_status = floors * 3.0, "estimated_from_floor_count_3m"
                        break
                except (KeyError, TypeError, ValueError):
                    pass
        buildings.append({
            "id": feature["id"], "geometry_local": mapping(geometry),
            "properties": {"class": mapped_class, "class_status": "overture_feature_class" if mapped.get("class") else "unknown_generic",
                           "height_m": height, "height_status": height_status},
        })
    vegetation = []
    audit_records = []
    for row in packet["trees"]:
        inventory_id = int(row["id"])
        x, y = cad_to_world(cad_origin + np.asarray(row["xyz"][:2], dtype=float))
        vegetation.append({
            "inventory_id": inventory_id, "kind": "tree", "species": row["species"],
            "position_status": "matched_marker", "height_m": row["height_m"],
            "position_local_xyz_m": [x, y, height_at_world(x, y) - origin_height],
        })
        audit_records.append({"inventory_id": inventory_id, "project_surface": "lawn"})

    bounds = packet["scope_bounds_local_m"]
    corners = [cad_to_world(cad_origin + np.asarray([x, y])) for x in (bounds[0], bounds[2]) for y in (bounds[1], bounds[3])]
    xmin, xmax = min(x for x, _ in corners) - 300.0, max(x for x, _ in corners) + 300.0
    ymin, ymax = min(y for _, y in corners) - 300.0, max(y for _, y in corners) + 300.0
    columns = int(math.ceil((xmax - xmin) / 10.0)) + 1
    rows = int(math.ceil((ymax - ymin) / 10.0)) + 1
    vertices = []
    for row in range(rows):
        y = ymin + (ymax - ymin) * row / (rows - 1)
        for column in range(columns):
            x = xmin + (xmax - xmin) * column / (columns - 1)
            vertices.append({"xyz_local_m": [x, y, height_at_world(x, y) - origin_height]})

    world = {
        "schema": "green-atlas.recovered-controlled-world.v1",
        "coordinate_frame": {
            "origin_wgs84_lon_lat": [origin_lon, origin_lat],
            "earth_radius_m": EARTH_RADIUS_M,
            "origin_topography_moscow_m": origin_height,
            "vertical": "estimated smooth Moscow datum surface from source labels",
        },
        "terrain": {"rows": rows, "columns": columns, "vertices": vertices,
                    "status": model["status"], "source_model": model},
        "named_reference_axes": axes,
        "buildings": buildings,
        "inventory_vegetation": vegetation,
        "source": {"metric_packet": str(args.packet.resolve()), "sha256": sha256(args.packet)},
    }

    def texture_spec(identifier: str, names: tuple[str, str, str]) -> dict:
        return {
            "id": identifier,
            "maps": {
                role: {"path": str(PBR / filename), "expected_sha256": sha256(PBR / filename)}
                for role, filename in zip(("diffuse", "roughness", "normal_gl"), names)
            },
        }

    material_packet = {"material_registry": {
        "road": texture_spec("polyhaven.asphalt_01.2k", ("asphalt01_Diffuse.jpg", "asphalt01_Rough.jpg", "asphalt01_nor_gl.jpg")),
        "sidewalk": texture_spec("polyhaven.concrete_floor_02.2k", ("concrete_Diffuse.jpg", "concrete_Rough.jpg", "concrete_nor_gl.jpg")),
    }}
    files = {"world.json": world, "placement-audit.json": {"records": audit_records}, "material-packet.json": material_packet}
    for name, contents in files.items():
        (args.output / name).write_text(json.dumps(contents, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"world": str(args.output / "world.json"), "buildings": len(buildings),
                      "vegetation": len(vegetation), "named_axes": len(axes), "terrain_grid": [columns, rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
