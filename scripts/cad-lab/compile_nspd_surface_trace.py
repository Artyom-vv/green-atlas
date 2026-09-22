#!/usr/bin/env python3
"""Compile pixel-space NSPD traces into a provenance-preserving local packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from shapely.geometry import GeometryCollection, Point, mapping, shape
from shapely.ops import transform as shapely_transform


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRACE = ROOT / "fixtures/spatial-evidence/kustanayskaya-nspd-surface-trace-v1.json"
DEFAULT_OUTPUT = ROOT / ".runtime/nspd-surface-trace-kustanayskaya-20260920"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    trace = json.loads(args.trace.read_text())
    ortho_manifest_path = ROOT / trace["source"]["orthophoto_manifest"]
    world_path = ROOT / trace["source"]["world"]
    kartaview_path = ROOT / trace["source"]["kartaview_manifest"]
    ortho = json.loads(ortho_manifest_path.read_text())
    world = json.loads(world_path.read_text())
    kartaview = json.loads(kartaview_path.read_text())
    if ortho["mosaic"]["cropped_sha256"] != trace["source"]["orthophoto_sha256"]:
        raise ValueError("orthophoto hash differs from trace source")
    if sha256(world_path) != trace["source"]["world_sha256"]:
        raise ValueError("world hash differs from trace source")

    zoom = int(ortho["mosaic"]["zoom"])
    x_min = int(ortho["mosaic"]["tile_range"]["x"][0])
    y_min = int(ortho["mosaic"]["tile_range"]["y"][0])
    crop_left, crop_top, _, _ = ortho["mosaic"]["crop_pixels"]
    origin_lon, origin_lat = world["coordinate_frame"]["origin_wgs84_lon_lat"]
    earth_radius = float(world["coordinate_frame"]["earth_radius_m"])
    scale = 2**zoom

    def pixel_to_lonlat(point: list[float]) -> list[float]:
        pixel_x, pixel_y = map(float, point[:2])
        tile_x = x_min + (crop_left + pixel_x) / 256.0
        tile_y = y_min + (crop_top + pixel_y) / 256.0
        lon = tile_x / scale * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * tile_y / scale))))
        return [lon, lat]

    def lonlat_to_local(point: list[float]) -> list[float]:
        lon, lat = map(float, point[:2])
        x = math.radians(lon - origin_lon) * earth_radius * math.cos(math.radians(origin_lat))
        y = math.radians(lat - origin_lat) * earth_radius
        return [x, y]

    def local_to_lonlat_xy(x: float, y: float, z: float | None = None):
        lon = origin_lon + math.degrees(x / (earth_radius * math.cos(math.radians(origin_lat))))
        lat = origin_lat + math.degrees(y / earth_radius)
        return (lon, lat) if z is None else (lon, lat, z)

    def pixel_geometry_to(geometry: dict, converter) -> dict:
        return {
            "type": "Polygon",
            "coordinates": [[converter(point) for point in ring] for ring in geometry["coordinates"]],
        }

    x0, y0, x1, y1 = trace["scene_roi_pixels"]
    roi_pixels = {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }
    roi_lonlat = pixel_geometry_to(roi_pixels, pixel_to_lonlat)
    roi_local = pixel_geometry_to(roi_lonlat, lonlat_to_local)
    roi_shape = shape(roi_local)

    compiled_features = []
    for feature in trace["features"]:
        geometry_wgs84 = pixel_geometry_to(feature["pixel_geometry"], pixel_to_lonlat)
        geometry_local = pixel_geometry_to(geometry_wgs84, lonlat_to_local)
        geom = shape(geometry_local)
        if not geom.is_valid or geom.area <= 0:
            raise ValueError(f"invalid trace geometry: {feature['id']}")
        clipped = geom.intersection(roi_shape)
        compiled_features.append(
            {
                **feature,
                "geometry_wgs84": mapping(shape(geometry_wgs84)),
                "geometry_local": mapping(clipped),
                "area_m2": round(clipped.area, 6),
                "source_orthophoto_sha256": ortho["mosaic"]["cropped_sha256"],
            }
        )

    world_by_id = {
        item["id"]: item
        for collection in (world["roads"], world["buildings"])
        for item in collection
    }
    referenced = []
    for reference in trace["referenced_world_features"]:
        source_feature = world_by_id.get(reference["id"])
        if source_feature is None:
            raise ValueError(f"missing world feature: {reference['id']}")
        geometry_key = "surface_geometry_local" if reference["semantic"] == "road" else "geometry_local"
        clipped = shape(source_feature[geometry_key]).intersection(roi_shape)
        referenced.append(
            {
                **reference,
                "geometry_local": mapping(clipped),
                "area_m2": round(clipped.area, 6),
                "properties": source_feature["properties"],
            }
        )

    reference_shapes = {item["semantic"]: shape(item["geometry_local"]) for item in referenced}
    semantic_priority = {"parking_apron": 30, "sidewalk": 20, "lawn": 10}
    source_conflicts = []
    for feature in compiled_features:
        raw = shape(feature["geometry_local"])
        resolved = raw
        clipped_by = []
        for semantic in ("building", "road"):
            mask = reference_shapes.get(semantic)
            if mask is None:
                continue
            intersection_area = raw.intersection(mask).area
            if intersection_area > 1e-6:
                source_conflicts.append(
                    {
                        "surface_id": feature["id"],
                        "reference_semantic": semantic,
                        "intersection_area_m2": round(intersection_area, 6),
                    }
                )
                resolved = resolved.difference(mask)
                clipped_by.append(semantic)
        feature["priority"] = semantic_priority[feature["semantic"]]
        feature["preliminary_geometry_local"] = mapping(resolved)
        feature["resolution"] = {
            "policy": "building_then_satellite_width_road_then_higher_priority_trace",
            "clipped_by": clipped_by,
            "removed_area_m2": 0.0,
        }

    occupied = GeometryCollection()
    for feature in sorted(compiled_features, key=lambda item: (-item["priority"], item["id"])):
        raw = shape(feature["geometry_local"])
        resolved = shape(feature["preliminary_geometry_local"])
        overlap = resolved.intersection(occupied)
        if overlap.area > 1e-6:
            source_conflicts.append(
                {
                    "surface_id": feature["id"],
                    "reference_semantic": "higher_priority_trace",
                    "intersection_area_m2": round(overlap.area, 6),
                }
            )
            resolved = resolved.difference(occupied)
            feature["resolution"]["clipped_by"].append("higher_priority_trace")
        feature["resolved_geometry_local"] = mapping(resolved)
        feature["resolved_geometry_wgs84"] = mapping(
            shapely_transform(local_to_lonlat_xy, resolved)
        )
        feature["resolved_area_m2"] = round(resolved.area, 6)
        feature["resolution"]["removed_area_m2"] = round(raw.area - resolved.area, 6)
        occupied = occupied.union(resolved)

    resolved_lawns = [
        shape(feature["resolved_geometry_local"])
        for feature in compiled_features
        if feature["semantic"] == "lawn"
    ]
    vegetation = []
    for candidate in trace.get("vegetation_candidates", []):
        lonlat = pixel_to_lonlat(candidate["pixel_xy"])
        local = lonlat_to_local(lonlat)
        support = [index for index, lawn in enumerate(resolved_lawns) if lawn.covers(Point(local))]
        vegetation.append(
            {
                **candidate,
                "wgs84_lon_lat": lonlat,
                "local_xy_m": local,
                "resolved_lawn_support_indices": support,
                "source_orthophoto_sha256": ortho["mosaic"]["cropped_sha256"],
            }
        )

    cameras = []
    for frame in kartaview["frames"]:
        x, y = frame["camera"]["local_xy_m"]
        if roi_shape.covers(Point(x, y)):
            cameras.append(
                {
                    "photo_id": frame["photo_id"],
                    "sequence_index": frame["sequence_index"],
                    "local_xy_m": [x, y],
                    "heading_deg": frame["camera"]["heading_deg"],
                    "image_sha256": frame["sha256"],
                }
            )

    overlap_rows = []
    for index, feature in enumerate(compiled_features):
        first = shape(feature["resolved_geometry_local"])
        for other in compiled_features[index + 1 :]:
            area = first.intersection(shape(other["resolved_geometry_local"])).area
            if area > 1e-6:
                overlap_rows.append({"a": feature["id"], "b": other["id"], "area_m2": round(area, 6)})

    packet = {
        "schema": "green-atlas.orthophoto-surface-packet.v1",
        "scene_id": trace["scene_id"],
        "source": {
            "trace_path": str(args.trace.relative_to(ROOT)),
            "trace_sha256": sha256(args.trace),
            "orthophoto_manifest": trace["source"]["orthophoto_manifest"],
            "orthophoto_sha256": ortho["mosaic"]["cropped_sha256"],
            "world": trace["source"]["world"],
            "world_sha256": sha256(world_path),
            "kartaview_manifest": trace["source"]["kartaview_manifest"],
            "kartaview_manifest_sha256": sha256(kartaview_path),
        },
        "scene_roi": {
            "pixels": trace["scene_roi_pixels"],
            "geometry_wgs84": roi_lonlat,
            "geometry_local": roi_local,
            "area_m2": round(roi_shape.area, 6),
        },
        "traced_surfaces": compiled_features,
        "referenced_world_features": referenced,
        "vegetation_candidates": vegetation,
        "kartaview_cameras": cameras,
        "source_conflicts": source_conflicts,
        "surface_overlaps": overlap_rows,
        "admission": trace["admission"],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    packet_path = args.output / "surface-packet.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")

    geojson_features = []
    for feature in compiled_features:
        geojson_features.append(
            {
                "type": "Feature",
                "id": feature["id"],
                "geometry": feature["resolved_geometry_wgs84"],
                "properties": {
                    "semantic": feature["semantic"],
                    "status": feature["review"]["status"],
                    "horizontal_uncertainty_m": feature["review"]["horizontal_uncertainty_m"],
                    "source_sha256": feature["source_orthophoto_sha256"],
                    "source_area_m2": feature["area_m2"],
                    "resolved_area_m2": feature["resolved_area_m2"],
                    "resolution": feature["resolution"],
                },
            }
        )
    (args.output / "surface-traces-wgs84.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": geojson_features}, ensure_ascii=False, indent=2) + "\n"
    )
    receipt = {
        "schema": "green-atlas.orthophoto-surface-receipt.v1",
        "packet_sha256": sha256(packet_path),
        "traced_feature_count": len(compiled_features),
        "referenced_feature_count": len(referenced),
        "kartaview_camera_count": len(cameras),
        "vegetation_candidate_count": len(vegetation),
        "source_conflict_count": len(source_conflicts),
        "overlap_count": len(overlap_rows),
        "admission": packet["admission"]["status"],
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
