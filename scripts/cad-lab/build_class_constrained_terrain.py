"""Build an estimated class-constrained terrain without spatial extrapolation.

This combines two already-audited kinds of evidence:

* conservative single spot-height hypotheses away from curbs;
* the upper member of five manually reviewed curb pairs, interpolated only
  between controls along each exact source-derived curb chain.

Triangles are built independently for sidewalk and lawn, clipped to exact
project HATCH geometry, and never leave the convex hull of their controls.
The result remains estimated and is not admitted as surveyed terrain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from shapely import constrained_delaunay_triangles, delaunay_triangles
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / ".runtime/deterministic-render-audit-20260919"
DEFAULT_OUTPUT = ROOT / ".runtime/terrain-control-20260920/class-constrained"
CLASSES = ("sidewalk", "lawn")
PALETTE = {"road": "#707780", "sidewalk": "#ded2bf", "lawn": "#9abb82"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def interpolate(rows: list[tuple[float, float]], station: float) -> float:
    rows = sorted(rows)
    if not rows[0][0] - 1e-8 <= station <= rows[-1][0] + 1e-8:
        raise ValueError("Curb height extrapolation is forbidden")
    if station <= rows[0][0]:
        return rows[0][1]
    if station >= rows[-1][0]:
        return rows[-1][1]
    left, right = next((a, b) for a, b in zip(rows, rows[1:]) if a[0] <= station <= b[0])
    ratio = (station-left[0]) / max(right[0]-left[0], 1e-12)
    return left[1] + ratio*(right[1]-left[1])


def barycentric_z(x: float, y: float, supports: list[dict[str, Any]]) -> float:
    (x1, y1), (x2, y2), (x3, y3) = [row["xy"] for row in supports]
    z1, z2, z3 = [row["z"] for row in supports]
    denominator = (y2-y3)*(x1-x3) + (x3-x2)*(y1-y3)
    if abs(denominator) < 1e-12:
        raise ValueError("Degenerate support triangle")
    a = ((y2-y3)*(x-x3) + (x3-x2)*(y-y3))/denominator
    b = ((y3-y1)*(x-x3) + (x1-x3)*(y-y3))/denominator
    return a*z1 + b*z2 + (1-a-b)*z3


def svg_path(geometry: Any, transform) -> str:
    commands = []
    for polygon in polygons(geometry):
        for ring in [polygon.exterior, *polygon.interiors]:
            coordinates = list(ring.coords)
            if not coordinates:
                continue
            x, y = transform(*coordinates[0])
            commands.append(f"M{x:.2f},{y:.2f}")
            for coordinate in coordinates[1:]:
                x, y = transform(*coordinate)
                commands.append(f"L{x:.2f},{y:.2f}")
            commands.append("Z")
    return " ".join(commands)


def write_review_artifacts(terrain: dict[str, Any], surfaces: dict[str, Any], output: Path) -> None:
    features = []
    triangle_geometries = {key: [] for key in CLASSES}
    for face, semantic_class in zip(terrain["triangles"], terrain["triangle_classes"]):
        polygon = Polygon([terrain["vertices"][index]["xyz"][:2] for index in face])
        triangle_geometries[semantic_class].append(polygon)
        features.append({
            "type": "Feature",
            "geometry": json.loads(json.dumps(polygon.__geo_interface__)),
            "properties": {
                "surface_class": semantic_class,
                "height_status": "estimated_class_constrained",
                "support_ids": terrain["vertices"][face[0]]["support_ids"],
            },
        })
    (output / "terrain.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": features,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    exact = {key: unary_union([shape(row["geometry"]) for row in surfaces["features"]
                               if row["properties"]["class"] == key])
             for key in ("road", *CLASSES)}
    bounds = unary_union(list(exact.values())).bounds
    width, height, margin = 1060, 860, 42
    scale = min((width-2*margin)/(bounds[2]-bounds[0]), (height-2*margin)/(bounds[3]-bounds[1]))

    def transform(x, y):
        return margin+(x-bounds[0])*scale, height-margin-(y-bounds[1])*scale

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f7f7f4"/>',
        '<g fill-rule="evenodd">',
    ]
    for semantic_class in ("road", "lawn", "sidewalk"):
        svg.append(f'<path d="{svg_path(exact[semantic_class], transform)}" fill="{PALETTE[semantic_class]}" '
                   'fill-opacity="0.30" stroke="#60676d" stroke-width="0.8"/>')
    coverage_colors = {"sidewalk": "#2c7fb8", "lawn": "#20a85b"}
    for semantic_class in CLASSES:
        coverage = unary_union(triangle_geometries[semantic_class]) if triangle_geometries[semantic_class] else Polygon()
        svg.append(f'<path d="{svg_path(coverage, transform)}" fill="{coverage_colors[semantic_class]}" '
                   'fill-opacity="0.62" stroke="#173a32" stroke-width="1"/>')
    svg.append('</g><g>')
    for semantic_class in CLASSES:
        for support in terrain["supports"][semantic_class]:
            x, y = transform(*support["xy"])
            radius = 3.2 if support["kind"].startswith("single") else 1.6
            fill = "#111111" if support["kind"].startswith("single") else "#ffffff"
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius}" fill="{fill}" '
                       f'stroke="{coverage_colors[semantic_class]}" stroke-width="1.2"/>')
    svg.extend([
        '<rect x="54" y="54" width="390" height="102" rx="8" fill="#ffffff" fill-opacity="0.92"/>',
        '<text x="72" y="82" font-family="Arial" font-size="18" fill="#1d2724">Estimated class-constrained terrain</text>',
        '<text x="72" y="108" font-family="Arial" font-size="14" fill="#1d2724">blue = sidewalk Z · green = lawn Z</text>',
        '<text x="72" y="132" font-family="Arial" font-size="14" fill="#1d2724">black = single spot · white = curb-top support</text>',
        '<text x="72" y="150" font-family="Arial" font-size="12" fill="#8a2f22">review only · no extrapolation · not surveyed</text>',
        '</g></svg>',
    ])
    (output / "coverage.svg").write_text("\n".join(svg) + "\n")


def triangle_slope(vertices: list[dict[str, Any]], face: list[int]) -> float:
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = [vertices[index]["xyz"] for index in face]
    denominator = (x2-x1)*(y3-y1) - (x3-x1)*(y2-y1)
    if abs(denominator) < 1e-12:
        raise ValueError("Degenerate output triangle")
    dzdx = ((z2-z1)*(y3-y1) - (z3-z1)*(y2-y1))/denominator
    dzdy = ((x2-x1)*(z3-z1) - (x3-x1)*(z2-z1))/denominator
    return math.hypot(dzdx, dzdy)


def build(
    surfaces_path: Path,
    general_terrain_path: Path,
    road_terrain_path: Path,
    curb_corridor_path: Path,
) -> dict[str, Any]:
    surfaces = json.loads(surfaces_path.read_text())
    general = json.loads(general_terrain_path.read_text())
    road = json.loads(road_terrain_path.read_text())
    corridor = json.loads(curb_corridor_path.read_text())
    class_geometry = {
        semantic_class: unary_union([
            shape(feature["geometry"]) for feature in surfaces["features"]
            if feature["properties"]["class"] == semantic_class
        ])
        for semantic_class in ("road", *CLASSES)
    }
    supports: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # Existing conservative single-height hypotheses are assigned only to the
    # exact project class that covers their PIKET coordinate.
    for row in general["vertices"]:
        point = Point(row["xyz"][:2])
        for semantic_class in CLASSES:
            if class_geometry[semantic_class].buffer(1e-8).covers(point):
                supports[semantic_class].append({
                    "id": f"single:{row['picket']}:{row['label']}",
                    "xy": row["xyz"][:2],
                    "z": row["xyz"][2],
                    "kind": "single_spot_height_hypothesis",
                    "picket": row["picket"],
                    "label": row["label"],
                    "status": row["status"],
                })

    chain_lines = [LineString(row["xy"]) for row in corridor["chains"]]
    pair_controls: list[list[dict[str, Any]]] = [[] for _ in chain_lines]
    for row in road["vertices"]:
        if row.get("alternative_value") is None:
            continue
        point = Point(row["xyz"][:2])
        distances = [line.distance(point) for line in chain_lines]
        chain_index = min(range(len(distances)), key=distances.__getitem__)
        if distances[chain_index] > 0.03:
            raise ValueError(f"Paired control {row['picket']} is not on a curb chain")
        pair_controls[chain_index].append({
            "station": chain_lines[chain_index].project(point),
            "z": row["alternative_value"],
            "picket": row["picket"],
            "label": row["alternative_label"],
        })
    if [len(rows) for rows in pair_controls] != [2, 3]:
        raise ValueError(f"Expected paired curb controls [2, 3], got {[len(rows) for rows in pair_controls]}")

    curb_support_counts = defaultdict(int)
    for chain_index, (chain, line, controls) in enumerate(zip(corridor["chains"], chain_lines, pair_controls)):
        height_rows = [(row["station"], row["z"]) for row in controls]
        station_min, station_max = min(x for x, _ in height_rows), max(x for x, _ in height_rows)
        samples = []
        # Exact projected controls close the interpolation interval. Source chain
        # vertices inside it retain the actual reconstructed curb XY.
        for row in controls:
            point = line.interpolate(row["station"])
            samples.append((row["station"], [point.x, point.y]))
        for xy in chain["xy"]:
            station = line.project(Point(xy))
            if station_min - 1e-8 <= station <= station_max + 1e-8:
                samples.append((station, list(xy)))
        unique_samples = {}
        for station, xy in samples:
            unique_samples[round(station, 8)] = (station, xy)
        ordered_all = [unique_samples[key] for key in sorted(unique_samples)]
        control_stations = {round(row["station"], 8) for row in controls}
        ordered = []
        for sample_index, (station, xy) in enumerate(ordered_all):
            is_control = round(station, 8) in control_stations
            is_endpoint = sample_index in (0, len(ordered_all)-1)
            if is_control or is_endpoint or not ordered or station-ordered[-1][0] >= 1.5:
                ordered.append((station, xy))
        for index, (station, xy) in enumerate(ordered):
            before = ordered[max(0, index-1)][1]
            after = ordered[min(len(ordered)-1, index+1)][1]
            tangent = (after[0]-before[0], after[1]-before[1])
            length = math.hypot(*tangent)
            if length < 1e-9:
                continue
            normals = [(-tangent[1]/length, tangent[0]/length), (tangent[1]/length, -tangent[0]/length)]
            # Select the side that leaves the exact road HATCH, then ask which
            # exact non-road class occupies that side. No synthetic verge width.
            class_votes: dict[str, int] = defaultdict(int)
            for nx, ny in normals:
                for distance in (0.25, 0.5, 1.0, 1.5):
                    probe = Point(xy[0]+nx*distance, xy[1]+ny*distance)
                    if class_geometry["road"].buffer(1e-8).covers(probe):
                        continue
                    for semantic_class in CLASSES:
                        if class_geometry[semantic_class].buffer(1e-8).covers(probe):
                            class_votes[semantic_class] += 1
            if not class_votes:
                continue
            highest = max(class_votes.values())
            selected = sorted(key for key, count in class_votes.items() if count == highest)
            z = interpolate(height_rows, station)
            for semantic_class in selected:
                supports[semantic_class].append({
                    "id": f"curb:{chain['source_insert']}:{station:.8f}:{semantic_class}",
                    "xy": xy,
                    "z": z,
                    "kind": "curb_top_between_reviewed_pairs",
                    "source_insert": chain["source_insert"],
                    "station_m": station,
                    "control_station_range_m": [station_min, station_max],
                    "status": "estimated_pair_role_not_survey_confirmed",
                })
                curb_support_counts[semantic_class] += 1

    output_vertices: list[dict[str, Any]] = []
    output_faces: list[list[int]] = []
    triangle_classes: list[str] = []
    coverage_by_class = {}
    rejected_by_class = {}
    max_edge = 27.0
    min_area = 0.02
    for semantic_class in CLASSES:
        # Deduplicate exact XY deterministically; incompatible duplicate heights
        # are retained as a hard error rather than silently averaged.
        unique: dict[tuple[float, float], dict[str, Any]] = {}
        for row in sorted(supports[semantic_class], key=lambda item: item["id"]):
            key = (round(row["xy"][0], 8), round(row["xy"][1], 8))
            if key in unique and abs(unique[key]["z"]-row["z"]) > 0.03:
                raise ValueError(f"Conflicting heights at {key}: {unique[key]['z']} vs {row['z']}")
            unique.setdefault(key, row)
        class_supports = list(unique.values())
        lookup = {(row["xy"][0], row["xy"][1]): row for row in class_supports}
        retained_pieces = []
        rejected = defaultdict(int)
        if len(class_supports) >= 3:
            for triangle in delaunay_triangles(MultiPoint(list(lookup)) ).geoms:
                coords = list(triangle.exterior.coords)[:3]
                if max(math.dist(coords[i], coords[(i+1) % 3]) for i in range(3)) > max_edge:
                    rejected["long_edge"] += 1
                    continue
                tri_supports = [lookup[(x, y)] for x, y in coords]
                clipped = triangle.intersection(class_geometry[semantic_class])
                for polygon in polygons(clipped):
                    if polygon.area < min_area:
                        rejected["small_clipped_piece"] += 1
                        continue
                    for part in constrained_delaunay_triangles(polygon).geoms:
                        if part.area < min_area:
                            rejected["small_triangle"] += 1
                            continue
                        face = []
                        for x, y in list(part.exterior.coords)[:3]:
                            z = barycentric_z(x, y, tri_supports)
                            face.append(len(output_vertices))
                            output_vertices.append({
                                "xyz": [round(x, 8), round(y, 8), round(z, 6)],
                                "status": "estimated_class_constrained",
                                "surface_class": semantic_class,
                                "support_ids": [row["id"] for row in tri_supports],
                            })
                        output_faces.append(face)
                        triangle_classes.append(semantic_class)
                        retained_pieces.append(part)
        coverage = unary_union(retained_pieces) if retained_pieces else Polygon()
        if not class_geometry[semantic_class].buffer(1e-7).covers(coverage):
            raise ValueError(f"{semantic_class} terrain escaped exact HATCH geometry")
        coverage_by_class[semantic_class] = {
            "support_points": len(class_supports),
            "single_support_points": sum(row["kind"].startswith("single") for row in class_supports),
            "curb_support_points": sum(row["kind"].startswith("curb") for row in class_supports),
            "area_xy_m2": round(coverage.area, 6),
            "exact_hatch_area_m2": round(class_geometry[semantic_class].area, 6),
            "coverage_ratio": round(coverage.area/class_geometry[semantic_class].area, 9),
        }
        rejected_by_class[semantic_class] = dict(sorted(rejected.items()))

    slope_by_class = {}
    for semantic_class in CLASSES:
        values = sorted(
            triangle_slope(output_vertices, face)
            for face, row_class in zip(output_faces, triangle_classes)
            if row_class == semantic_class
        )
        slope_by_class[semantic_class] = {
            "triangle_count": len(values),
            "median_ratio": round(statistics.median(values), 9),
            "p95_ratio": round(values[int(0.95*(len(values)-1))], 9),
            "max_ratio": round(max(values), 9),
        }
    return {
        "schema": "terrain-class-constrained-estimated-v1",
        "status": "experimental_class_constrained_not_survey_confirmed",
        "origin": general["origin"],
        "source_sha256": general["source_sha256"],
        "inputs": {
            "authored_surfaces": {"path": str(surfaces_path), "sha256": sha256(surfaces_path)},
            "single_height_mesh": {"path": str(general_terrain_path), "sha256": sha256(general_terrain_path)},
            "road_height_hypothesis": {"path": str(road_terrain_path), "sha256": sha256(road_terrain_path)},
            "curb_corridor": {"path": str(curb_corridor_path), "sha256": sha256(curb_corridor_path)},
        },
        "rules": {
            "classes": list(CLASSES),
            "max_support_triangle_edge_m": max_edge,
            "min_triangle_area_m2": min_area,
            "curb_support_spacing_m": 1.5,
            "curb_height": "piecewise linear only between paired-control stations; upper pair member",
            "spatial_domain": "Delaunay support triangles clipped to exact class HATCH",
            "outside_support_hull": "unknown",
        },
        "coverage_by_class": coverage_by_class,
        "slope_by_class": slope_by_class,
        "supports": {key: sorted(rows, key=lambda item: item["id"]) for key, rows in supports.items()},
        "vertices": output_vertices,
        "triangles": output_faces,
        "triangle_classes": triangle_classes,
        "rejected_triangles": rejected_by_class,
        "surface_area_xy": round(sum(row["area_xy_m2"] for row in coverage_by_class.values()), 6),
        "limitations": [
            "All height-label associations remain hypotheses, not surveyed terrain admission.",
            "The upper member of each reviewed pair is treated as curb top; this role needs survey legend confirmation.",
            "Delaunay faces are class-clipped but are not a survey-grade constrained triangulation implementation.",
            "No height is extrapolated beyond the convex hull of supports or outside exact project HATCH geometry.",
            "Source vertical datum is recorded as Moscow height system but is not transformed.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surfaces", type=Path, default=AUDIT / "authored-surfaces.geojson")
    parser.add_argument("--general-terrain", type=Path,
                        default=ROOT / ".runtime/terrain-control-20260918/mesh/terrain.json")
    parser.add_argument("--road-terrain", type=Path,
                        default=ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json")
    parser.add_argument("--curbs", type=Path,
                        default=ROOT / ".runtime/curb-corridor-audit-20260919/curb-corridor.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    terrain = build(args.surfaces, args.general_terrain, args.road_terrain, args.curbs)
    target = args.output / "terrain.json"
    target.write_text(json.dumps(terrain, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_review_artifacts(terrain, json.loads(args.surfaces.read_text()), args.output)
    receipt = {
        "schema": "terrain-class-constrained-receipt-v1",
        "terrain": str(target),
        "terrain_sha256": sha256(target),
        "status": terrain["status"],
        "vertices": len(terrain["vertices"]),
        "triangles": len(terrain["triangles"]),
        "coverage_by_class": terrain["coverage_by_class"],
    }
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
