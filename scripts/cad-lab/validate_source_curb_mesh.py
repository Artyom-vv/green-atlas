"""Validate provenance, topology and height bounds of the source curb mesh."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MESH = ROOT / ".runtime/source-curb-mesh-20260920/curb-mesh.json"
DEFAULT_OUTPUT = ROOT / ".runtime/source-curb-mesh-20260920/validation.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(path: Path) -> dict:
    mesh = json.loads(path.read_text())
    checks = []
    source_failures = []
    for name, reference in mesh["inputs"].items():
        source = Path(reference["path"])
        if not source.exists() or sha256(source) != reference["sha256"]:
            source_failures.append(name)
    checks.append({"id": "source_hashes", "passed": not source_failures, "failures": source_failures})

    vertices = mesh["vertices"]
    invalid_vertices = []
    duplicates = []
    seen = {}
    for index, row in enumerate(vertices):
        xyz = row.get("xyz", [])
        if len(xyz) != 3 or not all(math.isfinite(value) for value in xyz):
            invalid_vertices.append(index)
            continue
        key = (row["chain_id"], round(xyz[0], 8), round(xyz[1], 8), row["curb_role"])
        if key in seen:
            duplicates.append([seen[key], index])
        else:
            seen[key] = index
    checks.append({
        "id": "finite_welded_vertices", "passed": not invalid_vertices and not duplicates,
        "invalid": invalid_vertices, "duplicates": duplicates,
    })

    invalid_faces = []
    invalid_roles = []
    invalid_heights = []
    edges: Counter[tuple[int, int]] = Counter()
    for face_index, face in enumerate(mesh["faces"]):
        if len(face) != 4 or len(set(face)) != 4 or any(index < 0 or index >= len(vertices) for index in face):
            invalid_faces.append(face_index)
            continue
        rows = [vertices[index] for index in face]
        if [row["curb_role"] for row in rows] != ["lower", "lower", "upper", "upper"]:
            invalid_roles.append(face_index)
        if len({row["chain_id"] for row in rows}) != 1:
            invalid_roles.append(face_index)
        for lower, upper in ((rows[0], rows[3]), (rows[1], rows[2])):
            height = upper["xyz"][2] - lower["xyz"][2]
            if not 0.08-1e-6 <= height <= 0.30+1e-6:
                invalid_heights.append([face_index, height])
        for left, right in zip(face, face[1:]+face[:1]):
            edges[tuple(sorted((left, right)))] += 1
    nonmanifold = [list(edge) for edge, count in edges.items() if count > 2]
    checks.append({
        "id": "valid_vertical_faces", "passed": not invalid_faces and not invalid_roles and not invalid_heights,
        "invalid_faces": invalid_faces, "invalid_roles": sorted(set(invalid_roles)),
        "invalid_heights": invalid_heights,
    })
    checks.append({
        "id": "manifold_edge_incidence", "passed": not nonmanifold,
        "nonmanifold_edges": nonmanifold,
        "boundary_edges": sum(count == 1 for count in edges.values()),
        "shared_edges": sum(count == 2 for count in edges.values()),
    })

    accepted = {
        row["marker_handle"]: row for row in mesh["associations"]
        if row["status"] == "accepted_unique_curb_chain"
    }
    interval_failures = []
    for index, row in enumerate(mesh["intervals"]):
        left = accepted.get(row["start_marker"])
        right = accepted.get(row["end_marker"])
        if (
            left is None or right is None
            or left["chain_id"] != row["chain_id"] or right["chain_id"] != row["chain_id"]
            or not row["source_insert_handles"]
            or row["length_m"] <= 0
            or row["length_m"] > mesh["rules"]["max_control_interval_m"] + 1e-6
            or row["faces"] <= 0
            or abs(row["station_range_m"][0] - left["chain_station_m"]) > 1e-5
            or abs(row["station_range_m"][1] - right["chain_station_m"]) > 1e-5
        ):
            interval_failures.append(index)
    declared_length = mesh["quality"]["covered_curb_length_m"]
    actual_length = sum(row["length_m"] for row in mesh["intervals"])
    checks.append({
        "id": "bounded_control_intervals", "passed": not interval_failures and abs(actual_length-declared_length) <= 1e-5,
        "failures": interval_failures, "declared_length_m": declared_length,
        "actual_length_m": round(actual_length, 6),
    })

    expected = mesh["quality"]
    count_mismatches = []
    for key, actual in (
        ("accepted_pair_controls", len(accepted)),
        ("vertices", len(vertices)),
        ("faces", len(mesh["faces"])),
        ("mesh_intervals", len(mesh["intervals"])),
    ):
        if expected[key] != actual:
            count_mismatches.append({"key": key, "expected": expected[key], "actual": actual})
    checks.append({"id": "declared_counts", "passed": not count_mismatches, "mismatches": count_mismatches})

    failed = [row["id"] for row in checks if not row["passed"]]
    return {
        "schema": "green-atlas.source-curb-mesh-validation.v1",
        "status": "passed" if not failed else "failed",
        "mesh": {"path": str(path.resolve()), "sha256": sha256(path)},
        "checks": checks,
        "failed_checks": failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mesh", type=Path, default=DEFAULT_MESH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = validate(args.mesh)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
