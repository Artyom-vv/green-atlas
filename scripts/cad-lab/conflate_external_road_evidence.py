"""Conflate independent satellite road widths with the Overture/OSM base.

The output is a reviewed-input candidate packet.  It recommends widths only
when the two independently sourced centreline geometries overlap strongly.
It never edits the world packet itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Polygon, box, shape
from shapely.ops import transform as transform_geometry


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
DEFAULT_EVIDENCE = ROOT / ".runtime/microsoft-road-evidence-20260920/roads.geojson"
DEFAULT_OUTPUT = ROOT / ".runtime/conflated-road-evidence-20260920"
EARTH_RADIUS_M = 6_378_137.0
NON_MOTOR_CLASSES = {"footway", "path", "steps", "cycleway", "pedestrian"}


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
    if isinstance(value, dict):
        return {key: rounded(item, digits) for key, item in value.items()}
    return value


def line_parts(geometry: Any) -> list[LineString]:
    if isinstance(geometry, LineString):
        return [geometry]
    if hasattr(geometry, "geoms"):
        result: list[LineString] = []
        for part in geometry.geoms:
            result.extend(line_parts(part))
        return result
    return []


def direction_deg(line: LineString) -> float:
    start = line.interpolate(line.length * 0.1)
    end = line.interpolate(line.length * 0.9)
    return math.degrees(math.atan2(end.y - start.y, end.x - start.x)) % 180.0


def angular_delta(a: float, b: float) -> float:
    delta = abs(a - b) % 180.0
    return min(delta, 180.0 - delta)


def weighted_median(rows: list[tuple[float, float]]) -> float:
    ordered = sorted(rows)
    total = sum(weight for _, weight in ordered)
    cursor = 0.0
    for value, weight in ordered:
        cursor += weight
        if cursor >= total / 2.0:
            return value
    return ordered[-1][0]


def write_overview(packet: dict[str, Any], output: Path) -> None:
    width, height, margin = 1500, 1300, 50
    min_x, min_y, max_x, max_y = packet["scope"]["local_bounds_m"]
    scale = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))

    def project(x: float, y: float) -> tuple[float, float]:
        return margin + (x - min_x) * scale, height - margin - (y - min_y) * scale

    def polygon_path(polygon: Polygon) -> str:
        rings = [polygon.exterior, *polygon.interiors]
        result = []
        for ring in rings:
            points = [project(float(x), float(y)) for x, y in ring.coords]
            result.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points) + " Z")
        return " ".join(result)

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#edf0ed"/>',
    ]
    for road in packet["overture_roads"]:
        line = shape(road["centerline_geometry_local"])
        polygon = line.buffer(0.65, cap_style=2, join_style=2)
        svg.append(f'<path d="{polygon_path(polygon)}" fill="#a4aaac" fill-rule="evenodd"/>')
    accepted_ids = {
        evidence_id
        for row in packet["recommendations"]
        if row["admission"] == "eligible_for_map_base_width_after_visual_review"
        for evidence_id in row["evidence_ids"]
    }
    for row in packet["evidence_lines"]:
        line = shape(row["geometry_local"])
        accepted = row["id"] in accepted_ids
        color = "#2d8f63" if accepted else "#d66a2d"
        polygon = line.buffer(max(0.9, float(row["width_m"]) * 0.11), cap_style=1, join_style=2)
        svg.append(f'<path d="{polygon_path(polygon)}" fill="{color}" fill-opacity="0.92" fill-rule="evenodd"/>')
    pb = packet["scope"]["project_bbox_local_m"]
    boundary = box(*pb).boundary.buffer(0.65, cap_style=2, join_style=2)
    svg.append(f'<path d="{polygon_path(boundary)}" fill="#15191b" fill-rule="evenodd"/>')
    summary = packet["summary"]
    svg.extend(
        [
            '<rect x="22" y="20" width="930" height="150" rx="9" fill="#fff" fill-opacity="0.96"/>',
            '<g font-family="Arial, sans-serif" fill="#172126">',
            '<text x="45" y="56" font-size="25" font-weight="700">Ширины дорог: спутниковая проверка OSM/Overture</text>',
            '<text x="45" y="88" font-size="17">Серый: OSM/Overture. Зелёный: геометрически согласованный Microsoft. Оранжевый: отклонён.</text>',
            f'<text x="45" y="119" font-size="17">Внешних линий: {summary["evidence_line_count"]}; совпадений: {summary["match_count"]}; '
            f'ширин, прошедших автоматический gate: {summary["eligible_road_count"]}.</text>',
            '<text x="45" y="150" font-size="15" fill="#8b2d21">Gate разрешает только замену proxy-ширины; ось остаётся OSM, ручная проверка всё ещё обязательна.</text>',
            '</g></svg>',
        ]
    )
    svg_path = output / "road-width-evidence.svg"
    svg_path.write_text("\n".join(svg))
    subprocess.run(["magick", str(svg_path), str(output / "road-width-evidence.png")], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--axis-tolerance-m", type=float, default=8.0)
    parser.add_argument("--minimum-road-coverage", type=float, default=0.45)
    parser.add_argument("--maximum-angle-deg", type=float, default=30.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    world = json.loads(args.world.read_text())
    evidence = json.loads(args.evidence.read_text())
    lon0, lat0 = world["coordinate_frame"]["origin_wgs84_lon_lat"]
    cos_lat = math.cos(math.radians(lat0))

    def lonlat_to_local(x: Any, y: Any, z: Any = None) -> Any:
        result = (
            EARTH_RADIUS_M * math.radians(float(x) - lon0) * cos_lat,
            EARTH_RADIUS_M * math.radians(float(y) - lat0),
        )
        return result if z is None else (*result, z)

    local_scope = box(*world["scope"]["local_bounds_m"])
    evidence_lines = []
    for feature in evidence["features"]:
        local = transform_geometry(lonlat_to_local, shape(feature["geometry"])).intersection(local_scope)
        for index, part in enumerate(line_parts(local)):
            if part.length < 2.0:
                continue
            evidence_lines.append(
                {
                    "id": feature["properties"]["evidence_id"] + (f":{index}" if index else ""),
                    "source_id": feature["properties"]["evidence_id"],
                    "geometry_local": rounded(part.__geo_interface__),
                    "width_m": round(float(feature["properties"]["width_m"]), 6),
                    "length_m": round(part.length, 6),
                }
            )

    roads = [
        {
            "id": row["id"],
            "centerline_geometry_local": row["centerline_geometry_local"],
            "properties": row["properties"],
        }
        for row in world["roads"]
    ]
    observations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    matches = []
    for evidence_row in evidence_lines:
        external = shape(evidence_row["geometry_local"])
        external_direction = direction_deg(external)
        for road in roads:
            centerline = shape(road["centerline_geometry_local"])
            distance = external.distance(centerline)
            if distance > args.axis_tolerance_m:
                continue
            road_coverage = centerline.intersection(external.buffer(args.axis_tolerance_m)).length / centerline.length
            external_coverage = external.intersection(centerline.buffer(args.axis_tolerance_m)).length / external.length
            angle = angular_delta(external_direction, direction_deg(centerline))
            if road_coverage < args.minimum_road_coverage or angle > args.maximum_angle_deg:
                continue
            road_class = str(road["properties"].get("class") or "")
            width = float(evidence_row["width_m"])
            class_consistent = not (road_class in NON_MOTOR_CLASSES and width > 4.0)
            overlap_length = centerline.length * road_coverage
            match = {
                "road_id": road["id"],
                "evidence_id": evidence_row["id"],
                "width_m": width,
                "axis_distance_m": round(distance, 6),
                "road_coverage_ratio": round(road_coverage, 6),
                "external_coverage_ratio": round(external_coverage, 6),
                "angle_delta_deg": round(angle, 6),
                "matched_length_m": round(overlap_length, 6),
                "class_consistent": class_consistent,
            }
            matches.append(match)
            if class_consistent:
                observations[road["id"]].append(match)

    recommendations = []
    for road in roads:
        rows = observations.get(road["id"], [])
        if not rows:
            continue
        widths = [float(row["width_m"]) for row in rows]
        recommended = weighted_median([(float(row["width_m"]), float(row["matched_length_m"])) for row in rows])
        mean_width = statistics.fmean(widths)
        coefficient_of_variation = statistics.pstdev(widths) / mean_width if len(widths) > 1 and mean_width else 0.0
        coverage = max(float(row["road_coverage_ratio"]) for row in rows)
        current_status = str(road["properties"].get("width_status") or "")
        eligible = (
            current_status.startswith("render_estimate")
            and coverage >= 0.65
            and coefficient_of_variation <= 0.25
            and 2.5 <= recommended <= 25.0
        )
        recommendations.append(
            {
                "road_id": road["id"],
                "osm_way_ids": road["properties"].get("osm_way_ids") or [],
                "class": road["properties"].get("class"),
                "current_width_m": road["properties"].get("width_m"),
                "current_width_status": current_status,
                "recommended_width_m": round(recommended, 6),
                "delta_m": round(recommended - float(road["properties"]["width_m"]), 6),
                "evidence_ids": sorted({row["evidence_id"] for row in rows}),
                "observation_count": len(rows),
                "maximum_road_coverage_ratio": round(coverage, 6),
                "width_coefficient_of_variation": round(coefficient_of_variation, 6),
                "admission": (
                    "eligible_for_map_base_width_after_visual_review"
                    if eligible
                    else "blocked_insufficient_or_inconsistent_evidence"
                ),
            }
        )

    packet = {
        "schema": "green-atlas.conflated-road-evidence.v1",
        "status": "candidate_widths_require_visual_review",
        "principle": "The OSM/Overture axis remains authoritative for topology; Microsoft may replace only a proxy width after geometric agreement.",
        "scope": world["scope"],
        "coordinate_frame": world["coordinate_frame"],
        "sources": {
            "world": {"path": str(args.world.resolve()), "sha256": sha256(args.world)},
            "microsoft_roads": {"path": str(args.evidence.resolve()), "sha256": sha256(args.evidence)},
        },
        "matching": {
            "axis_tolerance_m": args.axis_tolerance_m,
            "minimum_road_coverage_ratio": args.minimum_road_coverage,
            "maximum_angle_delta_deg": args.maximum_angle_deg,
            "non_motor_maximum_width_m": 4.0,
        },
        "overture_roads": roads,
        "evidence_lines": evidence_lines,
        "matches": sorted(matches, key=lambda row: (row["road_id"], row["evidence_id"])),
        "recommendations": sorted(recommendations, key=lambda row: row["road_id"]),
        "summary": {
            "overture_road_count": len(roads),
            "evidence_line_count": len(evidence_lines),
            "match_count": len(matches),
            "matched_road_count": len(recommendations),
            "eligible_road_count": sum(
                row["admission"] == "eligible_for_map_base_width_after_visual_review"
                for row in recommendations
            ),
        },
        "admission": {
            "automatic_world_mutation": "blocked",
            "review_target": "road-width-evidence.png",
        },
    }
    packet_path = args.output / "road-width-evidence.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_overview(packet, args.output)
    receipt = {
        "schema": "green-atlas.conflated-road-evidence-receipt.v1",
        "status": packet["status"],
        "packet": {"path": str(packet_path.resolve()), "sha256": sha256(packet_path)},
        "overview": {
            "path": str((args.output / "road-width-evidence.png").resolve()),
            "sha256": sha256(args.output / "road-width-evidence.png"),
        },
        "summary": packet["summary"],
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
