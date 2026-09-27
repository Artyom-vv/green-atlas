"""Validate source hashes and spatial invariants of a compiled world packet."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from shapely.geometry import shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / ".runtime/world-base-packet-20260920"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(packet: dict[str, Any], geojson: dict[str, Any]) -> dict[str, Any]:
    checks = []

    source_failures = []
    for name, reference in packet["sources"].items():
        path = Path(reference["path"])
        if not path.exists():
            source_failures.append({"source": name, "reason": "missing"})
            continue
        actual = sha256(path)
        if actual != reference["sha256"]:
            source_failures.append({
                "source": name, "reason": "sha256_mismatch",
                "expected": reference["sha256"], "actual": actual,
            })
    checks.append({
        "id": "source_hashes", "passed": not source_failures,
        "failures": source_failures,
    })

    features = geojson.get("features", [])
    ids = [feature.get("properties", {}).get("id") for feature in features]
    duplicate_ids = sorted({feature_id for feature_id in ids if ids.count(feature_id) > 1})
    checks.append({
        "id": "stable_unique_feature_ids",
        "passed": None not in ids and not duplicate_ids,
        "duplicates": duplicate_ids,
    })

    invalid = []
    for feature in features:
        geometry = shape(feature["geometry"])
        if geometry.is_empty or not geometry.is_valid:
            invalid.append(feature["properties"]["id"])
    checks.append({"id": "valid_nonempty_geometry", "passed": not invalid, "invalid": invalid})

    project = [
        shape(feature["geometry"]) for feature in features
        if feature["properties"].get("kind") == "project_surface_override"
    ]
    authority = unary_union(project)
    conflicts = []
    wrong_centerline_types = []
    for feature in features:
        properties = feature["properties"]
        if properties.get("kind") != "transportation_centerline":
            continue
        geometry = shape(feature["geometry"])
        overlap = geometry.intersection(authority).length
        if overlap > 0.001:
            conflicts.append({"id": properties["id"], "overlap_m": overlap})
        if geometry.geom_type not in {"LineString", "MultiLineString"}:
            wrong_centerline_types.append({"id": properties["id"], "type": geometry.geom_type})
    checks.append({
        "id": "external_geometry_clipped_by_feature_authority",
        "passed": not conflicts, "conflicts": conflicts,
    })
    checks.append({
        "id": "unknown_width_remains_centerline",
        "passed": not wrong_centerline_types, "wrong_types": wrong_centerline_types,
    })

    invalid_curbs = []
    for feature in features:
        properties = feature["properties"]
        if properties.get("kind") != "curb_riser_segment":
            continue
        lower = feature["geometry"].get("coordinates", [])
        upper = properties.get("upper_endpoints_local_xyz", [])
        if len(lower) != 2 or len(upper) != 2:
            invalid_curbs.append({"id": properties["id"], "reason": "endpoint_count"})
            continue
        heights = []
        valid_xy = True
        for low, high in zip(lower, upper):
            if len(low) != 3 or len(high) != 3 or abs(low[0]-high[0]) > 1e-8 or abs(low[1]-high[1]) > 1e-8:
                valid_xy = False
                break
            heights.append(high[2]-low[2])
        if not valid_xy or any(not 0.08-1e-6 <= height <= 0.30+1e-6 for height in heights):
            invalid_curbs.append({"id": properties["id"], "reason": "vertical_pair", "heights": heights})
    checks.append({
        "id": "curb_segments_reconstruct_vertical_quads",
        "passed": not invalid_curbs, "invalid": invalid_curbs,
    })

    accidentally_eligible = [
        feature["properties"]["id"] for feature in features
        if feature["properties"].get("render_eligible") is True
    ]
    checks.append({
        "id": "blocked_packet_contains_no_render_eligible_geometry",
        "passed": packet["status"] != "diagnostic_world_base_not_beauty" or not accidentally_eligible,
        "unexpected": accidentally_eligible,
    })

    expected_counts = packet["feature_counts"]
    actual_counts: dict[str, int] = {}
    for feature in features:
        kind = feature["properties"]["kind"]
        actual_counts[kind] = actual_counts.get(kind, 0) + 1
    checks.append({
        "id": "feature_counts_match_packet",
        "passed": expected_counts == dict(sorted(actual_counts.items())),
        "expected": expected_counts, "actual": dict(sorted(actual_counts.items())),
    })

    failed = [check["id"] for check in checks if not check["passed"]]
    return {
        "schema": "green-atlas.world-base-validation.v1",
        "status": "passed_diagnostic_invariants" if not failed else "failed",
        "failed_checks": failed,
        "checks": checks,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_DIR / "world-base-packet.json")
    parser.add_argument("--geojson", type=Path, default=DEFAULT_DIR / "world-base-local.geojson")
    parser.add_argument("--output", type=Path, default=DEFAULT_DIR / "validation.json")
    args = parser.parse_args()
    result = validate(load(args.packet), load(args.geojson))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result["failed_checks"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
