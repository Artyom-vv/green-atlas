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
DEFAULT_STREET_OBJECTS = DEFAULT_AUDIT / "street-objects.geojson"
CLASS_IDS = {"road": 1, "sidewalk": 2, "lawn": 3}
AHORN_ASSET_PATH = ROOT / ".runtime/assets/blenderkit/ahorn-tree/ahorn-tree-1k.blend"
AHORN_ASSET_SHA256 = "609659602a9e31d9bb57360ef1abef0b7955f383624e31b08891f1e10d58983e"
AHORN_ASSET_BYTES = 8_117_775
PBR_ROOT = ROOT / ".runtime/cad-vegetation-20260919/pbr"


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
    triangle_classes = data.get("triangle_classes", [None] * len(data["triangles"]))
    if len(triangle_classes) != len(data["triangles"]):
        raise ValueError(f"triangle_classes length mismatch in {path}")
    for indices, surface_class in zip(data["triangles"], triangle_classes):
        xyz = [vertices[index] for index in indices]
        footprint = Polygon([(row[0], row[1]) for row in xyz])
        if not footprint.is_valid or footprint.area <= 1e-10:
            raise ValueError(f"Invalid terrain triangle in {path}: {indices}")
        triangles.append({"xyz": xyz, "footprint": footprint, "surface_class": surface_class})
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


def elevated_mesh(geometry: Any, terrain: dict[str, Any], origin: list[float],
                  required_class: str | None = None) -> tuple[dict[str, Any], Any]:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    covered_parts = []
    for terrain_triangle in terrain["triangles"]:
        if terrain_triangle["surface_class"] not in (None, required_class):
            continue
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


def sample_terrain(point: Point, terrain: dict[str, Any], required_class: str | None = None) -> float | None:
    for row in terrain["triangles"]:
        if row["surface_class"] not in (None, required_class):
            continue
        if row["footprint"].buffer(1e-9).covers(point):
            return barycentric_z(point.x, point.y, row["xyz"])
    return None


def json_geometry(geometry: Any) -> dict[str, Any]:
    return json.loads(json.dumps(mapping(geometry), sort_keys=True))


def texture_record(filename: str, expected_sha256: str, expected_bytes: int) -> dict[str, Any]:
    path = PBR_ROOT / filename
    actual_sha = sha256(path) if path.is_file() else None
    valid = bool(path.is_file() and path.stat().st_size == expected_bytes and actual_sha == expected_sha256)
    return {
        "path": str(path), "sha256": actual_sha, "expected_sha256": expected_sha256,
        "bytes": path.stat().st_size if path.is_file() else None, "expected_bytes": expected_bytes,
        "valid": valid,
    }


