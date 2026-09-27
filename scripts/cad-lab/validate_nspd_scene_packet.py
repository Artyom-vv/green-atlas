#!/usr/bin/env python3
"""Validate deterministic geometry and provenance of the NSPD street scene."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / ".runtime/nspd-scene-kustanayskaya-20260920/scene-packet.json"
DEFAULT_COVERAGE = ROOT / ".runtime/nspd-scene-kustanayskaya-20260920/source-coverage-audit.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def triangle_area_xy(triangle: list[list[float]]) -> float:
    a, b, c = triangle
    return abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) * 0.5


def finite_point(point: list[float]) -> bool:
    return len(point) == 3 and all(math.isfinite(float(value)) for value in point)


def main() -> None:
    args = parse_args()
    packet = json.loads(args.packet.read_text())
    checks = []

    def check(name: str, passed: bool, detail) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check("schema", packet.get("schema") == "green-atlas.nspd-street-scene.v1", packet.get("schema"))
    source_errors = []
    for key in ("surface_packet", "world", "alignment", "terrain"):
        path = ROOT / packet["source"][key]
        expected = packet["source"][f"{key}_sha256"]
        actual = sha256(path)
        if actual != expected:
            source_errors.append({"source": key, "expected": expected, "actual": actual})
    check("source_hashes", not source_errors, source_errors)

    surface_errors = []
    for surface in packet["surfaces"] + packet.get("automatic_context_surfaces", []):
        triangles = surface["triangles_scene_xyz_m"]
        if not triangles or any(not all(finite_point(point) for point in triangle) for triangle in triangles):
            surface_errors.append({"id": surface["id"], "reason": "missing_or_nonfinite_triangles"})
            continue
        area = sum(triangle_area_xy(triangle) for triangle in triangles)
        delta = abs(area - float(surface["source_area_m2"]))
        # Scene coordinates are serialized to micrometre precision; summed XY
        # triangle area therefore has a small deterministic rounding envelope.
        if delta > max(5e-5, float(surface["source_area_m2"]) * 1e-7):
            surface_errors.append({"id": surface["id"], "reason": "area_mismatch", "delta_m2": delta})
    check("surface_mesh_area", not surface_errors, surface_errors)

    infrastructure_errors = [
        row["id"] for row in packet.get("automatic_infrastructure", [])
        if not finite_point(row.get("anchor_scene_xyz_m", []))
        or not row.get("class")
        or not row.get("placement_status")
    ]
    check(
        "automatic_bbox_context",
        len(packet.get("automatic_context_surfaces", [])) >= 10
        and len(packet.get("automatic_infrastructure", [])) >= 10
        and not infrastructure_errors,
        {
            "surfaces": len(packet.get("automatic_context_surfaces", [])),
            "infrastructure": len(packet.get("automatic_infrastructure", [])),
            "errors": infrastructure_errors,
        },
    )

    curb_errors = []
    for index, curb in enumerate(packet["curbs"]):
        start, end = curb["start_scene_xyz_m"], curb["end_scene_xyz_m"]
        length = math.dist(start, end)
        if not finite_point(start) or not finite_point(end) or length <= 0.25:
            curb_errors.append({"index": index, "length_m": length})
        if float(curb["width_m"]) <= 0 or float(curb["height_m"]) <= 0:
            curb_errors.append({"index": index, "reason": "non_positive_dimensions"})
    check("curb_segments", bool(packet["curbs"]) and not curb_errors, curb_errors)

    marking_errors = []
    for index, marking in enumerate(packet.get("road_markings", [])):
        points = marking.get("points_scene_xyz_m", [])
        if len(points) < 2 or any(not finite_point(point) for point in points) or float(marking.get("width_m", 0)) <= 0:
            marking_errors.append(index)
    check("road_markings", len(packet.get("road_markings", [])) == 2 and not marking_errors, marking_errors)

    building_errors = [
        row["id"] for row in packet["buildings"]
        if float(row["height_m"]) <= 0
        or not row["rings_scene_xyz_m"]
        or any(len(ring) < 3 or any(not finite_point(point) for point in ring) for ring in row["rings_scene_xyz_m"])
    ]
    check(
        "buildings",
        sum(bool(row["is_primary_retail"]) for row in packet["buildings"]) == 1 and not building_errors,
        building_errors,
    )

    unresolved_building_errors = [
        row["id"] for row in packet.get("unresolved_building_footprints", [])
        if row.get("status") != "footprint_preserved_height_unknown_not_extruded"
        or row.get("height_status") != "unknown"
        or not row.get("rings_scene_xyz_m")
        or any(len(ring) < 3 or any(not finite_point(point) for point in ring) for ring in row["rings_scene_xyz_m"])
    ]
    check(
        "unknown_building_footprints_preserved",
        bool(packet.get("unresolved_building_footprints")) and not unresolved_building_errors,
        {
            "count": len(packet.get("unresolved_building_footprints", [])),
            "errors": unresolved_building_errors,
        },
    )

    coverage = json.loads(args.coverage.read_text())
    coverage_scene_hash = coverage.get("inputs", {}).get("scene", {}).get("sha256")
    coverage_ok = (
        coverage.get("schema") == "green-atlas.scene-source-coverage-audit.v1"
        and coverage.get("status") == "passed"
        and coverage_scene_hash == sha256(args.packet)
        and not coverage.get("unaccounted")
    )
    check(
        "source_coverage_gate",
        coverage_ok,
        {
            "coverage": str(args.coverage),
            "status": coverage.get("status"),
            "scene_sha256": coverage_scene_hash,
            "expected_scene_sha256": sha256(args.packet),
            "unaccounted": coverage.get("unaccounted"),
        },
    )

    vegetation_errors = [
        row["id"] for row in packet["vegetation"]
        if not finite_point(row["scene_xyz_m"])
        or float(row["height_m"]) <= 0
        or len(row.get("resolved_lawn_support_indices", [])) != 1
    ]
    check("vegetation_ground_contact", len(packet["vegetation"]) >= 3 and not vegetation_errors, vegetation_errors)

    camera = packet["camera"]
    camera_ok = (
        finite_point(camera["position_scene_xyz_m"])
        and finite_point(camera["target_scene_xyz_m"])
        and math.dist(camera["position_scene_xyz_m"], camera["target_scene_xyz_m"]) > 5.0
        and float(camera["lens_mm_proxy"]) > 0
    )
    check("camera", camera_ok, camera)
    check(
        "truth_gate",
        packet["status"] == "deterministic_geometry_prototype_not_beauty_admitted"
        and packet["neural_finish"]["status"] == "disabled"
        and packet["coordinate_frame"]["horizontal_alignment_status"] == "candidate_only_not_survey_control",
        {
            "scene_status": packet["status"],
            "neural_finish": packet["neural_finish"],
            "alignment": packet["coordinate_frame"]["horizontal_alignment_status"],
        },
    )

    result = {
        "schema": "green-atlas.nspd-street-scene-validation.v1",
        "passed": all(row["passed"] for row in checks),
        "checks": checks,
    }
    output = args.packet.parent / "validation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
