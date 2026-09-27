"""Assemble a metric, provenance-labelled scene from *already processed* inputs.

CAD input is GeoJSON emitted by the admitted AutoCAD capture pipeline. This
tool does not read DWG/DXF or reinterpret CAD entities. Missing geometry stays
missing; cartographic estimates never become surveyed objects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box, shape
from shapely.ops import unary_union

from automatic_address_controls import normalize_address, pair_unique_addresses


EARTH_RADIUS_M = 6_378_137.0
SURFACE_CLASSES = {"road", "sidewalk", "lawn", "marking", "special_surface"}


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def read_input(manifest_path: Path, row: dict[str, Any] | None) -> tuple[Any, dict[str, Any]] | tuple[None, None]:
    if row is None:
        return None, None
    path = (manifest_path.parent / row["path"]).resolve()
    if not path.is_file():
        raise ValueError(f"Input does not exist: {path}")
    sha = digest(path)
    if row.get("sha256") and row["sha256"] != sha:
        raise ValueError(f"Input SHA differs: {path}")
    return json.loads(path.read_text()), {"path": str(path), "sha256": sha}


def candidate_alignment(cad: dict[str, Any], mapped: dict[str, Any]) -> dict[str, Any]:
    cad_rows = []
    for feature in cad["features"]:
        geometry = shape(feature["geometry"])
        address = normalize_address(feature.get("properties", {}).get("address"))
        if address and isinstance(geometry, Polygon) and geometry.is_valid:
            cad_rows.append({"normalized_address": address, "centroid": geometry.centroid.coords[0]})
    map_rows = []
    for feature in mapped["features"]:
        geometry = shape(feature["geometry"])
        props = feature.get("properties", {})
        address = normalize_address(props.get("addr:housenumber") or props.get("address"))
        if address and isinstance(geometry, Polygon) and geometry.is_valid:
            map_rows.append({"normalized_address": address, "centroid": geometry.centroid.coords[0]})
    pairs, audit = pair_unique_addresses(cad_rows, map_rows)
    if len(pairs) < 3:
        raise ValueError(f"Insufficient unique address controls: {len(pairs)}; need >=3")
    lon0 = float(np.mean([row["map"]["centroid"][0] for row in pairs]))
    lat0 = float(np.mean([row["map"]["centroid"][1] for row in pairs]))
    source = np.array([tangent(*row["map"]["centroid"], lon0, lat0) for row in pairs])
    target = np.array([row["dxf"]["centroid"] for row in pairs])
    best = None
    for a, b in combinations(range(len(pairs)), 2):
        if np.linalg.norm(source[a] - source[b]) < 15 or np.linalg.norm(target[a] - target[b]) < 15:
            continue
        try:
            scale, rotation, translation = fit_similarity(source[[a, b]], target[[a, b]])
        except (ValueError, np.linalg.LinAlgError):
            continue
        if not 0.95 <= scale <= 1.05:
            continue
        residuals = np.linalg.norm(scale * source @ rotation + translation - target, axis=1)
        inliers = residuals <= 3.0
        score = (int(inliers.sum()), -float(np.mean(residuals[inliers])) if inliers.any() else -math.inf)
        if best is None or score > best[0]:
            best = (score, inliers)
    if best is None or best[0][0] < 3:
        raise ValueError("Address RANSAC found fewer than 3 agreeing controls")
    scale, rotation, translation = fit_similarity(source[best[1]], target[best[1]])
    residuals = np.linalg.norm(scale * source @ rotation + translation - target, axis=1)
    inliers = residuals <= 3.0
    rmse = float(np.sqrt(np.mean(residuals[inliers] ** 2)))
    if int(inliers.sum()) < 3 or rmse > 2.0:
        raise ValueError(f"Address alignment failed final gate: {int(inliers.sum())} controls, RMSE {rmse:.3f}m")
    return {
        "status": "candidate_address_alignment_not_survey_control",
        "reference_lon_lat": [lon0, lat0], "scale": float(scale),
        "rotation": rotation.tolist(), "translation": translation.tolist(),
        "unique_pairs": len(pairs), "inliers": int(inliers.sum()), "rmse_m": round(rmse, 6),
        "ambiguous_addresses": audit["ambiguous"],
    }


def fit_similarity(source: np.ndarray, target: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    a, b = source - source.mean(axis=0), target - target.mean(axis=0)
    denominator = float(np.sum(a * a))
    if denominator < 1e-9:
        raise ValueError("Degenerate control points")
    left, singular, right = np.linalg.svd(a.T @ b)
    orientation = float(np.linalg.det(left @ right))
    rotation = left @ np.diag([1, orientation]) @ right
    scale = float((singular[0] + orientation * singular[1]) / denominator)
    return scale, rotation, target.mean(axis=0) - scale * source.mean(axis=0) @ rotation


def tangent(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    return (EARTH_RADIUS_M * math.radians(lon - lon0) * math.cos(math.radians(lat0)),
            EARTH_RADIUS_M * math.radians(lat - lat0))


def read_alignment(data: dict[str, Any]) -> dict[str, Any]:
    if "similarity_transform_row_vector" in data:
        fit = data["similarity_transform_row_vector"]
        lonlat = data["projection_before_fit"]["reference_lon_lat"]
        return {"status": "candidate_supplied_not_survey_control", "reference_lon_lat": lonlat,
                "scale": fit["scale"], "rotation": fit["rotation"],
                "translation": fit["translation"], "rmse_m": data.get("ransac", {}).get("rmse_inliers_m"),
                "inliers": data.get("ransac", {}).get("inliers")}
    required = {"reference_lon_lat", "scale", "rotation", "translation", "status"}
    if not required <= data.keys():
        raise ValueError("Alignment lacks transformation or provenance")
    return data


def map_to_cad(point: tuple[float, float], alignment: dict[str, Any]) -> tuple[float, float]:
    lon0, lat0 = alignment["reference_lon_lat"]
    x, y = tangent(point[0], point[1], lon0, lat0)
    r = alignment["rotation"]
    scale = alignment["scale"]
    t = alignment["translation"]
    return (scale * (x * r[0][0] + y * r[1][0]) + t[0],
            scale * (x * r[0][1] + y * r[1][1]) + t[1])


def transform_geometry(geometry: Any, alignment: dict[str, Any]):
    from shapely.ops import transform
    return transform(lambda x, y, z=None: (*map_to_cad((x, y), alignment),) if np.isscalar(x)
                     else (np.asarray([map_to_cad((float(a), float(b)), alignment)[0] for a, b in zip(x, y)]),
                           np.asarray([map_to_cad((float(a), float(b)), alignment)[1] for a, b in zip(x, y)])), geometry)


def fit_ground(geojson: dict[str, Any], bounds: tuple[float, float, float, float], datum: str) -> tuple[dict[str, Any], Any, Polygon]:
    observations = {}
    for feature in geojson["features"]:
        geometry = shape(feature["geometry"])
        z = feature.get("properties", {}).get("z_m")
        if not isinstance(geometry, Point) or not isinstance(z, (int, float)) or not math.isfinite(z):
            continue
        key = (round(geometry.x, 3), round(geometry.y, 3))
        observations[key] = min(float(z), observations.get(key, math.inf))
    if len(observations) < 6:
        raise ValueError("At least six independent ground controls are required for a volumetric scene")
    center = [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2]
    span = [max(bounds[2] - bounds[0], 1), max(bounds[3] - bounds[1], 1)]
    points = np.array(list(observations), dtype=float)
    z = np.array(list(observations.values()), dtype=float)
    x, y = ((points[:, axis] - center[axis]) / span[axis] for axis in (0, 1))
    matrix = np.column_stack((np.ones(len(z)), x, y, x*x, x*y, y*y))
    if np.linalg.matrix_rank(matrix) < 6:
        raise ValueError("Ground controls do not support a two-dimensional height fit")
    weights = np.ones(len(z))
    for _ in range(15):
        root = np.sqrt(weights)
        coeff = np.linalg.lstsq(matrix * root[:, None], z * root, rcond=None)[0]
        residual = z - matrix @ coeff
        weights = np.minimum(1.0, 0.5 / np.maximum(np.abs(residual), 1e-9))
    rmse = float(np.sqrt(np.mean(residual**2)))
    low, high = float(z.min()), float(z.max())
    def sample(px: float, py: float) -> float:
        nx, ny = (px-center[0])/span[0], (py-center[1])/span[1]
        return float(np.clip(np.dot(coeff, [1, nx, ny, nx*nx, nx*ny, ny*ny]), low, high))
    from shapely.geometry import MultiPoint
    support_hull = MultiPoint([tuple(point) for point in points]).convex_hull
    source_scope = box(*bounds)
    coverage_ratio = float(support_hull.intersection(source_scope).area / source_scope.area)
    model = {"status": "estimated_smooth_surface_from_source_labels", "datum": datum,
             "control_count": len(observations), "rmse_m": round(rmse, 6),
             "control_hull_bbox_coverage_ratio": round(coverage_ratio, 6),
             "observed_range_m": [low, high], "center_xy_m": center, "scale_xy_m": span,
             "coefficients_m": coeff.tolist(), "microrelief": "unknown"}
    return model, sample, support_hull


def mesh_polygon(poly: Polygon, sample: Any, origin: list[float]) -> list[list[list[float]]]:
    triangles = []
    for piece in constrained_delaunay_triangles(poly).geoms:
        if piece.area < 1e-7:
            continue
        triangles.append([[round(x-origin[0], 4), round(y-origin[1], 4),
                           round(sample(x, y)-origin[2], 4)]
                          for x, y in list(piece.exterior.coords)[:3]])
    return triangles


def building_height(props: dict[str, Any]) -> tuple[float | None, str]:
    for key in ("height", "height_m"):
        try:
            height = float(props[key])
            if 0 < height < 500 and math.isfinite(height):
                return height, "cartographic_height"
        except (KeyError, TypeError, ValueError):
            pass
    for key in ("num_floors", "building:levels"):
        try:
            floors = float(props[key])
            if 0 < floors < 150 and math.isfinite(floors):
                return floors * 3.0, "estimated_from_floor_count_3m"
        except (KeyError, TypeError, ValueError):
            pass
    return None, "unknown"


def compile_package(manifest_path: Path, output: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "green-atlas.metric-scene-input.v1":
        raise ValueError("Unsupported scene input schema")
    cad_origin = manifest.get("cad_origin")
    if cad_origin not in {"autocad_capture_derived", "historical_legacy_processed_fixture"}:
        raise ValueError("CAD origin must identify native capture or an explicitly diagnostic legacy fixture")
    inputs = {}
    for key, row in manifest["inputs"].items():
        inputs[key] = read_input(manifest_path, row)
    surfaces = inputs["cad_surfaces"][0]
    elevations = inputs["elevation_controls"][0]
    mapped = inputs.get("map_buildings", (None, None))[0]
    if not surfaces["features"]:
        raise ValueError("No authored CAD surfaces")
    classified = []
    warnings = []
    for index, feature in enumerate(surfaces["features"]):
        poly = shape(feature["geometry"])
        props = feature.get("properties", {})
        kind = props.get("class")
        if not isinstance(poly, Polygon) or not poly.is_valid or poly.area <= 0 or kind not in SURFACE_CLASSES:
            warnings.append({"source": "cad_surfaces", "feature": index, "reason": "unclassified_or_invalid_surface"})
            continue
        classified.append((poly, props))
    if not classified:
        raise ValueError("No classified valid CAD surface")
    scope = unary_union([poly for poly, _ in classified])
    bbox = scope.bounds
    terrain, sample, ground_hull = fit_ground(elevations, bbox, manifest["vertical_datum"])
    terrain["authored_surface_coverage_ratio"] = round(float(scope.intersection(ground_hull).area / scope.area), 6)
    if terrain["control_hull_bbox_coverage_ratio"] < 0.8:
        warnings.append({"source": "elevation_controls", "reason": "terrain_controls_cover_less_than_80_percent_of_bbox"})
    origin = [(bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2, sample((bbox[0]+bbox[2])/2,(bbox[1]+bbox[3])/2)]
    alignment = None
    if mapped:
        supplied = inputs.get("alignment", (None, None))[0]
        if supplied:
            alignment = read_alignment(supplied)
        elif inputs.get("cad_buildings", (None, None))[0]:
            alignment = candidate_alignment(inputs["cad_buildings"][0], mapped)
        else:
            warnings.append({"source": "map_buildings", "reason": "no_alignment_or_addressed_cad_buildings"})
    lawn = unary_union([poly for poly, props in classified if props["class"] == "lawn"])
    building_rows = []
    mapped_footprints = []
    cad_footprints = []
    cad_buildings = inputs.get("cad_buildings", (None, None))[0]
    if cad_buildings:
        for index, feature in enumerate(cad_buildings["features"]):
            geom = shape(feature["geometry"])
            if not isinstance(geom, Polygon) or not geom.is_valid or geom.area <= 0 or not geom.intersects(box(*bbox)):
                continue
            if not ground_hull.covers(geom.centroid):
                warnings.append({"source": "cad_buildings", "feature": index, "reason": "no_ground_control_support"})
                continue
            props = feature.get("properties", {})
            cad_footprints.append(geom)
            height, height_status = building_height(props)
            building_rows.append({"id": str(props.get("source_handle") or feature.get("id") or f"cad-building:{index}"),
                                  "footprint": [[round(x-origin[0], 4), round(y-origin[1], 4)] for x,y in geom.exterior.coords],
                                  "ground_z": round(sample(geom.centroid.x, geom.centroid.y)-origin[2], 4),
                                  "height_m": height, "height_status": height_status,
                                  "alignment_status": "authored_cad_xy",
                                  "address": props.get("address")})
    cad_building_union = unary_union(cad_footprints) if cad_footprints else Polygon()
    if mapped and alignment:
        for index, feature in enumerate(mapped["features"]):
            geom = transform_geometry(shape(feature["geometry"]), alignment)
            if not isinstance(geom, Polygon) or not geom.is_valid or geom.area <= 0 or not geom.intersects(box(*bbox)):
                continue
            if not ground_hull.covers(geom.centroid):
                warnings.append({"source": "map_buildings", "feature": index, "reason": "no_ground_control_support"})
                continue
            if not cad_building_union.is_empty and geom.intersection(cad_building_union).area / geom.area > 0.5:
                continue
            props = feature.get("properties", {})
            mapped_footprints.append(geom)
            height, height_status = building_height(props)
            building_rows.append({"id": str(feature.get("id") or f"mapped:{index}"),
                                  "footprint": [[round(x-origin[0], 4), round(y-origin[1], 4)] for x,y in geom.exterior.coords],
                                  "ground_z": round(sample(geom.centroid.x, geom.centroid.y)-origin[2], 4),
                                  "height_m": height, "height_status": height_status,
                                  "alignment_status": alignment["status"],
                                  "address": props.get("addr:housenumber")})
    building_union = unary_union(cad_footprints + mapped_footprints) if cad_footprints or mapped_footprints else Polygon()
    surface_rows = []
    for index, (poly, props) in enumerate(classified):
        remaining = poly.difference(building_union) if props["class"] == "lawn" else poly
        remaining = remaining.intersection(ground_hull)
        parts = [remaining] if isinstance(remaining, Polygon) else list(getattr(remaining, "geoms", []))
        triangles = [triangle for part in parts if isinstance(part, Polygon) and part.area > 1e-7
                     for triangle in mesh_polygon(part, sample, origin)]
        if not triangles:
            warnings.append({"source": "cad_surfaces", "feature": index, "reason": "no_ground_control_support_or_occluded_by_building"})
            continue
        surface_rows.append({"id": props.get("source_handle") or f"surface:{index}",
                             "class": props["class"], "evidence": "authored_cad_xy_estimated_ground_z",
                             "source_layer": props.get("source_layer"), "triangles": triangles})
    road_axes = []
    roads_input = inputs.get("map_roads", (None, None))[0]
    if roads_input and alignment:
        for index, feature in enumerate(roads_input["features"]):
            geometry = transform_geometry(shape(feature["geometry"]), alignment).intersection(ground_hull)
            lines = [geometry] if isinstance(geometry, LineString) else list(geometry.geoms) if isinstance(geometry, MultiLineString) else []
            for line in lines:
                if not line.intersects(box(*bbox)):
                    continue
                coords = [[round(x-origin[0], 4), round(y-origin[1], 4), round(sample(x,y)-origin[2], 4)]
                          for x,y in line.coords]
                road_axes.append({"id": str(feature.get("id") or f"map-road:{index}"), "xyz": coords,
                                  "width_m": feature.get("properties", {}).get("width"),
                                  "status": "cartographic_centerline_width_not_assumed"})
    trees = []
    records = inputs.get("inventory_records", (None, None))[0]
    positions = inputs.get("inventory_positions", (None, None))[0]
    if records and positions:
        for ident, record in records.items():
            pos = positions.get("positions", {}).get(ident)
            if record.get("kind") != "tree" or not pos or pos.get("position_status") != "matched_marker":
                continue
            xy = pos.get("xy_dxf_m")
            if (not xy or not lawn.covers(Point(xy)) or not ground_hull.covers(Point(xy))
                    or building_union.covers(Point(xy))
                    or not isinstance(record.get("height_m"), (int, float))):
                continue
            trees.append({"id": ident, "species": record.get("species"), "height_m": record["height_m"],
                          "xyz": [round(xy[0]-origin[0], 4), round(xy[1]-origin[1], 4),
                                  round(sample(*xy)-origin[2], 4)],
                          "evidence": "inventory_exact_marker_on_authored_lawn"})
    packet = {"schema": "green-atlas.metric-scene.v1",
              "status": "candidate_scene_requires_review" if cad_origin == "autocad_capture_derived" else "diagnostic_legacy_fixture_not_product_input",
              "origin_cad_xy_height_m": origin,
              "scope_bounds_local_m": [round(bbox[0]-origin[0], 4), round(bbox[1]-origin[1], 4),
                                       round(bbox[2]-origin[0], 4), round(bbox[3]-origin[1], 4)],
              "alignment": alignment, "terrain": terrain,
              "surfaces": surface_rows, "buildings": building_rows, "trees": trees, "road_axes": road_axes,
              "warnings": warnings, "sources": {k: receipt for k, (_, receipt) in inputs.items() if receipt},
              "summary": {"surfaces": len(surface_rows), "buildings": len(building_rows),
                          "buildings_without_height": sum(row["height_m"] is None for row in building_rows),
                          "trees": len(trees), "road_axes": len(road_axes),
                          "surface_classes": dict(Counter(row["class"] for row in surface_rows)),
                          "alignment_status": alignment["status"] if alignment else "unavailable"}}
    output.mkdir(parents=True, exist_ok=True)
    (output / "scene.json").write_text(json.dumps(packet, ensure_ascii=False, separators=(",", ":")))
    (output / "receipt.json").write_text(json.dumps({"manifest_sha256": digest(manifest_path),
                                                      "scene_sha256": digest(output / "scene.json"),
                                                      "summary": packet["summary"], "warnings": warnings},
                                                     ensure_ascii=False, indent=2))
    return packet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    packet = compile_package(args.manifest.resolve(), args.output.resolve())
    print(json.dumps(packet["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
