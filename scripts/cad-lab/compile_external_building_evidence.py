"""Compare the map-first building layer with independent satellite footprints.

This compiler is deliberately evidence-only.  Microsoft Global ML Building
Footprints may reveal omissions or footprint disagreement, but candidates do
not enter the renderable world until a later reviewed fusion step.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import transform as transform_geometry


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
DEFAULT_MICROSOFT = ROOT / ".runtime/microsoft-buildings-russia-120310101-20260813.csv.gz"
DEFAULT_OUTPUT = ROOT / ".runtime/external-building-evidence-20260920"
EARTH_RADIUS_M = 6_378_137.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rounded(value: Any, digits: int = 6) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [rounded(item, digits) for item in value]
    if isinstance(value, tuple):
        return [rounded(item, digits) for item in value]
    if isinstance(value, dict):
        return {key: rounded(item, digits) for key, item in value.items()}
    return value


def polygon_parts(geometry: Any) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    if hasattr(geometry, "geoms"):
        result: list[Polygon] = []
        for part in geometry.geoms:
            result.extend(polygon_parts(part))
        return result
    return []


def svg_path(polygon: Polygon, project: Any) -> str:
    rings = [polygon.exterior, *polygon.interiors]
    segments = []
    for ring in rings:
        points = [project(float(x), float(y)) for x, y in ring.coords]
        segments.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points) + " Z")
    return " ".join(segments)


def write_overview(packet: dict[str, Any], output: Path) -> None:
    width, height, margin = 1500, 1300, 50
    min_x, min_y, max_x, max_y = packet["scope"]["local_bounds_m"]
    scale = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))

    def project(x: float, y: float) -> tuple[float, float]:
        return margin + (x - min_x) * scale, height - margin - (y - min_y) * scale

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f0f2ef"/>',
    ]
    for row in packet["microsoft_candidates"]:
        polygon = shape(row["geometry_local"])
        for part in polygon_parts(polygon):
            svg.append(
                f'<path d="{svg_path(part, project)}" fill="#dc553f" fill-opacity="0.20" '
                'stroke="#c73526" stroke-width="2.1" fill-rule="evenodd"/>'
            )
    for row in packet["overture_buildings"]:
        polygon = shape(row["geometry_local"])
        for part in polygon_parts(polygon):
            svg.append(
                f'<path d="{svg_path(part, project)}" fill="#3e7ec0" fill-opacity="0.18" '
                'stroke="#215f9f" stroke-width="2.1" fill-rule="evenodd"/>'
            )
    pb = packet["scope"]["project_bbox_local_m"]
    x1, y1 = project(pb[0], pb[1])
    x2, y2 = project(pb[2], pb[3])
    svg.append(
        f'<rect x="{x1:.1f}" y="{y2:.1f}" width="{x2-x1:.1f}" height="{y1-y2:.1f}" '
        'fill="none" stroke="#191d20" stroke-width="3" stroke-dasharray="10 6"/>'
    )
    for camera in packet.get("street_image_cameras", []):
        x, y = project(*camera["xy_local_m"])
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="#f5c542" stroke="#5f4900" stroke-width="2"/>')
    summary = packet["summary"]
    svg.extend(
        [
            '<rect x="22" y="20" width="895" height="164" rx="9" fill="#ffffff" fill-opacity="0.95"/>',
            '<g font-family="Arial, sans-serif" fill="#172126">',
            '<text x="45" y="56" font-size="25" font-weight="700">Независимая проверка контуров зданий</text>',
            '<text x="45" y="88" font-size="17">Синий: Overture/OSM. Красный: Microsoft спутниковая сегментация. Жёлтый: KartaView.</text>',
            f'<text x="45" y="119" font-size="17">Overture: {summary["overture_count"]}; Microsoft: {summary["microsoft_count"]}; '
            f'сопоставлено: {summary["matched_count"]}; только Microsoft: {summary["microsoft_only_count"]}.</text>',
            '<text x="45" y="150" font-size="16" fill="#8b2d21">Красные контуры — кандидаты проверки, не разрешённая для рендера геометрия.</text>',
            '<text x="45" y="174" font-size="14" fill="#4f5c61">Пунктир: проектный bbox. Microsoft height=-1 в этом тайле.</text>',
            '</g></svg>',
        ]
    )
    svg_path_output = output / "building-evidence.svg"
    svg_path_output.write_text("\n".join(svg))
    subprocess.run(["magick", str(svg_path_output), str(output / "building-evidence.png")], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--microsoft", type=Path, default=DEFAULT_MICROSOFT)
    parser.add_argument("--kartaview", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--match-iou", type=float, default=0.20)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    world = json.loads(args.world.read_text())
    lon0, lat0 = world["coordinate_frame"]["origin_wgs84_lon_lat"]
    cos_lat = math.cos(math.radians(lat0))

    def lonlat_to_local(x: Any, y: Any, z: Any = None) -> Any:
        local_x = EARTH_RADIUS_M * math.radians(float(x) - lon0) * cos_lat
        local_y = EARTH_RADIUS_M * math.radians(float(y) - lat0)
        return (local_x, local_y) if z is None else (local_x, local_y, z)

    scope_wgs84 = box(*world["scope"]["context_bbox_wgs84"])
    microsoft: list[dict[str, Any]] = []
    with gzip.open(args.microsoft, "rt") as stream:
        for line_number, line in enumerate(stream, start=1):
            feature = json.loads(line)
            geometry_wgs84 = shape(feature["geometry"])
            if not geometry_wgs84.intersects(scope_wgs84):
                continue
            clipped = geometry_wgs84.intersection(scope_wgs84)
            geometry_local = transform_geometry(lonlat_to_local, clipped)
            for part_index, polygon in enumerate(polygon_parts(geometry_local)):
                if polygon.area < 4.0:
                    continue
                properties = feature.get("properties") or {}
                microsoft.append(
                    {
                        "id": f"microsoft:quadkey:120310101:line:{line_number}:part:{part_index}",
                        "geometry_local": rounded(mapping(polygon)),
                        "properties": {
                            "area_m2": round(polygon.area, 6),
                            "height_m": None if float(properties.get("height", -1)) < 0 else float(properties["height"]),
                            "confidence": None if float(properties.get("confidence", -1)) < 0 else float(properties["confidence"]),
                            "geometry_status": "independent_satellite_candidate_not_render_admitted",
                        },
                    }
                )

    overture = [
        {
            "id": row["id"],
            "geometry_local": row["geometry_local"],
            "properties": {
                "height_m": row["properties"].get("height_m"),
                "height_status": row["properties"].get("height_status"),
                "osm_way_ids": row["properties"].get("osm_way_ids") or [],
            },
        }
        for row in world["buildings"]
    ]

    overture_shapes = {row["id"]: shape(row["geometry_local"]) for row in overture}
    microsoft_shapes = {row["id"]: shape(row["geometry_local"]) for row in microsoft}
    matches: list[dict[str, Any]] = []
    matched_overture: set[str] = set()
    matched_microsoft: set[str] = set()
    for microsoft_id, microsoft_shape in microsoft_shapes.items():
        best = None
        for overture_id, overture_shape in overture_shapes.items():
            if not microsoft_shape.intersects(overture_shape):
                continue
            intersection = microsoft_shape.intersection(overture_shape).area
            union = microsoft_shape.union(overture_shape).area
            iou = intersection / union if union else 0.0
            if best is None or iou > best[0]:
                best = (iou, overture_id, intersection, union)
        if best is None or best[0] < args.match_iou:
            continue
        iou, overture_id, intersection, union = best
        overture_shape = overture_shapes[overture_id]
        matches.append(
            {
                "microsoft_id": microsoft_id,
                "overture_id": overture_id,
                "iou": round(iou, 6),
                "intersection_m2": round(intersection, 6),
                "union_m2": round(union, 6),
                "centroid_delta_m": round(microsoft_shape.centroid.distance(overture_shape.centroid), 6),
                "hausdorff_m": round(microsoft_shape.hausdorff_distance(overture_shape), 6),
                "status": "independent_geometry_agreement_candidate",
            }
        )
        matched_overture.add(overture_id)
        matched_microsoft.add(microsoft_id)

    cameras = []
    if args.kartaview and args.kartaview.exists():
        evidence = json.loads(args.kartaview.read_text())
        evidence_rows = evidence.get("frames") or evidence.get("selected_frames") or []
        for row in evidence_rows:
            camera = row.get("camera") or {}
            coordinates = camera.get("wgs84_lon_lat") or row.get("wgs84_lon_lat")
            if not coordinates:
                # Older manifest form retains the raw API fields.
                coordinates = [row.get("lng"), row.get("lat")]
            if coordinates and None not in coordinates:
                x, y = lonlat_to_local(*coordinates)
                cameras.append({"photo_id": str(row.get("photo_id") or row.get("id")), "xy_local_m": rounded([x, y])})

    microsoft_only = sorted(set(microsoft_shapes) - matched_microsoft)
    overture_only = sorted(set(overture_shapes) - matched_overture)
    packet = {
        "schema": "green-atlas.external-building-evidence.v1",
        "status": "evidence_only_not_render_admitted",
        "principle": "Independent satellite footprints may corroborate or challenge the map base; they never enter render geometry without review.",
        "coordinate_frame": world["coordinate_frame"],
        "scope": world["scope"],
        "sources": {
            "world": {"path": str(args.world.resolve()), "sha256": sha256(args.world)},
            "microsoft_global_ml_building_footprints": {
                "path": str(args.microsoft.resolve()),
                "sha256": sha256(args.microsoft),
                "release": "2026-08-13",
                "quadkey": "120310101",
                "license": "CDLA Permissive 2.0",
                "upstream": "https://github.com/microsoft/GlobalMLBuildingFootprints",
                "limitations": [
                    "Satellite segmentation is independent evidence, not survey geometry.",
                    "All 43 retained candidates in this scope have no published height or confidence value.",
                    "Acquisition dates vary within the global release and may differ from current site state.",
                ],
            },
        },
        "matching": {
            "minimum_iou": args.match_iou,
            "one_best_overture_match_per_microsoft_candidate": True,
            "review_required": True,
        },
        "overture_buildings": overture,
        "microsoft_candidates": microsoft,
        "matches": sorted(matches, key=lambda row: (row["overture_id"], row["microsoft_id"])),
        "microsoft_only_ids": microsoft_only,
        "overture_only_ids": overture_only,
        "street_image_cameras": cameras,
        "summary": {
            "overture_count": len(overture),
            "microsoft_count": len(microsoft),
            "matched_count": len(matches),
            "matched_overture_count": len(matched_overture),
            "matched_microsoft_count": len(matched_microsoft),
            "microsoft_only_count": len(microsoft_only),
            "overture_only_count": len(overture_only),
            "median_match_iou": round(sorted(row["iou"] for row in matches)[len(matches) // 2], 6) if matches else None,
            "median_centroid_delta_m": round(sorted(row["centroid_delta_m"] for row in matches)[len(matches) // 2], 6) if matches else None,
        },
        "admission": {
            "render_geometry": "blocked_pending_feature_review",
            "allowed_uses": ["omission detection", "footprint disagreement audit", "source comparison overview"],
        },
    }
    packet_path = args.output / "building-evidence.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_overview(packet, args.output)
    receipt = {
        "schema": "green-atlas.external-building-evidence-receipt.v1",
        "packet": {"path": str(packet_path.resolve()), "sha256": sha256(packet_path)},
        "overview": {
            "path": str((args.output / "building-evidence.png").resolve()),
            "sha256": sha256(args.output / "building-evidence.png"),
        },
        "summary": packet["summary"],
        "status": packet["status"],
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