def compile_packet(
    source_contract_path: Path,
    surfaces_path: Path,
    general_terrain_path: Path,
    road_terrain_path: Path,
    trees_path: Path,
    street_objects_path: Path,
) -> dict[str, Any]:
    contract = json.loads(source_contract_path.read_text())
    surfaces = json.loads(surfaces_path.read_text())
    trees = json.loads(trees_path.read_text())
    street_objects = json.loads(street_objects_path.read_text())
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
        # The render packet currently has audited semantics only for these
        # three material classes.  Preserve every other authored class in the
        # source audit, but do not silently assign it a render material here.
        if semantic_class not in CLASS_IDS:
            continue
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
        mesh, covered = elevated_mesh(geometry, terrain, origin, semantic_class)
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

    ahorn_installed_sha = sha256(AHORN_ASSET_PATH) if AHORN_ASSET_PATH.is_file() else None
    ahorn_asset_valid = bool(
        ahorn_installed_sha == AHORN_ASSET_SHA256
        and AHORN_ASSET_PATH.stat().st_size == AHORN_ASSET_BYTES
    )
    asset_registry = [
        {
            "id": "blenderkit.ahorn_tree.1k",
            "kind": "deciduous_tree",
            "species_mapping": ["Клен"],
            "asset_name": "Ahorn tree",
            "asset_base_id": "6b36e011-4ce4-4f3d-98c9-4a2fd6b62f66",
            "asset_version_id": "9b0f6bb0-40a2-47a1-86cb-b5f52f36e2a8",
            "author": "Blendkit Community",
            "license": "CC0",
            "source_url": "https://www.blendkit.com/asset-gallery-detail/6b36e011-4ce4-4f3d-98c9-4a2fd6b62f66/",
            "api_download_uuid": "9f4a75be-ff46-40fb-9e37-0fd2997d6e31",
            "installed_path": str(AHORN_ASSET_PATH) if AHORN_ASSET_PATH.is_file() else None,
            "installed_sha256": ahorn_installed_sha,
            "expected_sha256": AHORN_ASSET_SHA256,
            "expected_bytes": AHORN_ASSET_BYTES,
            "native_bounds_m": [[-3.93259096, -4.25742912, -3.30225921],
                                [4.51455736, 4.05127764, 10.56235790]],
            "native_height_m": 13.86461711,
            "collection": "Ahorn tree",
            "dependency_collection": "twigs",
            "render_enabled": ahorn_asset_valid,
        },
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
    ]
    assets_by_id = {row["id"]: row for row in asset_registry}

    material_registry = {
        "road": {
            "id": "polyhaven.asphalt_01.2k",
            "license": "CC0",
            "source_url": "https://polyhaven.com/a/asphalt_01",
            "tile_width_m": 2.1,
            "scale_status": "fixed_visual_material_scale_not_geometry",
            "neutral_base_linear": [0.06, 0.065, 0.07, 1.0],
            "saturation": 0.20,
            "normal_strength": 0.28,
            "roughness_nominal": 0.88,
            "maps": {
                "diffuse": texture_record("asphalt01_Diffuse.jpg", "10fe1dafe34fec51397dbe9011f798c68c4e3fc16abfd4d7aea0123122db8993", 3_118_607),
                "roughness": texture_record("asphalt01_Rough.jpg", "c6962037eb41c6f0feafba22353e315d37ae8c8ea472dfd7d4fe28136904ceb5", 1_599_661),
                "normal_gl": texture_record("asphalt01_nor_gl.jpg", "60fd6af2aae30bb3fd2231227ecbe52d0ced2c761d53a64f50dea59d173307cd", 4_845_665),
            },
        },
        "sidewalk": {
            "id": "polyhaven.concrete_floor_02.2k",
            "license": "CC0",
            "source_url": "https://polyhaven.com/a/concrete_floor_02",
            "tile_width_m": 2.0,
            "scale_status": "fixed_visual_material_scale_not_geometry",
            "neutral_base_linear": [0.38, 0.39, 0.40, 1.0],
            "saturation": 0.12,
            "normal_strength": 0.16,
            "roughness_nominal": 0.82,
            "maps": {
                "diffuse": texture_record("concrete_Diffuse.jpg", "b08a20dcb306d08ee7f4a05ec216b19b7fd37e95745998ec5f81ecedaac1f0e9", 3_310_792),
                "roughness": texture_record("concrete_Rough.jpg", "cb539b0f8fdc8c63241683e01c923eb040fa701ec4c60cff27b674294746ae67", 1_417_673),
                "normal_gl": texture_record("concrete_nor_gl.jpg", "ce12d18f283f884f4be6f6c110f81582376049caada1e226958659d3d5fbaae1", 935_810),
            },
        },
    }
    for material in material_registry.values():
        material["render_enabled"] = all(row["valid"] for row in material["maps"].values())

    tree_candidates = []
    for tree in sorted(trees.get("matched", []), key=lambda item: item["number"]):
        point = Point(tree["xy"])
        if not scope.covers(point):
            continue
        z = sample_terrain(point, general_terrain, "lawn")
        asset_id = "blenderkit.ahorn_tree.1k" if tree["species"] == "Клен" else None
        asset_ready = bool(asset_id and assets_by_id[asset_id]["render_enabled"])
        render_enabled = bool(z is not None and asset_ready)
        if z is None:
            asset_gate = "blocked_missing_ground_z"
        elif not asset_id:
            asset_gate = "blocked_no_species_compatible_asset"
        elif not asset_ready:
            asset_gate = "blocked_asset_missing_or_hash_mismatch"
        else:
            asset_gate = "passed"
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
            "asset_id": asset_id,
            "render_enabled": render_enabled,
            "asset_gate": asset_gate,
        }
        tree_candidates.append(placement)

    street_object_candidates = []
    terrain_by_class = {"road": road_terrain, "sidewalk": general_terrain, "lawn": general_terrain}
    for feature in street_objects["features"]:
        point = shape(feature["geometry"])
        surface_classes = [name for name in ("road", "sidewalk", "lawn") if class_union[name].covers(point)]
        surface_class = surface_classes[0] if len(surface_classes) == 1 else None
        z = sample_terrain(point, terrain_by_class[surface_class], surface_class) if surface_class else None
        properties = feature["properties"]
        street_object_candidates.append({
            "id": f"street:{properties['source_handle']}",
            "semantic_class": properties["semantic_class"],
            "source": properties,
            "surface_class": surface_class,
            "position_local": [
                round(point.x-origin[0], 6), round(point.y-origin[1], 6),
                round(z-origin[2], 6) if z is not None else None,
            ],
            "placement_status": "estimated_unconfirmed_ground" if z is not None else "blocked_missing_ground_z",
            "asset_id": None,
            "asset_gate": "blocked_no_verified_physical_3d_asset",
            "render_enabled": False,
        })

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
            "street_objects": {"path": str(street_objects_path), "sha256": sha256(street_objects_path)},
        },
        "geometry": {
            "plan_objects": plan_objects,
            "render_objects": render_objects,
            "unknown_xy_geometry": json_geometry(unknown_xy),
            "authored_xy_without_height_geometry": json_geometry(authored_without_height),
        },
        "vegetation_candidates": tree_candidates,
        "street_object_candidates": street_object_candidates,
        "asset_registry": asset_registry,
        "material_registry": material_registry,
        "render": {
            "engine": "CYCLES",
            "device_policy": "METAL_if_available_else_CPU",
            "resolution": [960, 640],
            "samples": 96,
            "denoise": True,
            "camera": {
                "projection": "PERSP",
                "lens_mm": 52.0,
                "position_local": [-32.0, -38.0, 34.0],
                "target_local": [1.0, 2.0, 1.2],
            },
            "sun": {"rotation_deg": [32.0, -18.0, -128.0], "energy": 2.1, "angle_deg": 4.0},
            "world": {"model": "Nishita", "sun_elevation_deg": 32.0, "air_density": 1.0, "dust_density": 1.2},
            "passes": ["Combined", "Depth", "Normal", "Position", "DiffuseColor", "AmbientOcclusion",
                       "ObjectIndex", "CryptomatteObject", "AOV:Roughness", "AOV:SemanticID",
                       "AOV:HeightConfidence"],
            "semantic_palette_srgb": {
                "unknown": [255, 0, 255],
                "road": [55, 58, 62],
                "sidewalk": [190, 180, 165],
                "lawn": [54, 118, 52],
                "existing_tree": [0, 190, 210],
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
            "trees_render_enabled": sum(x["render_enabled"] for x in tree_candidates),
            "street_object_candidates": len(street_object_candidates),
            "street_objects_with_estimated_z": sum(x["position_local"][2] is not None for x in street_object_candidates),
            "street_objects_render_enabled": sum(x["render_enabled"] for x in street_object_candidates),
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
                        default=ROOT / ".runtime/terrain-control-20260920/class-constrained/terrain.json")
    parser.add_argument("--road-terrain", type=Path,
                        default=ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json")
    parser.add_argument("--trees", type=Path, default=ROOT / ".runtime/cad-vegetation-20260919/trees.json")
    parser.add_argument("--street-objects", type=Path, default=DEFAULT_STREET_OBJECTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    packet = compile_packet(args.source_contract, args.surfaces, args.general_terrain, args.road_terrain,
                            args.trees, args.street_objects)
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
            "vegetation_assets_partial": packet["quality"]["trees_render_enabled"] > 0,
            "vegetation_assets_complete": (
                packet["quality"]["trees_render_enabled"] == packet["quality"]["tree_candidates"]
            ),
            "neural_finish": {
                "status": "pending_render_masks",
                "geometry_or_object_generation_allowed": False,
            },
        },
    }
    (args.output / "compile-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
