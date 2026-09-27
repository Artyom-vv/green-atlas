"""Fill every authored project surface with one continuous source-tied Z field.

The output is a bare-earth support surface. Coincident upper/lower curb labels
are reduced to the lower observation; the measured curb mesh carries the
vertical riser separately. A single residual field is shared by road, sidewalk,
lawn and special-surface polygons so semantic boundaries cannot become floating
sheets. No geometry is created outside the exact authored surface polygons.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from shapely.geometry import MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import unary_union
from ezdxf.math.triangulation import mapbox_earcut_2d
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SURFACES = ROOT / ".runtime/deterministic-render-audit-20260920-expanded/authored-surfaces.geojson"
DEFAULT_CONTROLS = ROOT / ".runtime/annotation-constrained-terrain-20260920/terrain.json"
DEFAULT_CONTEXT = ROOT / ".runtime/continuous-context-terrain-20260920/terrain.json"
DEFAULT_OUTPUT = ROOT / ".runtime/full-surface-terrain-20260920"
CLASSES = ("road", "sidewalk", "special_surface", "lawn")
TOPOLOGY_TOLERANCE_M = 0.02
MIN_BASE_TRIANGLE_AREA_M2 = 0.001


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def polygons(geometry) -> Iterable[Polygon]:
    if geometry.is_empty:
        return
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, MultiPolygon):
        yield from geometry.geoms
    else:
        for part in getattr(geometry, "geoms", []):
            yield from polygons(part)


def subdivide_triangle(coordinates, max_edge_m: float):
    stack = [[(float(point.x), float(point.y)) for point in coordinates]]
    while stack:
        triangle = stack.pop()
        edges = [
            (math.dist(triangle[index], triangle[(index+1) % 3]), index)
            for index in range(3)
        ]
        length, index = max(edges)
        if length <= max_edge_m:
            yield triangle
            continue
        a = triangle[index]
        b = triangle[(index+1) % 3]
        c = triangle[(index+2) % 3]
        midpoint = ((a[0]+b[0])/2.0, (a[1]+b[1])/2.0)
        stack.append([a, midpoint, c])
        stack.append([midpoint, b, c])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surfaces", type=Path, default=DEFAULT_SURFACES)
    parser.add_argument("--controls", type=Path, default=DEFAULT_CONTROLS)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    surfaces = json.loads(args.surfaces.read_text())
    controls = json.loads(args.controls.read_text())
    context = json.loads(args.context.read_text())
    origin = context["coordinate_frame"]["local_origin_dxf_xyz"]
    grid = context["grid"]
    count, half, step = grid["columns"], grid["half_extent_m"], grid["step_m"]

    def context_z(x: float, y: float) -> float:
        local_x, local_y = x-origin[0], y-origin[1]
        fx = min(count-1-1e-9, max(0.0, (local_x+half)/step))
        fy = min(count-1-1e-9, max(0.0, (local_y+half)/step))
        x0, y0 = int(fx), int(fy)
        dx, dy = fx-x0, fy-y0
        z = [
            context["vertices"][y0*count+x0]["xyz"][2],
            context["vertices"][y0*count+x0+1]["xyz"][2],
            context["vertices"][(y0+1)*count+x0]["xyz"][2],
            context["vertices"][(y0+1)*count+x0+1]["xyz"][2],
        ]
        return (z[0]*(1-dx)+z[1]*dx)*(1-dy)+(z[2]*(1-dx)+z[3]*dx)*dy

    exact = {
        semantic_class: unary_union([
            shape(feature["geometry"]) for feature in surfaces["features"]
            if feature["properties"]["class"] == semantic_class
        ]).buffer(0)
        for semantic_class in CLASSES
    }
    # Resolve any real overlap once, with a fixed priority. This removes
    # coincident faces and makes the output renderable without z-fighting.
    raw_resolved = {}
    occupied = Polygon()
    for semantic_class in CLASSES:
        raw_resolved[semantic_class] = exact[semantic_class].difference(occupied).buffer(0)
        occupied = unary_union([occupied, raw_resolved[semantic_class]]).buffer(0)
    raw_occupied = occupied
    resolved = {}
    occupied = Polygon()
    topology_audit = {}
    for semantic_class in CLASSES:
        raw = raw_resolved[semantic_class]
        candidate = raw.simplify(TOPOLOGY_TOLERANCE_M, preserve_topology=True).buffer(0)
        normalized = candidate.difference(occupied).buffer(0)
        resolved[semantic_class] = normalized
        occupied = unary_union([occupied, normalized]).buffer(0)
        topology_audit[semantic_class] = {
            "source_area_m2": round(raw.area, 6),
            "normalized_area_m2": round(normalized.area, 6),
            "area_delta_m2": round(normalized.area-raw.area, 6),
            "hausdorff_distance_m": round(raw.hausdorff_distance(normalized), 6),
        }

    vertices = []
    triangles = []
    triangle_classes = []
    triangle_confidence = []
    vertex_index = {}
    component_rows = defaultdict(list)
    controls_by_class = controls["supports"]

    # Build one bare-earth control field for every semantic surface. At a
    # coincident curb annotation, the lower value is the terrain support and
    # the upper value belongs to the separately rendered curb/top surface.
    bare_earth_by_xy = {}
    for semantic_class, rows in controls_by_class.items():
        for row in rows:
            key = tuple(round(value, 6) for value in row["xy"])
            candidate = {
                **row,
                "semantic_class": semantic_class,
                "context_z_m": context_z(*row["xy"]),
            }
            candidate["residual_m"] = candidate["z_m"]-candidate["context_z_m"]
            current = bare_earth_by_xy.get(key)
            if current is None or candidate["z_m"] < current["z_m"]:
                bare_earth_by_xy[key] = candidate
    global_supports = sorted(
        bare_earth_by_xy.values(), key=lambda row: (row["xy"][0], row["xy"][1], row["id"])
    )

    # The source does not contain an authoritative TIN/LandXML surface. A
    # robust grade plane is therefore the highest-detail vertical model that
    # remains stable on the source's long, extremely thin hatch triangles.
    # Local curb height is preserved by the separate measured curb mesh.
    plane_origin = [
        sum(row["xy"][axis] for row in global_supports)/len(global_supports)
        for axis in (0, 1)
    ]
    plane_matrix = np.asarray([
        [1.0, row["xy"][0]-plane_origin[0], row["xy"][1]-plane_origin[1]]
        for row in global_supports
    ], dtype=float)
    plane_values = np.asarray([row["z_m"] for row in global_supports], dtype=float)
    plane_weights = np.ones(len(global_supports), dtype=float)
    plane_coefficients = None
    for _ in range(4):
        weighted_matrix = plane_matrix*np.sqrt(plane_weights)[:, None]
        weighted_values = plane_values*np.sqrt(plane_weights)
        plane_coefficients = np.linalg.lstsq(
            weighted_matrix, weighted_values, rcond=None
        )[0]
        residuals = plane_matrix@plane_coefficients-plane_values
        plane_weights = np.minimum(1.0, .5/np.maximum(np.abs(residuals), 1e-9))
    residuals = plane_matrix@plane_coefficients-plane_values
    plane_fit = {
        "origin_xy": [round(value, 6) for value in plane_origin],
        "coefficients_z_intercept_dx_dy": [
            round(float(value), 12) for value in plane_coefficients
        ],
        "controls": len(global_supports),
        "rmse_m": round(float(np.sqrt(np.mean(np.square(residuals)))), 6),
        "median_abs_residual_m": round(float(np.median(np.abs(residuals))), 6),
        "max_abs_residual_m": round(float(np.max(np.abs(residuals))), 6),
    }

    def height(x: float, y: float) -> float:
        return float(
            plane_coefficients[0]
            + plane_coefficients[1]*(x-plane_origin[0])
            + plane_coefficients[2]*(y-plane_origin[1])
        )

    for semantic_class in CLASSES:
        components = list(polygons(resolved[semantic_class]))
        assigned = defaultdict(list)
        for support in controls_by_class.get(semantic_class, []):
            point = Point(support["xy"])
            if not components:
                continue
            index = min(range(len(components)), key=lambda i: components[i].distance(point))
            if components[index].distance(point) <= .75:
                support = dict(support)
                support["context_z_m"] = context_z(*support["xy"])
                support["residual_m"] = support["z_m"]-support["context_z_m"]
                assigned[index].append(support)

        for component_index, component in enumerate(components):
            supports = assigned.get(component_index, [])
            confidence = "source_tied_global_grade_plane"

            component_faces = 0
            component_area = 0.0
            exterior = list(component.exterior.coords)[:-1]
            holes = [list(ring.coords)[:-1] for ring in component.interiors]
            candidate_triangles = [
                candidate for candidate in mapbox_earcut_2d(exterior, holes)
                if Polygon([(point.x, point.y) for point in candidate]).area
                >= MIN_BASE_TRIANGLE_AREA_M2
            ]
            refined_triangles = (
                refined
                for candidate in candidate_triangles
                for refined in subdivide_triangle(candidate, 3.0)
            )
            for coordinates in refined_triangles:
                tri = Polygon(coordinates)
                if tri.area < 1e-10:
                    continue
                face = []
                for x, y in list(tri.exterior.coords)[:3]:
                    key = (round(x, 8), round(y, 8))
                    z = round(height(key[0], key[1]), 6)
                    if key in vertex_index:
                        index = vertex_index[key]
                        if abs(vertices[index]["xyz"][2]-z) > 1e-4:
                            raise ValueError(f"Non-welded height at {key}")
                    else:
                        index = len(vertices)
                        vertex_index[key] = index
                        vertices.append({
                            "xyz": [key[0], key[1], z],
                            "semantic_class": semantic_class,
                            "height_status": confidence,
                            "component": component_index,
                        })
                    face.append(index)
                triangles.append(face)
                triangle_classes.append(semantic_class)
                triangle_confidence.append(confidence)
                component_faces += 1
                component_area += tri.area
            component_rows[semantic_class].append({
                "component": component_index,
                "surface_area_m2": round(component.area, 6),
                "triangulated_area_m2": round(component_area, 6),
                "controls": len(supports),
                "confidence": confidence,
                "triangles": component_faces,
            })

    covered = sum(
        Polygon([vertices[index]["xyz"][:2] for index in face]).area
        for face in triangles
    )
    target = occupied.area
    if abs(covered-target) > max(.05, target*1e-5):
        raise ValueError(f"Coverage mismatch {covered} vs {target}")
    triangle_slopes = []
    for face in triangles:
        points = [vertices[index]["xyz"] for index in face]
        denominator = (
            (points[1][0]-points[0][0])*(points[2][1]-points[0][1])
            -(points[2][0]-points[0][0])*(points[1][1]-points[0][1])
        )
        if abs(denominator) < 1e-12:
            continue
        dzdx = (
            (points[1][2]-points[0][2])*(points[2][1]-points[0][1])
            -(points[2][2]-points[0][2])*(points[1][1]-points[0][1])
        )/denominator
        dzdy = (
            (points[1][0]-points[0][0])*(points[2][2]-points[0][2])
            -(points[2][0]-points[0][0])*(points[1][2]-points[0][2])
        )/denominator
        triangle_slopes.append(math.hypot(dzdx, dzdy))
    packet = {
        "schema": "green-atlas.full-surface-terrain.v1",
        "status": "complete_authored_xy_with_mixed_vertical_confidence",
        "inputs": {
            "surfaces": {"path": str(args.surfaces.resolve()), "sha256": sha256(args.surfaces)},
            "controls": {"path": str(args.controls.resolve()), "sha256": sha256(args.controls)},
            "context": {"path": str(args.context.resolve()), "sha256": sha256(args.context)},
        },
        "rules": {
            "class_priority": list(CLASSES),
            "spatial_domain": "resolved exact authored HATCH union only",
            "height_model": "single robust bare-earth grade plane fitted to source controls",
            "coincident_control_rule": "lowest Z is bare-earth; upper curb observation remains in the separate source curb mesh",
            "control_count_after_xy_deduplication": len(global_supports),
            "grade_plane": plane_fit,
            "maximum_output_triangle_edge_m": 3.0,
            "topology_normalization_tolerance_m": TOPOLOGY_TOLERANCE_M,
            "minimum_base_triangle_area_m2": MIN_BASE_TRIANGLE_AREA_M2,
            "neural_generation": False,
        },
        "coverage": {
            "source_resolved_surface_area_m2": round(raw_occupied.area, 6),
            "target_resolved_surface_area_m2": round(target, 6),
            "triangulated_area_m2": round(covered, 6),
            "ratio": round(covered/target, 9),
            "by_class": component_rows,
            "topology_audit": topology_audit,
        },
        "vertices": vertices,
        "triangles": triangles,
        "triangle_classes": triangle_classes,
        "triangle_confidence": triangle_confidence,
        "limitations": [
            "The grade plane is fitted to lower source annotations; it does not claim exact interpolation at every annotation.",
            "The source lacks an authoritative TIN/LandXML surface, so local micro-relief is intentionally omitted.",
            "The reported plane residuals quantify the vertical detail lost by this stable approximation.",
            "Curb top/bottom separation is carried by the source curb mesh, not by independent semantic height fields.",
            "Context-only components remain unsuitable for precise curb or tree contact.",
        ],
    }
    terrain_path = args.output/"terrain.json"
    terrain_path.write_text(json.dumps(packet, indent=2, sort_keys=True)+"\n")
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": mapping(Polygon([vertices[index]["xyz"][:2] for index in face])),
            "properties": {
                "class": semantic_class,
                "height_confidence": confidence,
            },
        } for face, semantic_class, confidence in zip(
            triangles, triangle_classes, triangle_confidence
        )],
    }
    (args.output/"terrain.geojson").write_text(
        json.dumps(geojson, indent=2, sort_keys=True)+"\n"
    )
    receipt = {
        "schema": "green-atlas.full-surface-terrain-receipt.v1",
        "terrain": {"path": str(terrain_path.resolve()), "sha256": sha256(terrain_path)},
        "vertices": len(vertices),
        "triangles": len(triangles),
        "coverage_area_m2": round(covered, 6),
        "coverage_ratio": round(covered/target, 9),
        "confidence_triangle_counts": {
            status: triangle_confidence.count(status)
            for status in sorted(set(triangle_confidence))
        },
        "height_range_m": [
            min(row["xyz"][2] for row in vertices),
            max(row["xyz"][2] for row in vertices),
        ],
        "vertical_model_audit": {
            "grade_plane": plane_fit,
            "maximum_triangle_slope_ratio": round(max(triangle_slopes), 9),
            "triangles_above_50_percent_slope": sum(
                slope > .5 for slope in triangle_slopes
            ),
        },
    }
    (args.output/"receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True)+"\n"
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
