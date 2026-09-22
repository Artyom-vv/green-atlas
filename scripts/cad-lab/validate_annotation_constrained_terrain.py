"""Validate topology, provenance and spatial bounds of annotation terrain."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from shapely.geometry import Polygon, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TERRAIN = ROOT / ".runtime/annotation-constrained-terrain-20260920/terrain.json"
DEFAULT_OUTPUT = ROOT / ".runtime/annotation-constrained-terrain-20260920/validation.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slope(vertices: list[dict[str, Any]], face: list[int]) -> float:
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = [vertices[index]["xyz"] for index in face]
    denominator = (x2-x1)*(y3-y1) - (x3-x1)*(y2-y1)
    if abs(denominator) < 1e-12:
        return math.inf
    dzdx = ((z2-z1)*(y3-y1) - (z3-z1)*(y2-y1)) / denominator
    dzdy = ((x2-x1)*(z3-z1) - (x3-x1)*(z2-z1)) / denominator
    return math.hypot(dzdx, dzdy)


def validate(path: Path) -> dict[str, Any]:
    terrain = json.loads(path.read_text())
    surface_path = Path(terrain["inputs"]["surfaces"]["path"])
    controls_path = Path(terrain["inputs"]["controls"]["path"])
    surfaces = json.loads(surface_path.read_text())
    controls = json.loads(controls_path.read_text())
    vertices = terrain["vertices"]
    triangles = terrain["triangles"]
    classes = terrain["triangle_classes"]
    checks = []

    hash_failures = []
    for key, source_path in (("surfaces", surface_path), ("controls", controls_path)):
        if not source_path.exists() or sha256(source_path) != terrain["inputs"][key]["sha256"]:
            hash_failures.append(key)
    checks.append({"id": "source_hashes", "passed": not hash_failures, "failures": hash_failures})

    invalid_vertices = [
        index for index, row in enumerate(vertices)
        if len(row.get("xyz", [])) != 3 or not all(math.isfinite(value) for value in row["xyz"])
    ]
    duplicate_xy = []
    xy_seen = {}
    for index, row in enumerate(vertices):
        if index in invalid_vertices:
            continue
        key = (row["semantic_class"], round(row["xyz"][0], 8), round(row["xyz"][1], 8))
        if key in xy_seen:
            duplicate_xy.append([xy_seen[key], index])
        else:
            xy_seen[key] = index
    checks.append({
        "id": "finite_welded_vertices", "passed": not invalid_vertices and not duplicate_xy,
        "invalid": invalid_vertices, "duplicate_xy_class": duplicate_xy,
    })

    invalid_faces = []
    class_mismatches = []
    slopes = []
    edges: Counter[tuple[int, int]] = Counter()
    for face_index, (face, semantic_class) in enumerate(zip(triangles, classes)):
        if len(face) != 3 or len(set(face)) != 3 or any(index < 0 or index >= len(vertices) for index in face):
            invalid_faces.append(face_index)
            continue
        if any(vertices[index]["semantic_class"] != semantic_class for index in face):
            class_mismatches.append(face_index)
        for left, right in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edges[tuple(sorted((left, right)))] += 1
        slopes.append((face_index, slope(vertices, face)))
    if len(triangles) != len(classes):
        invalid_faces.append("triangle_class_count_mismatch")
    checks.append({
        "id": "valid_faces_and_classes", "passed": not invalid_faces and not class_mismatches,
        "invalid_faces": invalid_faces, "class_mismatches": class_mismatches,
    })
    nonmanifold = [list(edge) for edge, count in edges.items() if count > 2]
    checks.append({
        "id": "manifold_edge_incidence", "passed": not nonmanifold,
        "nonmanifold_edges": nonmanifold,
        "boundary_edges": sum(count == 1 for count in edges.values()),
        "shared_edges": sum(count == 2 for count in edges.values()),
    })

    maximum = float(terrain["rules"]["max_support_triangle_slope_ratio"])
    steep = [[index, value] for index, value in slopes if value > maximum + 1e-7]
    checks.append({
        "id": "slope_gate", "passed": not steep, "maximum": maximum,
        "steep_faces": steep,
    })

    exact = {
        semantic_class: unary_union([
            shape(feature["geometry"]) for feature in surfaces["features"]
            if feature.get("properties", {}).get("class") == semantic_class
        ])
        for semantic_class in terrain["rules"]["classes"]
    }
    escaped = []
    geometry_by_class: dict[str, list[Polygon]] = defaultdict(list)
    for face_index, (face, semantic_class) in enumerate(zip(triangles, classes)):
        if not all(isinstance(index, int) and 0 <= index < len(vertices) for index in face):
            continue
        polygon = Polygon([vertices[index]["xyz"][:2] for index in face])
        geometry_by_class[semantic_class].append(polygon)
        if polygon.area <= 0 or not exact[semantic_class].buffer(1e-7).covers(polygon):
            escaped.append(face_index)
    area_mismatches = []
    for semantic_class, geometry in exact.items():
        actual = unary_union(geometry_by_class[semantic_class]).area if geometry_by_class[semantic_class] else 0.0
        declared = terrain["coverage"]["by_class"][semantic_class]["coverage_area_m2"]
        if abs(actual - declared) > 1e-4:
            area_mismatches.append({
                "class": semantic_class, "declared": declared, "actual": round(actual, 6)
            })
    checks.append({
        "id": "contained_geometry_and_coverage", "passed": not escaped and not area_mismatches,
        "escaped_faces": escaped, "area_mismatches": area_mismatches,
    })

    control_ids = {row["id"] for row in controls["controls"]}
    missing_supports = sorted({
        support_id for row in vertices for support_id in row.get("support_ids", [])
        if support_id not in control_ids
    })
    checks.append({
        "id": "support_provenance", "passed": not missing_supports,
        "missing_control_ids": missing_supports,
    })
    failed = [row["id"] for row in checks if not row["passed"]]
    return {
        "schema": "green-atlas.annotation-constrained-terrain-validation.v1",
        "status": "passed" if not failed else "failed",
        "terrain": {"path": str(path.resolve()), "sha256": sha256(path)},
        "counts": {
            "vertices": len(vertices), "triangles": len(triangles),
            "classes": dict(sorted(Counter(classes).items())),
        },
        "checks": checks,
        "failed_checks": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terrain", type=Path, default=DEFAULT_TERRAIN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = validate(args.terrain)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
