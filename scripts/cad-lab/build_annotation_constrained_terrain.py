"""Build class-isolated terrain from source-backed topographic annotations.

No height is generated outside a control hull, across semantic classes, or
between disconnected surface components.  Steep and long support triangles are
rejected.  The result remains reviewable annotation-derived terrain until the
topographic legend/roles are approved or an engineering TIN supersedes it.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import shutil
import statistics
import subprocess
from typing import Any, Iterable

from shapely import constrained_delaunay_triangles, delaunay_triangles
from shapely.geometry import MultiPoint, MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SURFACES = (
    ROOT / ".runtime/deterministic-render-audit-20260920-expanded/authored-surfaces.geojson"
)
DEFAULT_CONTROLS = (
    ROOT / ".runtime/topographic-elevation-controls-20260920/elevation-controls.json"
)
DEFAULT_OUTPUT = ROOT / ".runtime/annotation-constrained-terrain-20260920"
CLASSES = ("road", "sidewalk", "lawn", "special_surface")
COLORS = {
    "road": "#565d64", "sidewalk": "#d6cbbd", "lawn": "#91ad70",
    "special_surface": "#bd9270",
}
COVERAGE = {
    "road": "#3478c5", "sidewalk": "#27a9a1", "lawn": "#2faa59",
    "special_surface": "#c7772d",
}


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


def barycentric_z(x: float, y: float, supports: list[dict[str, Any]]) -> float:
    (x1, y1), (x2, y2), (x3, y3) = [row["xy"] for row in supports]
    z1, z2, z3 = [float(row["z_m"]) for row in supports]
    denominator = (y2-y3)*(x1-x3) + (x3-x2)*(y1-y3)
    if abs(denominator) < 1e-12:
        raise ValueError("Degenerate support triangle")
    a = ((y2-y3)*(x-x3) + (x3-x2)*(y-y3)) / denominator
    b = ((y3-y1)*(x-x3) + (x1-x3)*(y-y3)) / denominator
    return a*z1 + b*z2 + (1-a-b)*z3


def slope_ratio(supports: list[dict[str, Any]]) -> float:
    (x1, y1), (x2, y2), (x3, y3) = [row["xy"] for row in supports]
    z1, z2, z3 = [float(row["z_m"]) for row in supports]
    denominator = (x2-x1)*(y3-y1) - (x3-x1)*(y2-y1)
    if abs(denominator) < 1e-12:
        raise ValueError("Degenerate support triangle")
    dzdx = ((z2-z1)*(y3-y1) - (z3-z1)*(y2-y1)) / denominator
    dzdy = ((x2-x1)*(z3-z1) - (x3-x1)*(z2-z1)) / denominator
    return math.hypot(dzdx, dzdy)


def build(
    surfaces_path: Path,
    controls_path: Path,
    max_edge_m: float = 35.0,
    max_slope_ratio: float = 0.15,
    min_triangle_area_m2: float = 0.02,
) -> dict[str, Any]:
    surfaces = json.loads(surfaces_path.read_text())
    controls_packet = json.loads(controls_path.read_text())
    source_ref = controls_packet["surface_source"]
    if source_ref["sha256"] != sha256(surfaces_path):
        raise ValueError("Control packet was classified against a different surface source")

    exact = {
        semantic_class: unary_union([
            shape(feature["geometry"]) for feature in surfaces["features"]
            if feature.get("properties", {}).get("class") == semantic_class
        ])
        for semantic_class in CLASSES
    }
    supports_by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in controls_packet["controls"]:
        semantic_class = row.get("semantic_class")
        if semantic_class not in CLASSES:
            continue
        supports_by_class[semantic_class].append({
            "id": row["id"],
            "xy": [float(value) for value in row["xy"]],
            "z_m": float(row["z_m"]),
            "kind": row["kind"],
            "curb_role": row.get("curb_role"),
            "marker_handle": row["marker_handle"],
            "label_handles": [label["label_handle"] for label in row["labels"]],
            "vertical_status": row["vertical_status"],
        })

    vertices: list[dict[str, Any]] = []
    vertex_by_xy_class: dict[tuple[str, float, float], int] = {}
    triangles: list[list[int]] = []
    triangle_classes: list[str] = []
    retained_geometry: dict[str, list[Polygon]] = defaultdict(list)
    rejected: dict[str, Counter[str]] = {key: Counter() for key in CLASSES}
    slope_values: dict[str, list[float]] = defaultdict(list)
    component_summaries: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for semantic_class in CLASSES:
        geometry = exact[semantic_class]
        components = list(polygons(geometry))
        groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for support in supports_by_class[semantic_class]:
            point = Point(support["xy"])
            component_index = min(
                range(len(components)), key=lambda index: components[index].distance(point)
            )
            if components[component_index].distance(point) <= 0.5:
                groups[component_index].append(support)
            else:
                rejected[semantic_class]["support_outside_class"] += 1

        for component_index, component in enumerate(components):
            raw_supports = groups.get(component_index, [])
            unique: dict[tuple[float, float], dict[str, Any]] = {}
            for support in sorted(raw_supports, key=lambda row: row["id"]):
                key = tuple(round(value, 8) for value in support["xy"])
                if key in unique and abs(unique[key]["z_m"] - support["z_m"]) > 0.03:
                    raise ValueError(
                        f"Conflicting {semantic_class} controls at {key}: "
                        f"{unique[key]['z_m']} vs {support['z_m']}"
                    )
                unique.setdefault(key, support)
            component_supports = list(unique.values())
            summary = {
                "component": component_index,
                "exact_area_m2": round(component.area, 6),
                "supports": len(component_supports),
                "coverage_area_m2": 0.0,
            }
            if len(component_supports) < 3:
                rejected[semantic_class]["component_has_fewer_than_three_controls"] += 1
                component_summaries[semantic_class].append(summary)
                continue
            lookup = {
                tuple(float(value) for value in support["xy"]): support
                for support in component_supports
            }
            component_pieces = []
            for support_triangle in delaunay_triangles(MultiPoint(list(lookup))).geoms:
                coordinates = list(support_triangle.exterior.coords)[:3]
                supports = [lookup[(x, y)] for x, y in coordinates]
                edge = max(
                    math.dist(coordinates[index], coordinates[(index+1) % 3])
                    for index in range(3)
                )
                if edge > max_edge_m:
                    rejected[semantic_class]["support_triangle_too_long"] += 1
                    continue
                slope = slope_ratio(supports)
                if slope > max_slope_ratio:
                    rejected[semantic_class]["support_triangle_too_steep"] += 1
                    continue
                clipped = support_triangle.intersection(component)
                for clipped_polygon in polygons(clipped):
                    if clipped_polygon.area < min_triangle_area_m2:
                        rejected[semantic_class]["clipped_piece_too_small"] += 1
                        continue
                    for face_polygon in constrained_delaunay_triangles(clipped_polygon).geoms:
                        if face_polygon.area < min_triangle_area_m2:
                            rejected[semantic_class]["output_triangle_too_small"] += 1
                            continue
                        face = []
                        for x, y in list(face_polygon.exterior.coords)[:3]:
                            z = round(barycentric_z(x, y, supports), 6)
                            key = (semantic_class, round(x, 8), round(y, 8))
                            if key in vertex_by_xy_class:
                                vertex_index = vertex_by_xy_class[key]
                                existing = vertices[vertex_index]
                                if abs(existing["xyz"][2] - z) > 1e-4:
                                    raise ValueError(
                                        f"Terrain crack at {key}: {existing['xyz'][2]} vs {z}"
                                    )
                                existing["support_ids"] = sorted(set(existing["support_ids"]) | {
                                    row["id"] for row in supports
                                })
                            else:
                                vertex_index = len(vertices)
                                vertex_by_xy_class[key] = vertex_index
                                vertices.append({
                                    "xyz": [key[1], key[2], z],
                                    "semantic_class": semantic_class,
                                    "height_status": "source_annotation_class_constrained",
                                    "support_ids": [row["id"] for row in supports],
                                })
                            face.append(vertex_index)
                        triangles.append(face)
                        triangle_classes.append(semantic_class)
                        slope_values[semantic_class].append(slope)
                        retained_geometry[semantic_class].append(face_polygon)
                        component_pieces.append(face_polygon)
            component_coverage = unary_union(component_pieces) if component_pieces else Polygon()
            if not component_coverage.is_empty and not component.buffer(1e-7).covers(component_coverage):
                raise ValueError(f"{semantic_class} component coverage escaped exact source surface")
            summary["coverage_area_m2"] = round(component_coverage.area, 6)
            summary["coverage_ratio"] = round(component_coverage.area / component.area, 9)
            component_summaries[semantic_class].append(summary)

    coverage_by_class = {}
    for semantic_class in CLASSES:
        coverage = unary_union(retained_geometry[semantic_class]) if retained_geometry[semantic_class] else Polygon()
        if not coverage.is_empty and not exact[semantic_class].buffer(1e-7).covers(coverage):
            raise ValueError(f"{semantic_class} terrain escaped exact source surfaces")
        values = sorted(slope_values[semantic_class])
        coverage_by_class[semantic_class] = {
            "supports": len(supports_by_class[semantic_class]),
            "single_supports": sum(row["kind"] == "single_spot_elevation" for row in supports_by_class[semantic_class]),
            "curb_pair_supports": sum(row["kind"] == "curb_pair_elevation" for row in supports_by_class[semantic_class]),
            "exact_surface_area_m2": round(exact[semantic_class].area, 6),
            "coverage_area_m2": round(coverage.area, 6),
            "coverage_ratio": round(coverage.area / exact[semantic_class].area, 9) if exact[semantic_class].area else 0.0,
            "triangles": sum(row == semantic_class for row in triangle_classes),
            "slope_ratio": {
                "median": round(statistics.median(values), 9) if values else None,
                "p95": round(values[int(0.95*(len(values)-1))], 9) if values else None,
                "max": round(max(values), 9) if values else None,
            },
            "rejected": dict(sorted(rejected[semantic_class].items())),
            "components": component_summaries[semantic_class],
        }
    total_exact = sum(row["exact_surface_area_m2"] for row in coverage_by_class.values())
    total_coverage = sum(row["coverage_area_m2"] for row in coverage_by_class.values())
    z_values = [vertex["xyz"][2] for vertex in vertices]
    return {
        "schema": "green-atlas.annotation-constrained-terrain.v1",
        "status": "source_annotation_constrained_requires_role_review",
        "inputs": {
            "surfaces": {"path": str(surfaces_path.resolve()), "sha256": sha256(surfaces_path)},
            "controls": {"path": str(controls_path.resolve()), "sha256": sha256(controls_path)},
        },
        "rules": {
            "classes": list(CLASSES),
            "component_isolation": True,
            "max_support_triangle_edge_m": max_edge_m,
            "max_support_triangle_slope_ratio": max_slope_ratio,
            "min_output_triangle_area_m2": min_triangle_area_m2,
            "spatial_domain": "per-class Delaunay hull clipped to exact HATCH component",
            "extrapolation": "forbidden outside support hull or exact source surface",
        },
        "coverage": {
            "exact_surface_area_m2": round(total_exact, 6),
            "terrain_area_m2": round(total_coverage, 6),
            "terrain_ratio_of_exact_surfaces": round(total_coverage / total_exact, 9),
            "scope_area_m2": 17325.0,
            "terrain_ratio_of_scope": round(total_coverage / 17325.0, 9),
            "by_class": coverage_by_class,
        },
        "height_range_m": [min(z_values), max(z_values)] if z_values else None,
        "supports": {
            key: sorted(rows, key=lambda row: row["id"])
            for key, rows in supports_by_class.items()
        },
        "vertices": vertices,
        "triangles": triangles,
        "triangle_classes": triangle_classes,
        "limitations": [
            "Controls come from source annotations associated by deterministic text-box proximity.",
            "The source drawing has no native TIN and curb upper/lower semantic roles still require review.",
            "Rejected ambiguous labels and unclassified surfaces remain unknown.",
            "An admitted engineering LandXML/TIN supersedes this terrain when supplied.",
        ],
    }


def svg_path(geometry: Any, project) -> str:
    result = []
    for polygon in polygons(geometry):
        for ring in [polygon.exterior, *polygon.interiors]:
            coordinates = list(ring.coords)
            if not coordinates:
                continue
            x, y = project(*coordinates[0])
            result.append(f"M{x:.2f},{y:.2f}")
            for coordinate in coordinates[1:]:
                x, y = project(*coordinate)
                result.append(f"L{x:.2f},{y:.2f}")
            result.append("Z")
    return " ".join(result)


def write_artifacts(terrain: dict[str, Any], surfaces_path: Path, output: Path) -> None:
    features = []
    coverage_geometry: dict[str, list[Polygon]] = defaultdict(list)
    for face, semantic_class in zip(terrain["triangles"], terrain["triangle_classes"]):
        polygon = Polygon([terrain["vertices"][index]["xyz"][:2] for index in face])
        coverage_geometry[semantic_class].append(polygon)
        features.append({
            "type": "Feature", "geometry": mapping(polygon),
            "properties": {
                "semantic_class": semantic_class,
                "height_status": "source_annotation_class_constrained",
                "vertex_z_m": [terrain["vertices"][index]["xyz"][2] for index in face],
                "support_ids": terrain["vertices"][face[0]]["support_ids"],
            },
        })
    (output / "terrain.geojson").write_text(json.dumps({
        "type": "FeatureCollection", "features": features
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    surfaces = json.loads(surfaces_path.read_text())
    exact = {
        semantic_class: unary_union([
            shape(feature["geometry"]) for feature in surfaces["features"]
            if feature.get("properties", {}).get("class") == semantic_class
        ])
        for semantic_class in CLASSES
    }
    bounds = unary_union(list(exact.values())).bounds
    width, height, margin = 1400, 980, 48
    scale = min((width-2*margin)/(bounds[2]-bounds[0]), (height-2*margin)/(bounds[3]-bounds[1]))

    def project(x: float, y: float) -> tuple[float, float]:
        return margin+(x-bounds[0])*scale, height-margin-(y-bounds[1])*scale

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f1f3f2"/>',
        '<g fill-rule="evenodd" stroke-linejoin="round">',
    ]
    for semantic_class in CLASSES:
        svg.append(
            f'<path d="{svg_path(exact[semantic_class], project)}" fill="{COLORS[semantic_class]}" '
            'fill-opacity="0.22" stroke="#697375" stroke-width="0.65"/>'
        )
    for semantic_class in CLASSES:
        coverage = unary_union(coverage_geometry[semantic_class]) if coverage_geometry[semantic_class] else Polygon()
        svg.append(
            f'<path d="{svg_path(coverage, project)}" fill="{COVERAGE[semantic_class]}" '
            'fill-opacity="0.72" stroke="#17383a" stroke-width="0.85"/>'
        )
    svg.append('</g><g>')
    for semantic_class, rows in terrain["supports"].items():
        for row in rows:
            x, y = project(*row["xy"])
            svg.append(
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.4" fill="#ffffff" '
                f'stroke="{COVERAGE[semantic_class]}" stroke-width="1.5"/>'
            )
    coverage = terrain["coverage"]
    svg.extend([
        '<rect x="28" y="24" width="790" height="112" rx="8" fill="#ffffff" fill-opacity="0.95"/>',
        '<text x="48" y="55" font-family="Arial" font-size="22" font-weight="700" fill="#172426">Высотное покрытие из пикетов и подписей</text>',
        f'<text x="48" y="84" font-family="Arial" font-size="15" fill="#435255">TIN: {coverage["terrain_area_m2"]:.1f} м² · {coverage["terrain_ratio_of_scope"]*100:.1f}% участка · только внутри HATCH и control hull</text>',
        '<text x="48" y="110" font-family="Arial" font-size="14" fill="#9a3c31">Диагностика: роли борта требуют review; неизвестные области не заполнены.</text>',
        '</g></svg>',
    ])
    svg_path_target = output / "coverage.svg"
    svg_path_target.write_text("\n".join(svg) + "\n")
    converter = shutil.which("rsvg-convert")
    if converter:
        subprocess.run(
            [converter, "-o", str(output / "coverage.png"), str(svg_path_target)], check=True
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surfaces", type=Path, default=DEFAULT_SURFACES)
    parser.add_argument("--controls", type=Path, default=DEFAULT_CONTROLS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-edge-m", type=float, default=35.0)
    parser.add_argument("--max-slope-ratio", type=float, default=0.15)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    terrain = build(
        args.surfaces, args.controls, max_edge_m=args.max_edge_m,
        max_slope_ratio=args.max_slope_ratio,
    )
    terrain_path = args.output / "terrain.json"
    terrain_path.write_text(json.dumps(terrain, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_artifacts(terrain, args.surfaces, args.output)
    receipt = {
        "schema": "green-atlas.annotation-constrained-terrain-receipt.v1",
        "status": terrain["status"],
        "terrain": {
            "path": str(terrain_path.resolve()), "bytes": terrain_path.stat().st_size,
            "sha256": sha256(terrain_path),
        },
        "vertices": len(terrain["vertices"]),
        "triangles": len(terrain["triangles"]),
        "coverage": terrain["coverage"],
    }
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
