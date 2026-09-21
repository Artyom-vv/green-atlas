"""Compile source-derived CAD/GIS evidence into a deterministic render packet.

The packet deliberately separates:

* exact authored XY surfaces used by plan/semantic views;
* surface fragments for which an estimated Z mesh is available;
* vegetation candidates, which remain disabled until both placement and an
  appropriately licensed asset pass their gates.

Nothing is extrapolated outside the supplied terrain triangles.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from shapely import constrained_delaunay_triangles
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT = ROOT / ".runtime/deterministic-render-audit-20260919"
DEFAULT_OUTPUT = ROOT / ".runtime/deterministic-render-pipeline-20260920"
CLASS_IDS = {"road": 1, "sidewalk": 2, "lawn": 3}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_seed(value: str) -> int:
    return int.from_bytes(hashlib.sha256(value.encode("utf-8")).digest()[:8], "big")


def polygons(geometry: Any) -> Iterable[Polygon]:
    if geometry.is_empty:
        return
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, MultiPolygon):
        yield from geometry.geoms
    else:
        for part in getattr(geometry, "geoms", []):
            yield from polygons(part)


def triangulate_polygon(polygon: Polygon) -> list[Polygon]:
    """Return triangles fully covered by a simple polygon.

    Intersections used here are source polygons clipped by terrain triangles,
    so they have no holes. The covered check prevents Delaunay diagonals from
    bridging a concavity.
    """
    if polygon.is_empty or polygon.area <= 1e-10:
        return []
    result = [
        candidate for candidate in constrained_delaunay_triangles(polygon).geoms
        if candidate.area > 1e-10
    ]
    area = sum(item.area for item in result)
    if not math.isclose(area, polygon.area, abs_tol=1e-6, rel_tol=1e-8):
        raise ValueError(f"Triangulation area mismatch: {area} != {polygon.area}")
    return result


def barycentric_z(x: float, y: float, xyz: list[list[float]]) -> float:
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = xyz
    denominator = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(denominator) < 1e-12:
        raise ValueError("Degenerate terrain triangle")
    a = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / denominator
    b = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / denominator
    return a * z1 + b * z2 + (1 - a - b) * z3


def load_terrain(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text())
    vertices = [row["xyz"] for row in data["vertices"]]
    triangles = []
    for indices in data["triangles"]:
        xyz = [vertices[index] for index in indices]
        footprint = Polygon([(row[0], row[1]) for row in xyz])
        if not footprint.is_valid or footprint.area <= 1e-10:
            raise ValueError(f"Invalid terrain triangle in {path}: {indices}")
        triangles.append({"xyz": xyz, "footprint": footprint})
    return {"path": path, "data": data, "triangles": triangles,
            "coverage": unary_union([row["footprint"] for row in triangles])}


def plan_mesh(geometry: Any, origin: list[float], z: float = 0.0) -> dict[str, Any]:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    for polygon in polygons(geometry):
        for triangle in triangulate_polygon(polygon):
            face = []
            for x, y in list(triangle.exterior.coords)[:3]:
                face.append(len(vertices))
                vertices.append([round(x - origin[0], 6), round(y - origin[1], 6), z])
            faces.append(face)
    return {"vertices": vertices, "triangles": faces}


def elevated_mesh(geometry: Any, terrain: dict[str, Any], origin: list[float]) -> tuple[dict[str, Any], Any]:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    covered_parts = []
    for terrain_triangle in terrain["triangles"]:
        clipped = geometry.intersection(terrain_triangle["footprint"])
        for polygon in polygons(clipped):
            covered_parts.append(polygon)
            for triangle in triangulate_polygon(polygon):
                face = []
                for x, y in list(triangle.exterior.coords)[:3]:
                    z = barycentric_z(x, y, terrain_triangle["xyz"])
                    face.append(len(vertices))
                    vertices.append([
                        round(x - origin[0], 6),
                        round(y - origin[1], 6),
                        round(z - origin[2], 6),
                    ])
                faces.append(face)
    coverage = unary_union(covered_parts) if covered_parts else Polygon()
    return {"vertices": vertices, "triangles": faces}, coverage


def sample_terrain(point: Point, terrain: dict[str, Any]) -> float | None:
    for row in terrain["triangles"]:
        if row["footprint"].buffer(1e-9).covers(point):
            return barycentric_z(point.x, point.y, row["xyz"])
    return None


def json_geometry(geometry: Any) -> dict[str, Any]:
    return json.loads(json.dumps(mapping(geometry), sort_keys=True))


def compile_packet(
    source_contract_path: Path,
    surfaces_path: Path,
    general_terrain_path: Path,
    road_terrain_path: Path,
    trees_path: Path,
) -> dict[str, Any]:
    contract = json.loads(source_contract_path.read_text())
    surfaces = json.loads(surfaces_path.read_text())
    trees = json.loads(trees_path.read_text())
    general_terrain = load_terrain(general_terrain_path)
    road_terrain = load_terrain(road_terrain_path)

    bbox = contract["scope"]["bbox_local_m"]
    scope = box(*bbox)
    origin = [round((bbox[0] + bbox[2]) / 2, 6), round((bbox[1] + bbox[3]) / 2, 6), 150.0]
    source_features = sorted(
        surfaces["features"],
        key=lambda item: (item["properties"]["class"], item["properties"]["source_handle"]),
    )
    plan_objects = []
    render_objects = []
    class_geometries: dict[str, list[Any]] = {key: [] for key in CLASS_IDS}
    class_height_geometries: dict[str, list[Any]] = {key: [] for key in CLASS_IDS}

    for feature in source_features:
        semantic_class = feature["properties"]["class"]
        geometry = shape(feature["geometry"]).intersection(scope)
        if geometry.is_empty:
            continue
        class_geometries[semantic_class].append(geometry)
        common = {
            "id": f"surface:{feature['properties']['source_handle']}",
            "semantic_class": semantic_class,
            "semantic_id": CLASS_IDS[semantic_class],
            "source": feature["properties"],
            "area_xy_m2": round(geometry.area, 6),
        }
        plan_objects.append({**common, "mesh": plan_mesh(geometry, origin), "height_status": "not_required"})
        terrain = road_terrain if semantic_class == "road" else general_terrain
        mesh, covered = elevated_mesh(geometry, terrain, origin)
        if mesh["triangles"]:
            class_height_geometries[semantic_class].append(covered)
            render_objects.append({
                **common,
                "id": common["id"] + ":z",
                "mesh": mesh,
                "area_xy_m2": round(covered.area, 6),
                "height_status": terrain["data"]["status"],
                "height_source": {
                    "path": str(terrain["path"]),
                    "sha256": sha256(terrain["path"]),
                    "source_sha256": terrain["data"].get("source_sha256"),
                    "limitations": terrain["data"].get("limitations", []),
                },
            })

    class_union = {key: unary_union(items) if items else Polygon() for key, items in class_geometries.items()}
    height_union = {key: unary_union(items) if items else Polygon() for key, items in class_height_geometries.items()}
    authored_union = unary_union(list(class_union.values()))
    known_height_union = unary_union(list(height_union.values()))
    unknown_xy = scope.difference(authored_union)
    authored_without_height = authored_union.difference(known_height_union)

    tree_candidates = []
    for tree in sorted(trees.get("matched", []), key=lambda item: item["number"]):
        point = Point(tree["xy"])
        if not scope.covers(point):
            continue
        z = sample_terrain(point, general_terrain)
        placement = {
            "id": f"tree:{tree['number']}",
            "semantic_class": "existing_tree",
            "source": {
                "file": trees["source"],
                "handle": tree["handle"],
                "number_handle": tree["number_handle"],
                "inventory_number": tree["number"],
                "link_status": tree["link_status"],
                "source_row": tree["source_row"],
            },
            "species": tree["species"],
            "height_m": tree["height_m"],
            "position_local": [round(point.x-origin[0], 6), round(point.y-origin[1], 6),
                               round(z-origin[2], 6) if z is not None else None],
            "rotation_z_rad": round((stable_seed(str(tree["number"])) % 10_000) / 10_000 * math.tau, 9),
            "placement_status": "estimated_unconfirmed_ground" if z is not None else "blocked_missing_ground_z",
            "render_enabled": False,
            "asset_gate": "no approved species-compatible asset installed",
        }
        tree_candidates.append(placement)

    by_class = {}
    for semantic_class in CLASS_IDS:
        total = class_union[semantic_class].area
        elevated = height_union[semantic_class].area
        by_class[semantic_class] = {
            "authored_xy_area_m2": round(total, 6),
            "renderable_estimated_z_area_m2": round(elevated, 6),
            "xy_known_z_unknown_area_m2": round(max(0.0, total-elevated), 6),
            "renderable_ratio": round(elevated/total, 9) if total else 0.0,
        }

    packet = {
        "schema": "green-atlas.render-scene-packet.v1",
        "scene_id": "kustanayskaya-control-53x43m-v1",
        "determinism": {
            "random_seed": 20260920,
            "coordinate_rounding_decimals": 6,
            "object_order": "semantic_class_then_source_handle",
            "unknown_fill_allowed": False,
        },
        "coordinates": {
            "source_horizontal": contract["coordinate_contract"],
            "vertical_reference": contract["terrain_status"]["vertical_reference_from_project_documents"],
            "local_origin_xyz": origin,
            "inverse": "source_xyz = local_xyz + local_origin_xyz",
            "scope_bbox_source_xy_m": bbox,
        },
        "inputs": {
            "source_contract": {"path": str(source_contract_path), "sha256": sha256(source_contract_path)},
            "authored_surfaces": {"path": str(surfaces_path), "sha256": sha256(surfaces_path)},
            "general_terrain": {"path": str(general_terrain_path), "sha256": sha256(general_terrain_path),
                                "status": general_terrain["data"]["status"]},
            "road_terrain": {"path": str(road_terrain_path), "sha256": sha256(road_terrain_path),
                             "status": road_terrain["data"]["status"]},
            "tree_inventory": {"path": str(trees_path), "sha256": sha256(trees_path)},
        },
        "geometry": {
            "plan_objects": plan_objects,
            "render_objects": render_objects,
            "unknown_xy_geometry": json_geometry(unknown_xy),
            "authored_xy_without_height_geometry": json_geometry(authored_without_height),
        },
        "vegetation_candidates": tree_candidates,
        "asset_registry": [
            {
                "id": "polyhaven.fir_tree_01.1k",
                "kind": "conifer_tree",
                "species": "fir; may only be used as an explicitly labelled conifer proxy",
                "license": "CC0",
                "license_url": "https://polyhaven.com/license",
                "source_url": "https://polyhaven.com/a/fir_tree_01",
                "download_url": "https://dl.polyhaven.org/file/ph-assets/Models/blend/1k/fir_tree_01/fir_tree_01_1k.blend",
                "expected_md5": "a08031ea8ffb49711b294e1c8213a909",
                "expected_bytes": 218986261,
                "installed_path": None,
                "render_enabled": False,
            },
            {
                "id": "local.botaniq_6_8",
                "kind": "vegetation_library",
                "license": "unverified",
                "installed_path": str(ROOT / ".runtime/botaniq-68-inspection/selected/botaniq_full"),
                "render_enabled": False,
                "block_reason": "archive provenance does not establish a production-use license",
            },
        ],
        "render": {
            "engine": "CYCLES",
            "device_policy": "METAL_if_available_else_CPU",
            "resolution": [960, 640],
            "samples": 96,
            "denoise": True,
            "camera": {
                "projection": "PERSP",
                "lens_mm": 52.0,
                "position_local": [-42.0, -46.0, 29.0],
                "target_local": [5.0, 4.0, 1.0],
            },
            "sun": {"rotation_deg": [32.0, -18.0, -128.0], "energy": 2.1, "angle_deg": 4.0},
            "world": {"model": "Nishita", "sun_elevation_deg": 32.0, "air_density": 1.0, "dust_density": 1.2},
            "passes": ["Combined", "Depth", "Normal", "Position", "DiffuseColor", "Roughness",
                       "Shadow", "AmbientOcclusion", "ObjectIndex", "CryptomatteObject"],
            "semantic_palette_srgb": {
                "unknown": [255, 0, 255],
                "road": [55, 58, 62],
                "sidewalk": [190, 180, 165],
                "lawn": [54, 118, 52],
            },
        },
        "quality": {
            "scope_area_m2": round(scope.area, 6),
            "authored_xy_area_m2": round(authored_union.area, 6),
            "unknown_xy_area_m2": round(unknown_xy.area, 6),
            "renderable_estimated_z_area_m2": round(known_height_union.area, 6),
            "authored_xy_without_height_area_m2": round(authored_without_height.area, 6),
            "by_class": by_class,
            "tree_candidates": len(tree_candidates),
            "tree_candidates_with_estimated_z": sum(x["position_local"][2] is not None for x in tree_candidates),
            "trees_render_enabled": 0,
            "external_osm_context_enabled": False,
            "external_osm_gate": "candidate alignment is not survey control",
        },
    }
    return packet


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-contract", type=Path, default=DEFAULT_AUDIT / "source-contract.json")
    parser.add_argument("--surfaces", type=Path, default=DEFAULT_AUDIT / "authored-surfaces.geojson")
    parser.add_argument("--general-terrain", type=Path,
                        default=ROOT / ".runtime/terrain-control-20260918/mesh/terrain.json")
    parser.add_argument("--road-terrain", type=Path,
                        default=ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json")
    parser.add_argument("--trees", type=Path, default=ROOT / ".runtime/cad-vegetation-20260919/trees.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    packet = compile_packet(args.source_contract, args.surfaces, args.general_terrain, args.road_terrain, args.trees)
    target = args.output / "scene-packet.json"
    target.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    receipt = {
        "schema": "green-atlas.render-scene-compile-receipt.v1",
        "scene_packet": str(target),
        "scene_packet_sha256": sha256(target),
        "quality": packet["quality"],
        "gates": {
            "unknown_area_preserved": True,
            "height_extrapolation": False,
            "osm_context": False,
            "vegetation_assets": False,
            "neural_finish": False,
        },
    }
    (args.output / "compile-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
