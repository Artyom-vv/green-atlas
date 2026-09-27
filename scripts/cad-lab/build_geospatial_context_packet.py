"""Build a provenance-preserving urban context packet around a render scene.

The project DXF remains the authority inside the design area.  This tool adds
the surrounding city from a previously captured OSM snapshot after applying a
reviewed *candidate* alignment.  It deliberately keeps inferred road widths
and heights distinct from measured geometry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from shapely.geometry import LineString, Point, Polygon, box, mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT = ROOT / ".runtime/deterministic-render-audit-20260919"
DEFAULT_SCENE = ROOT / ".runtime/deterministic-render-pipeline-20260920/scene-packet.json"
DEFAULT_OUTPUT = ROOT / ".runtime/geospatial-context-packet-20260920"

ROAD_WIDTH_DEFAULTS_M = {
    "tertiary": 12.0,
    "residential": 8.0,
    "service": 4.0,
    "footway": 2.0,
    "path": 1.5,
    "steps": 2.0,
}

# Small, reviewable enrichment for the nearest building whose OSM footprint has
# no vertical metadata.  The cited property record reports one floor; the 4.2 m
# extrusion remains an explicit render estimate, not a measured building height.
BUILDING_HEIGHT_OVERRIDES = {
    "93040605": {
        "height_m": 4.2,
        "source": "https://www.cian.ru/torgovyy-centr-smoll-shipilovskiy-moskva-215240/",
        "evidence": "Шипиловская улица 62А; one floor; 956 m2; built 2005",
        "retrieved_on": "2026-09-20",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tags(element: ET.Element) -> dict[str, str]:
    return {row.attrib["k"]: row.attrib["v"] for row in element.findall("tag")}


def local_tangent(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    radius = 6_378_137.0
    return (
        radius * math.radians(lon-lon0) * math.cos(math.radians(lat0)),
        radius * math.radians(lat-lat0),
    )


def transform(point: tuple[float, float], alignment: dict[str, Any]) -> tuple[float, float]:
    scale = alignment["scale"]
    rotation = alignment["rotation"]
    translation = alignment["translation"]
    x, y = point
    return (
        scale * (x*rotation[0][0] + y*rotation[1][0]) + translation[0],
        scale * (x*rotation[0][1] + y*rotation[1][1]) + translation[1],
    )


def parse_number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
    return float(match.group(0).replace(",", ".")) if match else None


def building_height(osm_id: str, row: dict[str, str]) -> tuple[float | None, str, str, dict[str, Any] | None]:
    override = BUILDING_HEIGHT_OVERRIDES.get(osm_id)
    if override:
        return (
            override["height_m"], "external_floor_count_render_estimate",
            "externally_verified_floor_count_estimated_height", override,
        )
    explicit = parse_number(row.get("height"))
    if explicit and explicit > 0:
        return explicit, "osm:height", "cartographic_explicit", None
    levels = parse_number(row.get("building:levels"))
    roof = parse_number(row.get("roof:height")) or 0.0
    if levels and levels > 0:
        return levels*3.0 + roof, "osm:building:levels*3m+roof:height", "estimated_from_levels", None
    return None, "missing", "unknown", None


def road_width(row: dict[str, str]) -> tuple[float, str, str]:
    explicit = parse_number(row.get("width"))
    if explicit and explicit > 0:
        return explicit, "osm:width", "cartographic_explicit"
    lanes = parse_number(row.get("lanes"))
    highway = row.get("highway", "")
    if lanes and lanes > 0 and highway not in {"footway", "path", "steps"}:
        return lanes*3.1, "osm:lanes*3.1m", "render_estimate"
    return ROAD_WIDTH_DEFAULTS_M.get(highway, 4.0), f"class_default:{highway or 'unknown'}", "render_estimate"


def rounded_geometry(geometry: dict[str, Any], digits: int = 6) -> dict[str, Any]:
    def visit(value: Any) -> Any:
        if isinstance(value, float):
            return round(value, digits)
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            return {key: visit(item) for key, item in value.items()}
        return value
    return visit(geometry)


def make_svg(features: list[dict[str, Any]], design_scope: Polygon, output: Path) -> None:
    geometries = [feature["geometry_local"] for feature in features]
    bounds = design_scope.bounds
    for geometry in geometries:
        shape_bounds = geometry.bounds
        bounds = (
            min(bounds[0], shape_bounds[0]), min(bounds[1], shape_bounds[1]),
            max(bounds[2], shape_bounds[2]), max(bounds[3], shape_bounds[3]),
        )
    min_x, min_y, max_x, max_y = bounds
    margin_m = 25.0
    min_x -= margin_m; min_y -= margin_m; max_x += margin_m; max_y += margin_m
    width, height, margin = 1400, 1400, 55
    scale = min((width-2*margin)/(max_x-min_x), (height-2*margin)/(max_y-min_y))

    def project(x: float, y: float) -> tuple[float, float]:
        return margin+(x-min_x)*scale, height-margin-(y-min_y)*scale

    def path(points: Any, close: bool = False) -> str:
        pixels = [project(*point) for point in points]
        value = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pixels)
        return value + (" Z" if close else "")

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#eef1f2"/>',
        '<g stroke-linecap="round" stroke-linejoin="round">',
    ]
    road_colors = {"tertiary": "#c6c7c7", "residential": "#d2d2cf", "service": "#dddeda",
                   "footway": "#e8d9c2", "path": "#d9cfb7", "steps": "#e8d9c2"}
    for feature in features:
        props = feature["properties"]
        geometry = feature["geometry_local"]
        if props["kind"] != "transportation" or not isinstance(geometry, LineString):
            continue
        color = road_colors.get(props.get("highway"), "#dadbd9")
        svg.append(f'<path d="{path(geometry.coords)}" fill="none" stroke="{color}" stroke-width="{max(1.2, props["width_m"]*scale):.1f}"/>')
        svg.append(f'<path d="{path(geometry.coords)}" fill="none" stroke="#9ca2a1" stroke-width="0.7" opacity="0.7"/>')
    for feature in features:
        props = feature["properties"]
        geometry = feature["geometry_local"]
        if props["kind"] == "building" and isinstance(geometry, Polygon):
            fill = "#9aa7ab" if props["height_m"] is not None else "#bdc5c7"
            svg.append(f'<path d="{path(geometry.exterior.coords, True)}" fill="{fill}" stroke="#445257" stroke-width="1"/>')
    x1, y1 = project(design_scope.bounds[0], design_scope.bounds[1])
    x2, y2 = project(design_scope.bounds[2], design_scope.bounds[3])
    svg.append(f'<rect x="{x1:.1f}" y="{y2:.1f}" width="{x2-x1:.1f}" height="{y1-y2:.1f}" fill="none" stroke="#e34234" stroke-width="5"/>')
    svg.extend([
        '</g>',
        '<g font-family="Arial, sans-serif">',
        '<rect x="35" y="28" width="690" height="92" rx="8" fill="#ffffff" opacity="0.93"/>',
        '<text x="55" y="62" font-size="24" font-weight="700" fill="#1b272a">OSM-контекст, совмещённый с проектным DXF</text>',
        '<text x="55" y="91" font-size="16" fill="#445257">Красный прямоугольник — прежнее окно 53×43 м. Серое — реальные контуры зданий.</text>',
        '<text x="55" y="112" font-size="14" fill="#9a3c31">Candidate alignment: контекст для сцены, не геодезическая основа проекта.</text>',
        '</g></svg>',
    ])
    output.write_text("\n".join(svg))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--osm", type=Path, default=DEFAULT_AUDIT/"kustanayskaya-osm.xml")
    parser.add_argument("--alignment", type=Path, default=DEFAULT_AUDIT/"candidate-osm-alignment.json")
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    alignment_receipt = json.loads(args.alignment.read_text())
    alignment = alignment_receipt["similarity_transform_row_vector"]
    lon0, lat0 = alignment_receipt["projection_before_fit"]["reference_lon_lat"]
    scene = json.loads(args.scene.read_text())
    origin = scene["coordinates"]["local_origin_xyz"]
    bbox_source = scene["coordinates"]["scope_bbox_source_xy_m"]
    design_scope = box(
        bbox_source[0]-origin[0], bbox_source[1]-origin[1],
        bbox_source[2]-origin[0], bbox_source[3]-origin[1],
    )

    root = ET.parse(args.osm).getroot()
    nodes_wgs84 = {
        node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
        for node in root.findall("node")
    }

    def local_coordinates(refs: list[str]) -> list[tuple[float, float]]:
        result = []
        for ref in refs:
            if ref not in nodes_wgs84:
                continue
            tangent = local_tangent(*nodes_wgs84[ref], lon0, lat0)
            x, y = transform(tangent, alignment)
            result.append((x-origin[0], y-origin[1]))
        return result

    features: list[dict[str, Any]] = []
    buildings_total = buildings_with_height = roads_total = estimated_widths = 0
    for way in root.findall("way"):
        row = tags(way)
        refs = [item.attrib["ref"] for item in way.findall("nd")]
        coordinates = local_coordinates(refs)
        if "building" in row and len(coordinates) >= 4 and coordinates[0] == coordinates[-1]:
            polygon = Polygon(coordinates)
            if not polygon.is_valid or polygon.area <= 1.0:
                continue
            height, height_source, height_confidence, external_evidence = building_height(way.attrib["id"], row)
            buildings_total += 1
            buildings_with_height += int(height is not None)
            features.append({
                "geometry_local": polygon,
                "properties": {
                    "id": f"osm:way:{way.attrib['id']}", "kind": "building",
                    "osm_id": way.attrib["id"], "height_m": height,
                    "height_source": height_source, "height_confidence": height_confidence,
                    "external_height_evidence": external_evidence,
                    "building": row.get("building"), "levels": row.get("building:levels"),
                    "address": row.get("addr:housenumber"),
                    "alignment_status": alignment_receipt["status"],
                },
            })
        if "highway" in row and len(coordinates) >= 2:
            line = LineString(coordinates)
            width, width_source, width_confidence = road_width(row)
            roads_total += 1
            estimated_widths += int(width_confidence == "render_estimate")
            features.append({
                "geometry_local": line,
                "properties": {
                    "id": f"osm:way:{way.attrib['id']}", "kind": "transportation",
                    "osm_id": way.attrib["id"], "highway": row.get("highway"),
                    "name": row.get("name"), "surface": row.get("surface"),
                    "lanes": row.get("lanes"), "width_m": width,
                    "width_source": width_source, "width_confidence": width_confidence,
                    "alignment_status": alignment_receipt["status"],
                },
            })

    point_keys = ("highway", "amenity", "barrier", "man_made")
    point_count = 0
    for node in root.findall("node"):
        row = tags(node)
        if not any(key in row for key in point_keys):
            continue
        tangent = local_tangent(float(node.attrib["lon"]), float(node.attrib["lat"]), lon0, lat0)
        x, y = transform(tangent, alignment)
        geometry = Point(x-origin[0], y-origin[1])
        features.append({
            "geometry_local": geometry,
            "properties": {
                "id": f"osm:node:{node.attrib['id']}", "kind": "street_object_candidate",
                "osm_id": node.attrib["id"], "tags": row,
                "alignment_status": alignment_receipt["status"],
            },
        })
        point_count += 1

    features.sort(key=lambda feature: (feature["properties"]["kind"], feature["properties"]["id"]))
    packet_features = [
        {
            "type": "Feature",
            "geometry": rounded_geometry(mapping(feature["geometry_local"])),
            "properties": feature["properties"],
        }
        for feature in features
    ]
    packet = {
        "schema": "green-atlas.geospatial-context-packet.v1",
        "status": "candidate_context_not_survey_control",
        "sources": {
            "osm": {"path": str(args.osm.resolve()), "sha256": sha256(args.osm)},
            "alignment": {"path": str(args.alignment.resolve()), "sha256": sha256(args.alignment)},
            "project_scene": {"path": str(args.scene.resolve()), "sha256": sha256(args.scene)},
        },
        "coordinate_contract": {
            "feature_coordinates": "metres relative to project scene local_origin_xyz",
            "local_origin_xyz": origin,
            "alignment_status": alignment_receipt["status"],
            "fit_rmse_m": alignment_receipt["ransac"]["rmse_inliers_m"],
            "fit_max_residual_m": alignment_receipt["ransac"]["max_inlier_residual_m"],
            "independent_survey_checkpoints": 0,
        },
        "authority_policy": {
            "inside_design_area": "project/topographic DXF overrides external context",
            "outside_design_area": "aligned OSM context is allowed with visible candidate status",
            "terrain": "survey TIN inside; external DEM only for broad surrounding grade",
            "appearance": "street imagery may validate materials but cannot silently create geometry",
        },
        "design_scope_local": rounded_geometry(mapping(design_scope)),
        "quality": {
            "building_count": buildings_total,
            "buildings_with_height_or_levels": buildings_with_height,
            "building_height_coverage": round(buildings_with_height/buildings_total, 6) if buildings_total else 0.0,
            "transportation_count": roads_total,
            "transportation_width_estimates": estimated_widths,
            "street_object_candidates": point_count,
            "truth_scene_eligible": False,
            "context_visualization_eligible": True,
            "blocking_reason": "alignment has no independent surveyed checkpoints",
        },
        "features": packet_features,
    }
    packet_path = args.output/"context-packet.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    geojson_path = args.output/"context-local.geojson"
    geojson_path.write_text(json.dumps({"type": "FeatureCollection", "features": packet_features}, ensure_ascii=False, indent=2))
    make_svg(features, design_scope, args.output/"context-overview.svg")
    receipt = {
        "schema": "green-atlas.geospatial-context-receipt.v1",
        "packet": str(packet_path.resolve()), "packet_sha256": sha256(packet_path),
        "counts": packet["quality"],
    }
    (args.output/"receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
