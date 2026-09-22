#!/usr/bin/env python3
"""Validate NSPD trace provenance, topology, conflict resolution, and cameras."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from shapely.geometry import Point, shape


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / ".runtime/nspd-surface-trace-kustanayskaya-20260920/surface-packet.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    packet = json.loads(args.packet.read_text())
    trace_path = ROOT / packet["source"]["trace_path"]
    trace = json.loads(trace_path.read_text())
    ortho_path = ROOT / packet["source"]["orthophoto_manifest"]
    world_path = ROOT / packet["source"]["world"]
    kartaview_path = ROOT / packet["source"]["kartaview_manifest"]
    ortho = json.loads(ortho_path.read_text())
    kartaview = json.loads(kartaview_path.read_text())
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: object) -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    check(
        "schema",
        packet.get("schema") == "green-atlas.orthophoto-surface-packet.v1",
        packet.get("schema"),
    )
    source_hashes = {
        "trace": sha256(trace_path),
        "world": sha256(world_path),
        "kartaview": sha256(kartaview_path),
    }
    check(
        "source_hashes",
        source_hashes["trace"] == packet["source"]["trace_sha256"]
        and source_hashes["world"] == packet["source"]["world_sha256"]
        and source_hashes["kartaview"] == packet["source"]["kartaview_manifest_sha256"]
        and ortho["mosaic"]["cropped_sha256"] == packet["source"]["orthophoto_sha256"],
        source_hashes,
    )
    width, height = ortho["mosaic"]["cropped_pixel_size"]
    out_of_bounds = []
    for feature in packet["traced_surfaces"]:
        for ring in feature["pixel_geometry"]["coordinates"]:
            for x, y in ring:
                if not (0 <= x < width and 0 <= y < height):
                    out_of_bounds.append([feature["id"], x, y])
    check("pixel_vertices_inside_source", not out_of_bounds, out_of_bounds)

    roi = shape(packet["scene_roi"]["geometry_local"])
    invalid = []
    for feature in packet["traced_surfaces"]:
        raw = shape(feature["geometry_local"])
        resolved = shape(feature["resolved_geometry_local"])
        if (
            not raw.is_valid
            or not resolved.is_valid
            or raw.area <= 0
            or resolved.area <= 0
            or not roi.covers(raw)
            or resolved.area > raw.area + 1e-8
        ):
            invalid.append(feature["id"])
    check("trace_geometry", not invalid, invalid)

    references = {
        feature["semantic"]: shape(feature["geometry_local"])
        for feature in packet["referenced_world_features"]
    }
    reference_overlaps = []
    for feature in packet["traced_surfaces"]:
        resolved = shape(feature["resolved_geometry_local"])
        for semantic, reference in references.items():
            area = resolved.intersection(reference).area
            if area > 1e-6:
                reference_overlaps.append([feature["id"], semantic, area])
    check("resolved_reference_conflicts", not reference_overlaps, reference_overlaps)
    check("resolved_surface_overlaps", not packet["surface_overlaps"], packet["surface_overlaps"])

    uncertainty_errors = [
        feature["id"]
        for feature in packet["traced_surfaces"]
        if feature["review"]["status"] != "visual_trace_candidate"
        or feature["review"]["horizontal_uncertainty_m"] <= 0
        or not feature["review"]["limitation"]
    ]
    check("uncertainty_declared", not uncertainty_errors, uncertainty_errors)

    parking = shape(
        next(
            feature["resolved_geometry_local"]
            for feature in packet["traced_surfaces"]
            if feature["semantic"] == "parking_apron"
        )
    )
    cameras = {camera["sequence_index"]: Point(camera["local_xy_m"]) for camera in packet["kartaview_cameras"]}
    support = trace["camera_support"]["parking_apron"]
    wrong_outside = [index for index in support["outside_sequence_indices"] if parking.covers(cameras[index])]
    wrong_inside = [index for index in support["inside_sequence_indices"] if not parking.covers(cameras[index])]
    check(
        "kartaview_parking_transition",
        not wrong_outside and not wrong_inside,
        {"wrong_outside": wrong_outside, "wrong_inside": wrong_inside},
    )

    photo_ids = {str(frame["photo_id"]) for frame in kartaview["frames"]}
    vegetation_errors = []
    for candidate in packet.get("vegetation_candidates", []):
        point = Point(candidate["local_xy_m"])
        evidence_ids = {str(value) for value in candidate.get("evidence_photo_ids", [])}
        support = candidate.get("resolved_lawn_support_indices", [])
        reasons = []
        if not roi.covers(point):
            reasons.append("outside_scene_roi")
        if len(support) != 1:
            reasons.append(f"lawn_support_count={len(support)}")
        if float(candidate.get("horizontal_uncertainty_m", 0)) <= 0:
            reasons.append("missing_horizontal_uncertainty")
        if float(candidate.get("height_m", 0)) <= 0:
            reasons.append("non_positive_height")
        if float(candidate.get("canopy_radius_m", 0)) <= 0:
            reasons.append("non_positive_canopy_radius")
        missing_photos = sorted(evidence_ids - photo_ids)
        if not evidence_ids or missing_photos:
            reasons.append(f"missing_evidence_photos={missing_photos}")
        if reasons:
            vegetation_errors.append({"id": candidate["id"], "reasons": reasons})
    check(
        "vegetation_candidates_have_lawn_and_photo_support",
        bool(packet.get("vegetation_candidates")) and not vegetation_errors,
        vegetation_errors,
    )
    check(
        "truth_gate_stays_closed",
        packet["admission"]["status"] == "candidate_pending_overlay_review"
        and "survey export" in packet["admission"]["forbidden_use"],
        packet["admission"],
    )
    result = {
        "schema": "green-atlas.orthophoto-surface-validation.v1",
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
    output = args.packet.parent / "validation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
