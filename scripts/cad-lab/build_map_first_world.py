"""Compile a deterministic map-first world around Kustanayskaya Street.

The external geographic sources are the world base.  No DXF geometry is used
by this compiler.  The resulting packet keeps measured/cartographic geometry,
render estimates, and unknown values separate so a diagnostic render cannot
silently turn missing data into asserted reality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from shapely import constrained_delaunay_triangles
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.ops import transform as transform_geometry, unary_union


ROOT = Path(__file__).resolve().parents[2]
OVERTURE = ROOT / ".runtime/overture-kustanayskaya-20260920"
DEFAULT_OSM = ROOT / ".runtime/deterministic-render-audit-20260919/kustanayskaya-osm.xml"
DEFAULT_MANIFEST = ROOT / ".runtime/world-source-manifest-20260920/world-source-manifest.json"
DEFAULT_OUTPUT = ROOT / ".runtime/map-first-world-20260920"
DEFAULT_DEM = OVERTURE / "Copernicus_DSM_COG_10_N55_00_E037_00_DEM.tif"
EARTH_RADIUS_M = 6_378_137.0

# These are visibly useful render proxies, never physical survey evidence.
ROAD_CLASS_PROXY_WIDTH_M = {
    "motorway": 18.0,
    "trunk": 16.0,
    "primary": 14.0,
    "secondary": 12.0,
    "tertiary": 11.0,
    "residential": 7.0,
    "living_street": 6.0,
    "service": 4.5,
    "pedestrian": 4.0,
    "footway": 1.8,
    "path": 1.5,
    "steps": 1.5,
    "cycleway": 2.0,
}

BUILDING_HEIGHT_OVERRIDES_BY_OSM_WAY = {
    "93040605": {
        "height_m": 4.2,
        "height_range_m": [3.8, 5.5],
        "floor_count": 1,
        "status": "render_estimate_from_externally_verified_floor_count",
        "sources": [
            {
                "url": "https://www.cian.ru/torgovyy-centr-smoll-shipilovskiy-moskva-215240/",
                "evidence": "Шипиловская улица 62А; 1 floor; 956.5 m2; built 2005",
                "retrieved_on": "2026-09-20",
            },
            {
                "dataset": "KartaView sequence 3838297",
                "evidence": "nine frozen frames confirm a single-storey retail volume",
                "shot_date": "2021-09-04",
                "manifest": ".runtime/kartaview-kustanayskaya-20260920/evidence/manifest.json",
            },
        ],
        "limitation": "4.2 m is a bounded render proxy; neither source publishes measured facade height",
    }
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def round_value(value: Any, digits: int = 6) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [round_value(item, digits) for item in value]
    if isinstance(value, tuple):
        return [round_value(item, digits) for item in value]
    if isinstance(value, dict):
        return {key: round_value(item, digits) for key, item in value.items()}
    return value


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def osm_tags(element: ET.Element) -> dict[str, str]:
    return {item.attrib["k"]: item.attrib["v"] for item in element.findall("tag")}


def source_osm_way_ids(properties: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for source in properties.get("sources") or []:
        record = str(source.get("record_id") or "")
        match = re.match(r"w(\d+)@", record)
        if match:
            result.append(match.group(1))
    return sorted(set(result))


def geometry_parts(geometry: Any, allowed: tuple[type, ...]) -> Iterable[Any]:
    if isinstance(geometry, allowed):
        yield geometry
        return
    if hasattr(geometry, "geoms"):
        for item in geometry.geoms:
            yield from geometry_parts(item, allowed)


def extract_dem_crop(dem: Path, left: int, top: int, width: int, height: int) -> list[list[float]]:
    result = subprocess.run(
        ["magick", f"{dem}[0]", "-crop", f"{width}x{height}+{left}+{top}", "txt:-"],
        check=True,
        capture_output=True,
        text=True,
    )
    values = [[math.nan] * width for _ in range(height)]
    pattern = re.compile(r"^(\d+),(\d+):.*gray\(([-+0-9.eE]+)%\)")
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match:
            x, y, percentage = match.groups()
            # HDR GeoTIFF values are reported as a percentage of quantum range;
            # dividing by 100 restores the source elevation in metres.
            values[int(y)][int(x)] = float(percentage) / 100.0
    if any(not math.isfinite(value) for row in values for value in row):
        raise RuntimeError("ImageMagick did not expose every requested DEM pixel")
    return values


class DemSampler:
    def __init__(self, dem: Path, lonlat_bounds: tuple[float, float, float, float]) -> None:
        self.dem = dem
        min_lon, min_lat, max_lon, max_lat = lonlat_bounds
        pixels = [self.pixel(lon, lat) for lon in (min_lon, max_lon) for lat in (min_lat, max_lat)]
        self.left = math.floor(min(point[0] for point in pixels)) - 2
        self.right = math.ceil(max(point[0] for point in pixels)) + 2
        self.top = math.floor(min(point[1] for point in pixels)) - 2
        self.bottom = math.ceil(max(point[1] for point in pixels)) + 2
        self.values = extract_dem_crop(
            dem,
            self.left,
            self.top,
            self.right - self.left + 1,
            self.bottom - self.top + 1,
        )

    @staticmethod
    def pixel(lon: float, lat: float) -> tuple[float, float]:
        # Copernicus N55/E037 COG: 2400 columns/degree and 3600 rows/degree.
        return (lon - 37.0) * 2400.0, (56.0 - lat) * 3600.0

    def sample(self, lon: float, lat: float) -> float:
        px, py = self.pixel(lon, lat)
        x, y = px - self.left, py - self.top
        x0, y0 = math.floor(x), math.floor(y)
        fx, fy = x - x0, y - y0
        a = self.values[y0][x0]
        b = self.values[y0][x0 + 1]
        c = self.values[y0 + 1][x0]
        d = self.values[y0 + 1][x0 + 1]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def choose_building_height(properties: dict[str, Any]) -> tuple[float | None, str, str, dict[str, Any] | None]:
    explicit = parse_number(properties.get("height"))
    if explicit and explicit > 0:
        return explicit, "overture:height", "cartographic_explicit", None
    floors = parse_number(properties.get("num_floors"))
    if floors and floors > 0:
        return floors * 3.0, "overture:num_floors*3.0m", "render_estimate_from_floor_count", None
    for way_id in source_osm_way_ids(properties):
        override = BUILDING_HEIGHT_OVERRIDES_BY_OSM_WAY.get(way_id)
        if override:
            return float(override["height_m"]), "external_evidence_bounded_proxy", str(override["status"]), override
    return None, "missing", "unknown", None


def choose_road_width(
    properties: dict[str, Any], osm_way_tags: dict[str, dict[str, str]]
) -> tuple[float, str, str, list[dict[str, Any]]]:
    rules = properties.get("width_rules") or []
    valid_rules = [rule for rule in rules if parse_number(rule.get("value")) and parse_number(rule.get("value")) > 0]
    if valid_rules:
        values = [float(rule["value"]) for rule in valid_rules]
        between = [rule.get("between") for rule in valid_rules]
        full = any(item is None or item == [0.0, 1.0] for item in between)
        status = "cartographic_explicit" if full else "cartographic_explicit_partial_segment"
        return max(values), "overture:width_rules", status, valid_rules

    linked_tags = [osm_way_tags[way_id] for way_id in source_osm_way_ids(properties) if way_id in osm_way_tags]
    explicit_widths = [parse_number(row.get("width")) for row in linked_tags]
    explicit_widths = [value for value in explicit_widths if value and value > 0]
    if explicit_widths:
        return max(explicit_widths), "osm:width", "cartographic_explicit", []

    highway_class = str(properties.get("class") or "")
    lane_counts = [parse_number(row.get("lanes")) for row in linked_tags]
    lane_counts = [value for value in lane_counts if value and value > 0]
    if lane_counts and highway_class not in {"footway", "path", "steps", "cycleway"}:
        return max(lane_counts) * 3.1, "osm:lanes*3.1m", "render_estimate_from_lane_count", []

    return (
        ROAD_CLASS_PROXY_WIDTH_M.get(highway_class, 4.0),
        f"class_proxy:{highway_class or 'unknown'}",
        "render_estimate_from_class",
        [],
    )


def make_overview(packet: dict[str, Any], output: Path) -> None:
    width, height, margin = 1600, 1300, 55
    bounds = packet["scope"]["local_bounds_m"]
    min_x, min_y, max_x, max_y = bounds
    scale = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))

    def project(x: float, y: float) -> tuple[float, float]:
        return margin + (x - min_x) * scale, height - margin - (y - min_y) * scale

    def path(coords: list[list[float]], closed: bool = False) -> str:
        points = [project(float(p[0]), float(p[1])) for p in coords]
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points) + (" Z" if closed else "")

    def polygon_paths(geometry: dict[str, Any]) -> list[str]:
        if geometry["type"] == "Polygon":
            return [path(geometry["coordinates"][0], True)]
        if geometry["type"] == "MultiPolygon":
            return [path(polygon[0], True) for polygon in geometry["coordinates"]]
        return []

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#e9ece9"/>',
        '<g stroke-linejoin="round" stroke-linecap="round">',
    ]
    green_colors = {"wood": "#69885c", "grass": "#9fbc76", "tree_row": "#4d7448", "playground": "#d6b77e", "pitch": "#7fa77a", "dog_park": "#9cad78"}
    for feature in packet["green_areas"]:
        color = green_colors.get(feature["properties"]["class"], "#a4b886")
        for item in polygon_paths(feature["geometry_local"]):
            svg.append(f'<path d="{item}" fill="{color}" fill-opacity="0.78" stroke="#5f7755" stroke-width="0.8"/>')
    for road in packet["roads"]:
        status = road["properties"]["width_status"]
        fill = "#747a7d" if status.startswith("cartographic_explicit") else "#929798"
        for item in polygon_paths(road["surface_geometry_local"]):
            svg.append(f'<path d="{item}" fill="{fill}" stroke="#f3f1e9" stroke-width="0.6"/>')
    for building in packet["buildings"]:
        fill = "#d8d4ca" if building["properties"]["height_m"] is not None else "#f6f3eb"
        for item in polygon_paths(building["geometry_local"]):
            svg.append(f'<path d="{item}" fill="{fill}" stroke="#695f55" stroke-width="1.2"/>')
    point_colors = {"street_lamp": "#f3ca4c", "bench": "#7a4f33", "waste_basket": "#4f5b61", "crossing": "#ffffff"}
    for feature in packet["infrastructure"]:
        geometry = feature["geometry_local"]
        if geometry["type"] != "Point":
            continue
        x, y = project(*geometry["coordinates"][:2])
        color = point_colors.get(feature["properties"]["class"], "#37484c")
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.4" fill="{color}" stroke="#263337" stroke-width="0.5"/>')
    project_bbox = packet["scope"]["project_bbox_local_m"]
    x1, y1 = project(project_bbox[0], project_bbox[1])
    x2, y2 = project(project_bbox[2], project_bbox[3])
    svg.append(f'<rect x="{x1:.1f}" y="{y2:.1f}" width="{x2-x1:.1f}" height="{y1-y2:.1f}" fill="none" stroke="#d3392f" stroke-width="4"/>')
    svg.extend([
        '</g>',
        '<g font-family="Arial, sans-serif">',
        '<rect x="28" y="22" width="770" height="118" rx="9" fill="#ffffff" fill-opacity="0.94"/>',
        '<text x="50" y="55" font-size="24" font-weight="700" fill="#172326">Кустанайская: map-first world, без DXF-геометрии</text>',
        '<text x="50" y="84" font-size="16" fill="#3e4e51">Здания, дороги, зелёные зоны и объекты — Overture/OSM в локальной ENU-системе.</text>',
        '<text x="50" y="109" font-size="15" fill="#5b686a">Тёмная дорога: width задан; светлая: width — помеченный proxy. Красное — проектный bbox.</text>',
        '<text x="50" y="132" font-size="14" fill="#7b3732">DSM — только широкий рельеф; неизвестные высоты зданий не экструдируются.</text>',
        '</g></svg>',
    ])
    output.write_text("\n".join(svg))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--osm", type=Path, default=DEFAULT_OSM)
    parser.add_argument("--dem", type=Path, default=DEFAULT_DEM)
    parser.add_argument("--overture-dir", type=Path, default=OVERTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--road-evidence", type=Path)
    parser.add_argument("--road-evidence-review", type=Path)
    parser.add_argument("--margin-m", type=float, default=120.0)
    parser.add_argument("--terrain-step-m", type=float, default=10.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    road_evidence_by_id: dict[str, dict[str, Any]] = {}
    if bool(args.road_evidence) != bool(args.road_evidence_review):
        raise ValueError("--road-evidence and --road-evidence-review must be supplied together")
    if args.road_evidence and args.road_evidence_review:
        evidence_packet = json.loads(args.road_evidence.read_text())
        evidence_review = json.loads(args.road_evidence_review.read_text())
        expected_hash = evidence_review["source_packet"]["sha256"]
        if sha256(args.road_evidence) != expected_hash:
            raise ValueError("Road evidence hash does not match the reviewed packet")
        accepted_ids = set(evidence_review.get("accepted_road_ids") or [])
        recommendations = {row["road_id"]: row for row in evidence_packet["recommendations"]}
        missing = accepted_ids - set(recommendations)
        if missing:
            raise ValueError(f"Reviewed road ids are missing from the evidence packet: {sorted(missing)}")
        for road_id in accepted_ids:
            recommendation = recommendations[road_id]
            if recommendation["admission"] != "eligible_for_map_base_width_after_visual_review":
                raise ValueError(f"Reviewed road did not pass the evidence gate: {road_id}")
            road_evidence_by_id[road_id] = recommendation

    manifest = json.loads(args.manifest.read_text())
    project_bbox_wgs84 = tuple(float(value) for value in manifest["site"]["scope_bbox_wgs84_candidate"])
    min_lon, min_lat, max_lon, max_lat = project_bbox_wgs84
    lon0 = (min_lon + max_lon) / 2.0
    lat0 = (min_lat + max_lat) / 2.0

    def lonlat_to_local(x: Any, y: Any, z: Any = None) -> Any:
        local_x = EARTH_RADIUS_M * math.radians(float(x) - lon0) * math.cos(math.radians(lat0))
        local_y = EARTH_RADIUS_M * math.radians(float(y) - lat0)
        return (local_x, local_y) if z is None else (local_x, local_y, z)

    def local_to_lonlat(x: float, y: float) -> tuple[float, float]:
        lon = lon0 + math.degrees(x / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
        lat = lat0 + math.degrees(y / EARTH_RADIUS_M)
        return lon, lat

    project_local = transform_geometry(lonlat_to_local, box(*project_bbox_wgs84))
    scope_local = box(*project_local.bounds).buffer(args.margin_m, join_style=2)
    scope_lonlat_points = [local_to_lonlat(x, y) for x, y in scope_local.exterior.coords]
    scope_wgs84 = (
        min(point[0] for point in scope_lonlat_points), min(point[1] for point in scope_lonlat_points),
        max(point[0] for point in scope_lonlat_points), max(point[1] for point in scope_lonlat_points),
    )

    osm_root = ET.parse(args.osm).getroot()
    osm_way_tags = {way.attrib["id"]: osm_tags(way) for way in osm_root.findall("way")}
    osm_nodes = {
        node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
        for node in osm_root.findall("node")
    }

    def load_geojson(name: str) -> dict[str, Any]:
        return json.loads((args.overture_dir / f"{name}.geojson").read_text())

    def clipped_local(feature: dict[str, Any]) -> Any | None:
        geometry = shape(feature["geometry"])
        local = transform_geometry(lonlat_to_local, geometry)
        if not local.intersects(scope_local):
            return None
        clipped = local.intersection(scope_local)
        return clipped if not clipped.is_empty else None

    buildings: list[dict[str, Any]] = []
    for feature in load_geojson("building")["features"]:
        clipped = clipped_local(feature)
        if clipped is None:
            continue
        properties = feature.get("properties") or {}
        height, height_source, height_status, height_evidence = choose_building_height(properties)
        for index, polygon in enumerate(geometry_parts(clipped, (Polygon,))):
            if polygon.area < 4.0:
                continue
            centroid_lonlat = local_to_lonlat(polygon.centroid.x, polygon.centroid.y)
            buildings.append({
                "id": f"overture:building:{feature.get('id')}:{index}",
                "geometry_local": round_value(mapping(polygon)),
                "properties": {
                    "height_m": height,
                    "height_source": height_source,
                    "height_status": height_status,
                    "height_evidence": height_evidence,
                    "num_floors": properties.get("num_floors"),
                    "class": properties.get("class"),
                    "subtype": properties.get("subtype"),
                    "ground_sample_lonlat": round_value(list(centroid_lonlat), 9),
                    "sources": properties.get("sources") or [],
                },
            })

    roads: list[dict[str, Any]] = []
    for feature in load_geojson("segment")["features"]:
        properties = feature.get("properties") or {}
        if properties.get("subtype") != "road":
            continue
        clipped = clipped_local(feature)
        if clipped is None:
            continue
        width, width_source, width_status, width_rules = choose_road_width(properties, osm_way_tags)
        for index, line in enumerate(geometry_parts(clipped, (LineString,))):
            if line.length < 0.2:
                continue
            road_id = f"overture:segment:{feature.get('id')}:{index}"
            evidence_override = road_evidence_by_id.get(road_id)
            width_evidence = None
            if evidence_override:
                width = float(evidence_override["recommended_width_m"])
                width_source = "microsoft_road_detections:WidthMeters"
                width_status = "independent_satellite_width_corroborated"
                width_evidence = {
                    "evidence_ids": evidence_override["evidence_ids"],
                    "road_coverage_ratio": evidence_override["maximum_road_coverage_ratio"],
                    "previous_width_m": evidence_override["current_width_m"],
                    "previous_width_status": evidence_override["current_width_status"],
                    "review": str(args.road_evidence_review.resolve()),
                    "limitation": "approximate satellite-derived width; centreline-correlated, not curb survey",
                }
            surface = line.buffer(width / 2.0, cap_style=2, join_style=2).intersection(scope_local)
            roads.append({
                "id": road_id,
                "centerline_geometry_local": round_value(mapping(line)),
                "surface_geometry_local": round_value(mapping(surface)),
                "properties": {
                    "class": properties.get("class"),
                    "subtype": properties.get("subtype"),
                    "width_m": width,
                    "width_source": width_source,
                    "width_status": width_status,
                    "width_evidence": width_evidence,
                    "width_rules": width_rules,
                    "road_surface": properties.get("road_surface") or [],
                    "osm_way_ids": source_osm_way_ids(properties),
                    "sources": properties.get("sources") or [],
                },
            })

    green_areas: list[dict[str, Any]] = []
    green_predicates = [
        ("natural", {"wood", "scrub", "grassland", "tree_row"}),
        ("landuse", {"grass", "forest", "meadow", "recreation_ground"}),
        ("leisure", {"park", "garden", "playground", "pitch", "dog_park"}),
    ]
    for way in osm_root.findall("way"):
        row = osm_way_tags[way.attrib["id"]]
        selected_key = selected_value = None
        for key, allowed in green_predicates:
            if row.get(key) in allowed:
                selected_key, selected_value = key, row[key]
                break
        if not selected_value:
            continue
        coordinates = [osm_nodes[item.attrib["ref"]] for item in way.findall("nd") if item.attrib["ref"] in osm_nodes]
        if len(coordinates) < 2:
            continue
        is_closed = len(coordinates) >= 4 and coordinates[0] == coordinates[-1]
        source_geometry = Polygon(coordinates) if is_closed else LineString(coordinates)
        local = transform_geometry(lonlat_to_local, source_geometry).intersection(scope_local)
        if local.is_empty:
            continue
        for index, part in enumerate(geometry_parts(local, (Polygon, LineString))):
            if isinstance(part, Polygon) and part.area < 1.0:
                continue
            green_areas.append({
                "id": f"osm:way:{way.attrib['id']}:{index}",
                "geometry_local": round_value(mapping(part)),
                "properties": {
                    "class": selected_value,
                    "source_tag": f"{selected_key}={selected_value}",
                    "name": row.get("name"),
                    "geometry_status": "osm_cartographic",
                },
            })

    infrastructure: list[dict[str, Any]] = []
    for feature in load_geojson("infrastructure")["features"]:
        clipped = clipped_local(feature)
        if clipped is None:
            continue
        properties = feature.get("properties") or {}
        for index, part in enumerate(geometry_parts(clipped, (Point, LineString, Polygon))):
            infrastructure.append({
                "id": f"overture:infrastructure:{feature.get('id')}:{index}",
                "geometry_local": round_value(mapping(part)),
                "properties": {
                    "class": properties.get("class"),
                    "subtype": properties.get("subtype"),
                    "placement_status": "overture_osm_cartographic",
                    "dimensions_status": "unknown_no_model_assertion",
                    "source_tags": properties.get("source_tags") or [],
                    "sources": properties.get("sources") or [],
                },
            })

    reference_axes = []
    for way in osm_root.findall("way"):
        row = osm_way_tags[way.attrib["id"]]
        if row.get("highway") is None or row.get("name") is None:
            continue
        coordinates = [osm_nodes[item.attrib["ref"]] for item in way.findall("nd") if item.attrib["ref"] in osm_nodes]
        if len(coordinates) < 2:
            continue
        source_line = LineString(coordinates)
        local_line = transform_geometry(lonlat_to_local, source_line).intersection(scope_local)
        for index, line in enumerate(geometry_parts(local_line, (LineString,))):
            if line.length < 1.0:
                continue
            project_piece = line.intersection(project_local)
            project_lines = list(geometry_parts(project_piece, (LineString,)))
            project_axis = max(project_lines, key=lambda item: item.length) if project_lines else None
            reference_axes.append({
                "id": f"osm:way:{way.attrib['id']}:{index}",
                "name": row.get("name"),
                "highway": row.get("highway"),
                "lanes": parse_number(row.get("lanes")),
                "geometry_local": round_value(mapping(line)),
                "project_geometry_local": round_value(mapping(project_axis)) if project_axis is not None else None,
                "length_in_project_m": round(project_piece.length, 6),
            })

    dem = DemSampler(args.dem, scope_wgs84)
    origin_dsm = dem.sample(lon0, lat0)
    min_x, min_y, max_x, max_y = scope_local.bounds
    columns = math.ceil((max_x - min_x) / args.terrain_step_m) + 1
    rows = math.ceil((max_y - min_y) / args.terrain_step_m) + 1
    xs = [min_x + (max_x - min_x) * index / (columns - 1) for index in range(columns)]
    ys = [min_y + (max_y - min_y) * index / (rows - 1) for index in range(rows)]
    terrain_vertices: list[dict[str, Any]] = []
    for y in ys:
        for x in xs:
            lon, lat = local_to_lonlat(x, y)
            absolute_z = dem.sample(lon, lat)
            terrain_vertices.append({
                "xyz_local_m": [round(x, 6), round(y, 6), round(absolute_z - origin_dsm, 6)],
                "dsm_egm2008_m": round(absolute_z, 6),
                "status": "copernicus_glo30_context_only",
            })
    terrain_triangles: list[list[int]] = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            b, c, d = a + 1, a + columns, a + columns + 1
            terrain_triangles.extend([[a, b, d], [a, d, c]])

    # Ground every eligible object against the same broad DSM and preserve the
    # absolute sample used.  This is context contact, never curb-level truth.
    for collection in (buildings, infrastructure):
        for feature in collection:
            geometry = shape(feature["geometry_local"])
            point = geometry if isinstance(geometry, Point) else geometry.representative_point()
            lon, lat = local_to_lonlat(point.x, point.y)
            absolute_z = dem.sample(lon, lat)
            feature["properties"]["ground_z_local_m"] = round(absolute_z - origin_dsm, 6)
            feature["properties"]["ground_z_egm2008_m"] = round(absolute_z, 6)
            feature["properties"]["ground_status"] = "copernicus_glo30_context_only"

    def terrain_mesh_z(x: float, y: float) -> float:
        """Interpolate the exact two-triangle terrain mesh, not a bilinear patch."""
        fx = (x - min_x) / (max_x - min_x) * (columns - 1)
        fy = (y - min_y) / (max_y - min_y) * (rows - 1)
        column = min(max(math.floor(fx), 0), columns - 2)
        row = min(max(math.floor(fy), 0), rows - 2)
        tx = min(max(fx - column, 0.0), 1.0)
        ty = min(max(fy - row, 0.0), 1.0)
        a = terrain_vertices[row * columns + column]["xyz_local_m"][2]
        b = terrain_vertices[row * columns + column + 1]["xyz_local_m"][2]
        c = terrain_vertices[(row + 1) * columns + column]["xyz_local_m"][2]
        d = terrain_vertices[(row + 1) * columns + column + 1]["xyz_local_m"][2]
        if ty <= tx:
            return a * (1.0 - tx) + b * (tx - ty) + d * ty
        return a * (1.0 - ty) + d * tx + c * (ty - tx)

    for building in buildings:
        polygon = shape(building["geometry_local"])
        # GLO-30 is a surface model: a centroid sample can be the roof itself.
        # The median of an 8 m exterior ring is a less biased context-grade
        # ground proxy.  It remains explicitly unsuitable for survey use.
        ring = polygon.buffer(8.0).exterior
        sample_count = max(24, math.ceil(ring.length / 4.0))
        samples = []
        for index in range(sample_count):
            point = ring.interpolate(ring.length * index / sample_count)
            samples.append(terrain_mesh_z(point.x, point.y))
        samples.sort()
        ground_z = samples[len(samples) // 2]
        building["properties"]["ground_z_local_m"] = round(ground_z, 6)
        building["properties"]["ground_z_egm2008_m"] = round(origin_dsm + ground_z, 6)
        building["properties"]["ground_status"] = "copernicus_glo30_exterior_ring_median_context_only"
        building["properties"]["ground_sampling"] = {
            "ring_offset_m": 8.0,
            "samples": sample_count,
            "statistic": "median",
            "range_local_m": [round(samples[0], 6), round(samples[-1], 6)],
        }

    def render_triangles(source_geometry: Any, z_offset: float) -> list[list[list[float]]]:
        """Constrain surface triangles to terrain cells, then drape every vertex."""
        triangles_xyz: list[list[list[float]]] = []
        if source_geometry.is_empty:
            return triangles_xyz
        source_min_x, source_min_y, source_max_x, source_max_y = source_geometry.bounds
        column_start = max(0, math.floor((source_min_x - min_x) / (max_x - min_x) * (columns - 1)))
        column_end = min(columns - 2, math.floor((source_max_x - min_x) / (max_x - min_x) * (columns - 1)))
        row_start = max(0, math.floor((source_min_y - min_y) / (max_y - min_y) * (rows - 1)))
        row_end = min(rows - 2, math.floor((source_max_y - min_y) / (max_y - min_y) * (rows - 1)))
        for row_index in range(row_start, row_end + 1):
            for column_index in range(column_start, column_end + 1):
                cell = box(xs[column_index], ys[row_index], xs[column_index + 1], ys[row_index + 1])
                clipped = source_geometry.intersection(cell)
                for polygon in geometry_parts(clipped, (Polygon,)):
                    if polygon.area < 1e-4:
                        continue
                    tessellation = constrained_delaunay_triangles(polygon)
                    for triangle in geometry_parts(tessellation, (Polygon,)):
                        points = list(triangle.exterior.coords)[:3]
                        if len(points) != 3:
                            continue
                        xyz = []
                        for x, y in points:
                            xyz.append([round(x, 6), round(y, 6), round(terrain_mesh_z(x, y) + z_offset, 6)])
                        triangles_xyz.append(xyz)
        return triangles_xyz

    road_groups: dict[str, list[Any]] = {"explicit": [], "corroborated": [], "estimated": []}
    for feature in roads:
        width_status = feature["properties"]["width_status"]
        if width_status.startswith("cartographic_explicit"):
            group = "explicit"
        elif width_status == "independent_satellite_width_corroborated":
            group = "corroborated"
        else:
            group = "estimated"
        road_groups[group].append(shape(feature["surface_geometry_local"]))
    green_groups: dict[str, list[Any]] = {"woodland": [], "grass": [], "recreation": []}
    for feature in green_areas:
        geometry = shape(feature["geometry_local"])
        if not isinstance(geometry, (Polygon, MultiPolygon)):
            continue
        cls = feature["properties"]["class"]
        group = "woodland" if cls in {"wood", "forest", "tree_row"} else "recreation" if cls in {"playground", "pitch", "dog_park"} else "grass"
        green_groups[group].append(geometry)
    render_layers = []
    for group, geometries in green_groups.items():
        if geometries:
            merged = unary_union(geometries)
            triangles = render_triangles(merged, 0.10)
            triangle_area = sum(Polygon([(p[0], p[1]) for p in triangle]).area for triangle in triangles)
            render_layers.append({
                "id": f"green_{group}",
                "semantic": group,
                "source": "union of exact clipped OSM green polygons",
                "source_area_m2": round(merged.area, 6),
                "triangulated_area_m2": round(triangle_area, 6),
                "triangles_xyz_local_m": triangles,
            })
    for group, geometries in road_groups.items():
        if geometries:
            merged = unary_union(geometries)
            triangles = render_triangles(merged, 0.14)
            triangle_area = sum(Polygon([(p[0], p[1]) for p in triangle]).area for triangle in triangles)
            render_layers.append({
                "id": f"road_{group}",
                "semantic": f"road_{group}",
                "source": "union of Overture centerline buffers; width provenance remains on road features",
                "source_area_m2": round(merged.area, 6),
                "triangulated_area_m2": round(triangle_area, 6),
                "triangles_xyz_local_m": triangles,
            })

    packet = {
        "schema": "green-atlas.map-first-world.v1",
        "status": "diagnostic_map_base_not_survey_grade",
        "principle": "External GIS is the continuous world base. DXF geometry is absent and may only enter later as a reviewed project overlay.",
        "coordinate_frame": {
            "horizontal": "local equirectangular ENU approximation in metres from WGS84 origin",
            "origin_wgs84_lon_lat": [round(lon0, 10), round(lat0, 10)],
            "earth_radius_m": EARTH_RADIUS_M,
            "vertical": "metres relative to Copernicus GLO-30 DSM sample at origin; source datum EGM2008",
            "origin_dsm_egm2008_m": round(origin_dsm, 6),
        },
        "scope": {
            "project_bbox_wgs84_candidate": list(project_bbox_wgs84),
            "project_bbox_local_m": round_value(list(project_local.bounds)),
            "context_bbox_wgs84": round_value(list(scope_wgs84), 10),
            "local_bounds_m": round_value(list(scope_local.bounds)),
            "context_margin_m": args.margin_m,
        },
        "inputs": {
            "manifest": {"path": str(args.manifest.resolve()), "sha256": sha256(args.manifest)},
            "osm": {"path": str(args.osm.resolve()), "sha256": sha256(args.osm)},
            "dem": {"path": str(args.dem.resolve()), "sha256": sha256(args.dem)},
            **{
                f"overture_{name}": {
                    "path": str((args.overture_dir / f"{name}.geojson").resolve()),
                    "sha256": sha256(args.overture_dir / f"{name}.geojson"),
                }
                for name in ("building", "segment", "infrastructure")
            },
            **(
                {
                    "road_evidence": {"path": str(args.road_evidence.resolve()), "sha256": sha256(args.road_evidence)},
                    "road_evidence_review": {
                        "path": str(args.road_evidence_review.resolve()),
                        "sha256": sha256(args.road_evidence_review),
                    },
                }
                if args.road_evidence and args.road_evidence_review
                else {}
            ),
        },
        "terrain": {
            "rows": rows,
            "columns": columns,
            "nominal_step_m": args.terrain_step_m,
            "vertices": terrain_vertices,
            "triangles": terrain_triangles,
            "status": "continuous_broad_context_only",
        },
        "render_layers": render_layers,
        "named_reference_axes": sorted(reference_axes, key=lambda row: row["id"]),
        "buildings": sorted(buildings, key=lambda row: row["id"]),
        "roads": sorted(roads, key=lambda row: row["id"]),
        "green_areas": sorted(green_areas, key=lambda row: row["id"]),
        "infrastructure": sorted(infrastructure, key=lambda row: row["id"]),
        "admission": {
            "diagnostic_render": "allowed",
            "beauty_render": "blocked",
            "blockers": [
                "GLO-30 is a DSM at roughly 30 m resolution and does not resolve curbs or object contact precisely.",
                "Most Overture road segments still have no physical width; only reviewed Microsoft matches replace selected proxies.",
                "Buildings without external height evidence remain plan-only.",
                "No individual existing trees are mapped in the captured OSM snapshot.",
                "The candidate project bbox has no independent survey control against WGS84 yet.",
            ],
        },
    }
    world_path = args.output / "world.json"
    world_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    overview = args.output / "overview.svg"
    make_overview(packet, overview)
    subprocess.run(["magick", str(overview), str(args.output / "overview.png")], check=True)

    width_statuses = Counter(row["properties"]["width_status"] for row in roads)
    height_statuses = Counter(row["properties"]["height_status"] for row in buildings)
    infrastructure_classes = Counter(row["properties"]["class"] for row in infrastructure)
    receipt = {
        "schema": "green-atlas.map-first-world-receipt.v1",
        "world": {"path": str(world_path.resolve()), "sha256": sha256(world_path)},
        "overview": {"path": str(overview.resolve()), "sha256": sha256(overview)},
        "counts": {
            "buildings": len(buildings),
            "roads": len(roads),
            "green_areas": len(green_areas),
            "infrastructure": len(infrastructure),
            "terrain_vertices": len(terrain_vertices),
            "terrain_triangles": len(terrain_triangles),
            "draped_surface_triangles": sum(len(row["triangles_xyz_local_m"]) for row in render_layers),
        },
        "building_height_status": dict(sorted(height_statuses.items())),
        "road_width_status": dict(sorted(width_statuses.items())),
        "infrastructure_classes": dict(sorted(infrastructure_classes.items(), key=lambda item: str(item[0]))),
        "dxfless_base": True,
        "status": packet["status"],
    }
    receipt_path = args.output / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
