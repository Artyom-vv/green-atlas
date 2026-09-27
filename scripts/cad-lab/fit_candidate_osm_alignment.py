"""Fit and visualize a candidate OSM -> DXF similarity transform.

Addressed building footprints provide coarse tie objects.  They are useful for
discovering the likely placement, but they are not survey control points.  The
output therefore remains `candidate_only` even when residuals are small.
"""
from __future__ import annotations

import hashlib
import json
import math
import argparse
import sys
import xml.etree.ElementTree as ET
from itertools import combinations
from pathlib import Path

import ezdxf
import numpy as np
from ezdxf.path import make_path
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.dxf_import.adapters import _hatch_polygon_geometry  # noqa: E402
from app.dxf_import.polygons import hatch_geometry  # noqa: E402
from automatic_address_controls import normalize_address, normalize_street, pair_unique_addresses  # noqa: E402


DXF_ROOT = ROOT / ".runtime/kustanayskaya-mac-ready-20260917/dxf"
OUT = ROOT / ".runtime/deterministic-render-audit-20260919"
OSM = OUT / "kustanayskaya-osm.xml"
EARTH_RADIUS_M = 6378137.0
REFERENCE_LON_LAT = (37.7535, 55.6185)
INLIER_THRESHOLD_M = 4.0

# Retained only to compare the automatic discovery with the historical audit.
# Production/default execution does not use these handles or way identifiers.
LEGACY_CONTROL_PAIRS = [
    {"address": "4 к2", "dxf_label_handle": "144EF", "dxf_building_handle": "72A3", "osm_way_id": "40678924"},
    {"address": "4 к1", "dxf_label_handle": "1471E", "dxf_building_handle": "78A6", "osm_way_id": "40678925"},
    {"address": "2 к1", "dxf_label_handle": "147E9", "dxf_building_handle": "78B3", "osm_way_id": "40678960"},
    {"address": "3 к1", "dxf_label_handle": "150F6", "dxf_building_handle": "E201", "osm_way_id": "40678955"},
    {"address": "10 к1", "dxf_label_handle": "15180", "dxf_building_handle": "F292", "osm_way_id": "40678921"},
    {"address": "10 к3", "dxf_label_handle": "152B9", "dxf_building_handle": "1257E", "osm_way_id": "45980296"},
    {"address": "14 к1", "dxf_label_handle": "15770", "dxf_building_handle": "132F7", "osm_way_id": "40678918"},
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source(name: str) -> Path:
    matches = list(DXF_ROOT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {name}, got {len(matches)}")
    return matches[0]


def dxf_buildings(document) -> dict[str, Polygon]:
    result = {}
    for entity in document.modelspace().query("LWPOLYLINE"):
        if entity.dxf.layer != "Здания":
            continue
        points = [(point.x, point.y) for point in make_path(entity).flattening(0.02)]
        if len(points) >= 3 and points[0] != points[-1]:
            points.append(points[0])
        if len(points) < 4:
            continue
        polygon = Polygon(points)
        if polygon.is_valid and polygon.area > 0:
            result[entity.dxf.handle] = polygon
    return result


def text_value(entity) -> str:
    value = entity.plain_text() if hasattr(entity, "plain_text") else entity.dxf.text
    return str(value).replace("\\P", " ").strip()


def text_position(entity) -> tuple[float, float]:
    point = entity.dxf.insert
    return float(point.x), float(point.y)


def discover_dxf_address_candidates(document, buildings: dict[str, Polygon],
                                    accepted_addresses: set[str]) -> list[dict[str, object]]:
    candidates = []
    for entity in document.modelspace().query("TEXT MTEXT"):
        raw = text_value(entity)
        address = normalize_address(raw)
        if address is None or address not in accepted_addresses:
            continue
        point_xy = text_position(entity)
        containing = [
            (handle, polygon) for handle, polygon in buildings.items()
            if polygon.buffer(0.05).covers(Point(point_xy))
        ]
        placement = "inside"
        if not containing:
            nearest = sorted(
                ((polygon.distance(Point(point_xy)), handle, polygon) for handle, polygon in buildings.items()),
                key=lambda row: row[0],
            )
            if not nearest or nearest[0][0] > 6.0:
                continue
            _, handle, polygon = nearest[0]
            containing = [(handle, polygon)]
            placement = "nearest_within_6m"
        if len(containing) != 1:
            continue
        handle, polygon = containing[0]
        candidates.append({
            "normalized_address": address,
            "raw_address": raw,
            "label_handle": entity.dxf.handle,
            "building_handle": handle,
            "centroid": list(polygon.centroid.coords[0]),
            "area_m2": polygon.area,
            "label_placement": placement,
        })
    # Repeated text entities pointing at the same footprint are one candidate.
    unique = {}
    for row in candidates:
        unique[(row["normalized_address"], row["building_handle"])] = row
    return sorted(unique.values(), key=lambda row: (row["normalized_address"], row["building_handle"]))


def discover_osm_address_candidates(ways, street_name: str) -> list[dict[str, object]]:
    expected_street = normalize_street(street_name)
    candidates = []
    for way_id, way in ways.items():
        tags = way["tags"]
        if "building" not in tags or normalize_street(tags.get("addr:street")) != expected_street:
            continue
        address = normalize_address(tags.get("addr:housenumber"))
        if address is None or len(way["coordinates"]) < 4:
            continue
        polygon = Polygon(way["coordinates"])
        if not polygon.is_valid or polygon.area <= 0:
            continue
        candidates.append({
            "normalized_address": address,
            "way_id": way_id,
            "centroid": list(polygon.centroid.coords[0]),
            "area_m2": polygon.area,
            "tags": {
                key: tags.get(key) for key in
                ("addr:street", "addr:housenumber", "building", "building:levels", "height")
                if tags.get(key) is not None
            },
        })
    return sorted(candidates, key=lambda row: (row["normalized_address"], row["way_id"]))


def local_tangent(lon: float, lat: float) -> np.ndarray:
    lon0, lat0 = REFERENCE_LON_LAT
    return np.array([
        EARTH_RADIUS_M * math.radians(lon - lon0) * math.cos(math.radians(lat0)),
        EARTH_RADIUS_M * math.radians(lat - lat0),
    ])


def read_osm(path: Path):
    root = ET.parse(path).getroot()
    nodes = {
        node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
        for node in root.findall("node")
    }
    ways = {}
    for way in root.findall("way"):
        refs = [node.attrib["ref"] for node in way.findall("nd")]
        coordinates = [tuple(local_tangent(*nodes[ref])) for ref in refs if ref in nodes]
        ways[way.attrib["id"]] = {
            "coordinates": coordinates,
            "tags": {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")},
        }
    return nodes, ways


def fit_similarity(source_points: np.ndarray, target_points: np.ndarray):
    source_mean = source_points.mean(axis=0)
    target_mean = target_points.mean(axis=0)
    source_centered = source_points - source_mean
    target_centered = target_points - target_mean
    left, singular, right_t = np.linalg.svd(source_centered.T @ target_centered)
    rotation = left @ right_t
    if np.linalg.det(rotation) < 0:
        left[:, -1] *= -1
        rotation = left @ right_t
    scale = singular.sum() / np.square(source_centered).sum()
    translation = target_mean - scale * source_mean @ rotation
    return scale, rotation, translation


def apply(points, scale, rotation, translation):
    return scale * np.asarray(points) @ rotation + translation


def robust_fit(source_points: np.ndarray, target_points: np.ndarray):
    best = None
    for left, right in combinations(range(len(source_points)), 2):
        try:
            model = fit_similarity(source_points[[left, right]], target_points[[left, right]])
        except (ValueError, np.linalg.LinAlgError, FloatingPointError):
            continue
        residuals = np.linalg.norm(apply(source_points, *model) - target_points, axis=1)
        inliers = residuals <= INLIER_THRESHOLD_M
        score = (int(inliers.sum()), -float(np.square(residuals[inliers]).sum()))
        if best is None or score > best[0]:
            best = (score, inliers)
    if best is None or int(best[1].sum()) < 3:
        raise RuntimeError("Could not find a stable candidate transform")
    inliers = best[1]
    model = fit_similarity(source_points[inliers], target_points[inliers])
    residuals = np.linalg.norm(apply(source_points, *model) - target_points, axis=1)
    return model, residuals, inliers


def svg_path(points, project, *, close=False):
    projected = [project(*point) for point in points]
    data = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in projected)
    return data + (" Z" if close else "")


def render_overlay(boundary: Polygon, buildings, ways, model, controls, output: Path):
    min_x, min_y, max_x, max_y = boundary.bounds
    margin_m = 35
    min_x -= margin_m; min_y -= margin_m; max_x += margin_m; max_y += margin_m
    width, height, margin = 1000, 1900, 70
    scale_px = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))

    def project(x, y):
        return margin + (x - min_x) * scale_px, height - margin - (y - min_y) * scale_px

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f6f1"/>',
        '<g font-family="Arial, sans-serif">',
        '<text x="55" y="34" font-size="22" font-weight="bold" fill="#152624">OSM → DXF: кандидатное совмещение по адресным зданиям</text>',
        '<text x="55" y="58" font-size="14" fill="#50605c">Синий — OSM, тёмный — DXF. Это не утверждённая геодезическая трансформация.</text>',
        f'<path d="{svg_path(boundary.exterior.coords, project, close=True)}" fill="none" stroke="#93a19d" stroke-width="2" stroke-dasharray="8 6"/>',
    ]
    for way in ways.values():
        coordinates = way["coordinates"]
        if len(coordinates) < 2:
            continue
        transformed = apply(np.asarray(coordinates), *model)
        if "building" in way["tags"] and len(transformed) >= 4 and np.allclose(transformed[0], transformed[-1]):
            svg.append(f'<path d="{svg_path(transformed, project, close=True)}" fill="none" stroke="#2478b7" stroke-width="1.5" opacity="0.78"/>')
        elif way["tags"].get("name") == "Кустанайская улица":
            svg.append(f'<path d="{svg_path(transformed, project)}" fill="none" stroke="#1683a3" stroke-width="4" opacity="0.85"/>')
    for polygon in buildings.values():
        if boundary.buffer(80).intersects(polygon):
            svg.append(f'<path d="{svg_path(polygon.exterior.coords, project, close=True)}" fill="none" stroke="#252f2c" stroke-width="1.5"/>')
    for control in controls:
        x, y = project(*control["dxf_centroid"])
        color = "#2f8f4e" if control["inlier"] else "#c24c3b"
        svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="{color}" stroke="#fff" stroke-width="1.5"/>')
        svg.append(f'<text x="{x+7:.2f}" y="{y-7:.2f}" font-size="12" fill="{color}">{control["address"]} · {control["residual_m"]:.1f} м</text>')
    svg.extend(["</g>", "</svg>"])
    output.write_text("\n".join(svg))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--street-name", default="Кустанайская улица")
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument(
        "--legacy-manual-controls", action="store_true",
        help="Use the old seven hard-coded pairs only for regression comparison.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    topography_path = source("00.1_10004141_Топография.dxf")
    boundary_path = source("01_10004141_Границы работ.dxf")
    project_path = source("03_10004141_Проектные решения.dxf")
    topography = ezdxf.readfile(topography_path)
    boundary_document = ezdxf.readfile(boundary_path)
    project_document = ezdxf.readfile(project_path)
    buildings = dxf_buildings(topography)
    _, ways = read_osm(OSM)

    boundary_entity = boundary_document.entitydb.get("2A8E2F")
    if boundary_entity is None:
        candidates = []
        for entity in boundary_document.modelspace().query("LWPOLYLINE"):
            if entity.dxf.layer != "ДВ_ГП_П_Граница работ" or not entity.closed:
                continue
            points = [(point.x, point.y) for point in make_path(entity).flattening(0.02)]
            if points and points[0] != points[-1]:
                points.append(points[0])
            polygon = Polygon(points)
            if polygon.is_valid and polygon.area > 0:
                candidates.append((polygon.area, entity, polygon))
        if not candidates:
            raise RuntimeError("No valid authored work boundary found")
        _, boundary_entity, boundary = max(candidates, key=lambda row: row[0])
    else:
        boundary_points = [(point.x, point.y) for point in make_path(boundary_entity).flattening(0.02)]
        if boundary_points[0] != boundary_points[-1]:
            boundary_points.append(boundary_points[0])
        boundary = Polygon(boundary_points)

    osm_points = []
    dxf_points = []
    controls = []
    if args.legacy_manual_controls:
        control_discovery = {
            "method": "legacy_manual_handles_and_way_ids",
            "pairs": len(LEGACY_CONTROL_PAIRS),
        }
        for pair in LEGACY_CONTROL_PAIRS:
            polygon = buildings[pair["dxf_building_handle"]]
            osm_way = ways[pair["osm_way_id"]]
            osm_polygon = Polygon(osm_way["coordinates"])
            if not osm_polygon.is_valid:
                raise RuntimeError(f"Invalid OSM building {pair['osm_way_id']}")
            osm_points.append(osm_polygon.centroid.coords[0])
            dxf_points.append(polygon.centroid.coords[0])
            controls.append({
                **pair,
                "source": "legacy_manual_control",
                "dxf_centroid": list(polygon.centroid.coords[0]),
                "dxf_area_m2": polygon.area,
                "osm_tangent_centroid": list(osm_polygon.centroid.coords[0]),
                "osm_area_m2": osm_polygon.area,
            })
    else:
        osm_candidates = discover_osm_address_candidates(ways, args.street_name)
        accepted_addresses = {str(row["normalized_address"]) for row in osm_candidates}
        dxf_candidates = discover_dxf_address_candidates(topography, buildings, accepted_addresses)
        pairs, discovery_audit = pair_unique_addresses(dxf_candidates, osm_candidates)
        control_discovery = {
            "method": "automatic_normalized_address_inside_dxf_building_to_osm_addr_housenumber",
            "street_name": args.street_name,
            **discovery_audit,
        }
        if len(pairs) < 3:
            raise RuntimeError(
                f"Automatic address discovery produced only {len(pairs)} unique pairs; "
                "at least three are required"
            )
        for pair in pairs:
            left, right = pair["dxf"], pair["map"]
            osm_points.append(right["centroid"])
            dxf_points.append(left["centroid"])
            controls.append({
                "address": pair["address"],
                "source": "automatic_unique_normalized_address",
                "dxf_label_handle": left["label_handle"],
                "dxf_building_handle": left["building_handle"],
                "osm_way_id": right["way_id"],
                "dxf_label_placement": left["label_placement"],
                "dxf_centroid": left["centroid"],
                "dxf_area_m2": left["area_m2"],
                "osm_tangent_centroid": right["centroid"],
                "osm_area_m2": right["area_m2"],
                "osm_tags": right["tags"],
            })
    osm_points = np.asarray(osm_points)
    dxf_points = np.asarray(dxf_points)
    model, residuals, inliers = robust_fit(osm_points, dxf_points)
    scale, rotation, translation = model
    for index, control in enumerate(controls):
        control["candidate_dxf_centroid"] = list(apply(osm_points[index:index+1], *model)[0])
        control["residual_m"] = float(residuals[index])
        control["inlier"] = bool(inliers[index])

    angle_degrees = math.degrees(math.atan2(rotation[0, 1], rotation[0, 0]))
    inlier_residuals = residuals[inliers]

    # Independent coarse check: the named OSM street was not used by the
    # building-centroid fit.  After transforming and clipping it to the work
    # boundary, measure how much lies inside exact authored road HATCHes.
    road_parts = []
    for entity in project_document.modelspace().query("HATCH"):
        primary = entity.dxf.layer.split(" за ", 1)[0]
        if "Тип2_ПЧ" not in primary and "Тип4_ПЧ" not in primary:
            continue
        geometry = hatch_geometry(entity, 1.0, _hatch_polygon_geometry)
        if geometry is not None:
            road_parts.append(shape(geometry).intersection(boundary))
    authored_road = unary_union(road_parts)
    named_street_parts = []
    for way in ways.values():
        if way["tags"].get("name") != "Кустанайская улица" or len(way["coordinates"]) < 2:
            continue
        named_street_parts.append(LineString(apply(way["coordinates"], *model)).intersection(boundary))
    named_street = unary_union(named_street_parts)
    named_street_inside_road_m = named_street.intersection(authored_road).length
    receipt = {
        "schema": "green-atlas.candidate-osm-alignment.v1",
        "status": "candidate_only_not_survey_control",
        "sources": {
            "topography": {"path": str(topography_path), "sha256": sha256(topography_path)},
            "project_surfaces": {"path": str(project_path), "sha256": sha256(project_path)},
            "osm": {"path": str(OSM), "sha256": sha256(OSM)},
        },
        "projection_before_fit": {
            "method": "local equirectangular tangent approximation",
            "reference_lon_lat": list(REFERENCE_LON_LAT),
            "earth_radius_m": EARTH_RADIUS_M,
        },
        "similarity_transform_row_vector": {
            "formula": "dxf_xy = scale * tangent_xy @ rotation + translation",
            "scale": float(scale),
            "rotation": rotation.tolist(),
            "rotation_degrees": angle_degrees,
            "translation": translation.tolist(),
        },
        "ransac": {
            "threshold_m": INLIER_THRESHOLD_M,
            "controls_total": len(controls),
            "inliers": int(inliers.sum()),
            "rmse_inliers_m": float(math.sqrt(np.mean(np.square(inlier_residuals)))),
            "max_inlier_residual_m": float(inlier_residuals.max()),
        },
        "control_discovery": control_discovery,
        "controls": controls,
        "independent_coarse_check": {
            "feature": "OSM ways named Кустанайская улица against authored DXF road HATCH union",
            "named_street_length_in_work_boundary_m": named_street.length,
            "length_inside_authored_road_m": named_street_inside_road_m,
            "inside_ratio": named_street_inside_road_m / named_street.length,
            "interpretation": "confirms coarse placement only; a road corridor is too wide to validate survey accuracy",
        },
        "limitations": [
            "Building centroids from two independent cartographic sources are coarse tie objects, not geodetic control points.",
            "All inlier controls participate in the fit; there are no independent validation checkpoints yet.",
            "The equirectangular projection is a local approximation, not an identified Moscow CRS definition.",
            "Do not use this transform for final object placement until surveyed or official control points validate it.",
        ],
    }
    (args.output / "candidate-osm-alignment.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    render_overlay(boundary, buildings, ways, model, controls, args.output / "candidate-osm-alignment.svg")
    print(json.dumps({
        "status": receipt["status"],
        "scale": receipt["similarity_transform_row_vector"]["scale"],
        "rotation_degrees": angle_degrees,
        **receipt["ransac"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
