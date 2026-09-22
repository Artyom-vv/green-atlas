#!/usr/bin/env python3
"""Validate the controlled Kustanayskaya render packet and its outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / ".runtime/kustanayskaya-controlled-scene-20260920/scene-packet.json"
DEFAULT_RECEIPT = ROOT / ".runtime/kustanayskaya-controlled-scene-20260920/render-v3/render-receipt.json"
DEFAULT_OUTPUT = ROOT / ".runtime/kustanayskaya-controlled-scene-20260920/validation.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text())
    receipt = json.loads(args.receipt.read_text())
    audit = json.loads(Path(packet["source"]["placement_audit"]).read_text())
    audit_by_id = {int(row["inventory_id"]): row for row in audit["records"]}

    checks = []

    def check(identifier: str, passed: bool, detail: object) -> None:
        checks.append({"id": identifier, "passed": bool(passed), "detail": detail})

    source_hashes = {}
    for name in ("world", "surfaces", "placement_audit", "project_dxf", "alignment"):
        path = Path(packet["source"][name])
        actual = sha256(path)
        expected = packet["source"][f"{name}_sha256"]
        source_hashes[name] = {"actual": actual, "expected": expected}
    check("source_hashes", all(row["actual"] == row["expected"] for row in source_hashes.values()), source_hashes)

    check("osm_project_surfaces_disabled", not packet.get("automatic_context_surfaces"), len(packet.get("automatic_context_surfaces", [])))
    proxy_count = int(receipt["counts"].get("context_window_proxies", 0)) + int(receipt["counts"].get("retail_glazing_proxies", 0))
    check(
        "building_detail_proxies_explicit",
        packet["admission"].get("building_detail_proxies") is True and proxy_count > 0,
        {"enabled": packet["admission"].get("building_detail_proxies"), "count": proxy_count},
    )

    project_marking = [row for row in packet["surfaces"] if row.get("semantic") == "marking"]
    check(
        "project_marking_provenance",
        bool(project_marking) and all(
            row.get("authority") == "authored_project_dxf"
            and row.get("scenario_visibility") == ["project"]
            and row.get("review", {}).get("status") == "exact_project_hatch_from_marking_insert"
            for row in project_marking
        ),
        [{"id": row.get("id"), "authority": row.get("authority"), "visibility": row.get("scenario_visibility")} for row in project_marking],
    )
    inferred_marking = packet.get("road_markings", [])
    check(
        "osm_lane_marking_is_labelled_estimate",
        bool(inferred_marking) and all(
            row.get("authority") == "cartographic_lane_count_inference"
            and row.get("status") == "render_estimate_from_osm_two_opposing_lanes"
            for row in inferred_marking
        ),
        {"count": len(inferred_marking)},
    )
    check(
        "scene_state_receipted",
        receipt.get("scene_state") in packet.get("render_scenarios", {}),
        receipt.get("scene_state"),
    )
    population = packet.get("presentation_population", {})
    check(
        "presentation_population_separated_from_truth",
        population.get("default_enabled") is False
        and population.get("status") == "optional_deterministic_non_observed_layer",
        population,
    )

    surface_errors = []
    road_triangles = []
    sidewalk_triangles = []
    max_surface_slope = 0.0
    for surface in packet["surfaces"]:
        error = abs(float(surface["source_area_m2"]) - float(surface["triangulated_area_m2"]))
        if error > max(0.05, float(surface["source_area_m2"]) * 1e-5):
            surface_errors.append({"id": surface["id"], "area_error_m2": error})
        for triangle in surface["triangles_scene_xyz_m"]:
            polygon = Polygon([point[:2] for point in triangle])
            if surface["semantic"] == "road":
                road_triangles.append(polygon)
            elif surface["semantic"] == "sidewalk":
                sidewalk_triangles.append(polygon)
            a, b, c = triangle
            denominator = (b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])
            if abs(denominator) <= 1e-12:
                continue
            dzdx = ((b[2] - a[2]) * (c[1] - a[1]) - (c[2] - a[2]) * (b[1] - a[1])) / denominator
            dzdy = ((b[0] - a[0]) * (c[2] - a[2]) - (c[0] - a[0]) * (b[2] - a[2])) / denominator
            max_surface_slope = max(max_surface_slope, math.hypot(dzdx, dzdy))
    check("surface_area_conservation", not surface_errors, surface_errors)
    check("surface_slope_bounded", max_surface_slope <= 0.055, {"max_slope_ratio": max_surface_slope})
    check("camera_on_authored_road", unary_union(road_triangles).covers(Point(packet["camera"]["position_scene_xyz_m"][:2])), packet["camera"]["position_scene_xyz_m"][:2])

    road_union = unary_union(road_triangles)
    sidewalk_union = unary_union(sidewalk_triangles)
    car_anchors = population.get("anchors", {}).get("passenger_car", [])
    pedestrian_anchors = population.get("anchors", {}).get("pedestrian", [])
    invalid_population = [
        row["id"] for row in car_anchors
        if row.get("observed") is not False or not road_union.covers(Point(row["scene_xyz_m"][:2]))
    ] + [
        row["id"] for row in pedestrian_anchors
        if row.get("observed") is not False or not sidewalk_union.covers(Point(row["scene_xyz_m"][:2]))
    ]
    check(
        "presentation_anchors_on_admitted_surfaces",
        not invalid_population,
        {"cars": len(car_anchors), "pedestrians": len(pedestrian_anchors), "invalid": invalid_population},
    )
    presentation_asset_hashes = []
    for name, specification in population.get("assets", {}).items():
        path = Path(specification["path"])
        presentation_asset_hashes.append({
            "class": name,
            "matches": path.is_file() and sha256(path) == specification["sha256"],
        })
    check(
        "presentation_assets_hash_locked",
        (bool(presentation_asset_hashes) or receipt.get("presentation_population_rendered") is False)
        and all(row["matches"] for row in presentation_asset_hashes),
        presentation_asset_hashes,
    )
    if receipt.get("presentation_population_rendered"):
        population_counts = receipt.get("presentation_population_counts", {})
        check(
            "presentation_receipt_matches_anchors",
            int(population_counts.get("passenger_car", -1)) == len(car_anchors)
            and int(population_counts.get("pedestrian", -1)) == len(pedestrian_anchors),
            population_counts,
        )

    invalid_curbs = [row for row in packet["curbs"] if not row["status"].startswith("exact_project_polyline_xy") or float(row["height_m"]) <= 0 or float(row["width_m"]) <= 0]
    check("curbs_source_backed_xy", bool(packet["curbs"]) and not invalid_curbs, {"count": len(packet["curbs"]), "invalid": len(invalid_curbs)})

    invalid_infrastructure = [row for row in packet["automatic_infrastructure"] if row.get("placement_status") != "exact_transformed_project_block_geometry_centre"]
    check("project_infrastructure_only", not invalid_infrastructure, {"count": len(packet["automatic_infrastructure"]), "invalid": len(invalid_infrastructure)})

    invalid_vegetation = []
    for row in packet["vegetation"]:
        source = audit_by_id.get(int(row["inventory_id"]))
        if source is None or source["kind"] != "tree" or source["position_status"] != "matched_marker" or source["project_surface"] != "lawn":
            invalid_vegetation.append(row["id"])
    check("vegetation_admission", bool(packet["vegetation"]) and not invalid_vegetation, {"count": len(packet["vegetation"]), "invalid": invalid_vegetation})
    check("no_shrub_geometry_without_footprint", all("shrub" not in row["id"] for row in packet["vegetation"]), packet["audit"]["vegetation_excluded"].get("shrub_or_stump_without_authored_footprint"))

    image_checks = {}
    image_keys = ("image",) if receipt.get("diagnostics_rendered") is False else ("image", "semantic_image")
    for key in image_keys:
        path = Path(receipt["output"][key])
        actual = sha256(path) if path.is_file() else None
        expected = receipt["output"][f"{key}_sha256"]
        image_checks[key] = {"path": str(path), "bytes": path.stat().st_size if path.is_file() else 0, "actual": actual, "expected": expected}
    check("render_outputs", all(row["bytes"] > 100_000 and row["actual"] == row["expected"] for row in image_checks.values()), image_checks)
    check("neural_finish_disabled", packet["neural_finish"]["status"] == "disabled", packet["neural_finish"])

    report = {
        "schema": "green-atlas.controlled-project-scene-validation.v1",
        "status": "passed" if all(row["passed"] for row in checks) else "failed",
        "passed": sum(row["passed"] for row in checks),
        "total": len(checks),
        "checks": checks,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
