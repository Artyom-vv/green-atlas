#!/usr/bin/env python3
"""Independently verify an AutoCAD REGION topology probe.

The verifier deliberately does not import application geometry code. It checks
the source hash, loop closure/planarity and recomputes planar area/perimeter
from the emitted coordinates before a native probe can be used as evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))


def signed_xy_area(coordinates: list[list[float]]) -> float:
    return 0.5 * sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(coordinates, coordinates[1:])
    )


def length_3d(coordinates: list[list[float]]) -> float:
    return sum(
        distance(left, right)
        for left, right in zip(coordinates, coordinates[1:])
    )


def verify(probe_path: Path, *, diagnostic: bool = False) -> dict[str, Any]:
    document = json.loads(probe_path.read_text())
    if document.get("schema") != "green-atlas.autocad-region-topology-probe/1":
        raise ValueError("unexpected probe schema")

    source = document["source"]
    source_path = Path(source["path"])
    if not source_path.is_file():
        raise ValueError(f"source does not exist: {source_path}")
    actual_hash = sha256_file(source_path)
    if actual_hash != source["sha256"]:
        raise ValueError("source SHA-256 does not match the probe")
    database_modified_flags = int(source["database_modified_flags"])
    if database_modified_flags != 0 and not diagnostic:
        raise ValueError("probe was not captured from a clean live database")
    admissible_source = database_modified_flags == 0

    metres_per_unit = float(source["metres_per_unit"])
    tolerance_units = float(document["requested_tolerance_m"]) / metres_per_unit
    regions = document["regions"]
    unresolved = [region for region in regions if region["status"] != "native"]
    failures: list[dict[str, Any]] = []
    maximum_area_error = 0.0
    maximum_perimeter_error = 0.0
    maximum_closure_error = 0.0
    maximum_z_span = 0.0
    total_points = 0
    total_loops = 0

    for region in regions:
        if region["status"] != "native":
            continue
        outer_area = 0.0
        hole_area = 0.0
        reconstructed_perimeter = 0.0
        outer_count = 0
        for loop in region["loops"]:
            coordinates = loop["coordinates"]
            total_loops += 1
            total_points += len(coordinates)
            if len(coordinates) < 4:
                failures.append({"layer": region["layer"], "reason": "loop has fewer than four points"})
                continue
            closure_error = distance(coordinates[0], coordinates[-1])
            maximum_closure_error = max(maximum_closure_error, closure_error)
            z_values = [point[2] for point in coordinates]
            z_span = max(z_values) - min(z_values)
            maximum_z_span = max(maximum_z_span, z_span)
            area = abs(signed_xy_area(coordinates))
            if loop["role"] == "outer":
                outer_count += 1
                outer_area += area
            elif loop["role"] == "hole":
                hole_area += area
            else:
                failures.append({"layer": region["layer"], "reason": "unknown loop role"})
            reconstructed_perimeter += length_3d(coordinates)

            if closure_error > tolerance_units:
                failures.append({"layer": region["layer"], "reason": "loop is not closed"})
            if z_span > tolerance_units:
                failures.append({"layer": region["layer"], "reason": "loop is not WCS-XY planar"})

        if outer_count != 1:
            failures.append(
                {"layer": region["layer"], "reason": f"expected one exterior loop, got {outer_count}"}
            )

        reconstructed_area = outer_area - hole_area
        native_area = float(region["native_area_units2"])
        native_perimeter = float(region["native_perimeter_units"])
        area_error = abs(reconstructed_area - native_area)
        perimeter_error = abs(reconstructed_perimeter - native_perimeter)
        maximum_area_error = max(maximum_area_error, area_error)
        maximum_perimeter_error = max(maximum_perimeter_error, perimeter_error)

        # A chord can move the boundary by at most the requested sagitta at
        # sampled checkpoints. This conservative envelope is only an admission
        # guard; the report still exposes the actual errors.
        area_envelope = max(1e-10, native_perimeter * tolerance_units * 2.0)
        perimeter_envelope = max(1e-10, native_perimeter * 0.01)
        if area_error > area_envelope:
            failures.append(
                {
                    "layer": region["layer"],
                    "reason": "reconstructed area exceeds tolerance envelope",
                    "error": area_error,
                    "envelope": area_envelope,
                }
            )
        if perimeter_error > perimeter_envelope:
            failures.append(
                {
                    "layer": region["layer"],
                    "reason": "reconstructed perimeter exceeds diagnostic envelope",
                    "error": perimeter_error,
                    "envelope": perimeter_envelope,
                }
            )

    summary = document["summary"]
    if summary["regions"] != len(regions):
        failures.append({"reason": "summary region count mismatch"})
    if summary["resolved"] != len(regions) - len(unresolved):
        failures.append({"reason": "summary resolved count mismatch"})
    if summary["loops"] != total_loops:
        failures.append({"reason": "summary loop count mismatch"})
    if summary["points"] != total_points:
        failures.append({"reason": "summary point count mismatch"})

    coverage = document.get("coverage")
    if coverage is not None:
        coverage_keys = [
            (record["handle"], tuple(record["instance_chain"])) for record in coverage
        ]
        if len(coverage_keys) != len(set(coverage_keys)):
            failures.append({"reason": "duplicate source instance in coverage"})
        native_keys = {
            (record["handle"], tuple(record["instance_chain"]))
            for record in coverage
            if record["status"] == "native"
        }
        region_keys = {
            (region["handle"], tuple(region["instance_chain"])) for region in regions
        }
        if native_keys != region_keys:
            failures.append({"reason": "native coverage does not match REGION geometry"})
        status_counts = Counter(record["status"] for record in coverage)
        expected_summary = {
            "source_instances": len(coverage),
            "native": status_counts["native"],
            "context": status_counts["context"],
            "unresolved_instances": status_counts["unresolved"],
        }
        for name, expected in expected_summary.items():
            if summary.get(name) != expected:
                failures.append({"reason": f"summary {name} count mismatch"})

    geometry_passed = not unresolved and not failures
    counts = {
        "regions": len(regions),
        "resolved": len(regions) - len(unresolved),
        "unresolved": len(unresolved),
        "loops": total_loops,
        "points": total_points,
        "failures": len(failures),
    }
    if coverage is not None:
        counts["source_instances"] = len(coverage)
    return {
        "probe": str(probe_path),
        "source": str(source_path),
        "source_sha256": actual_hash,
        "database_modified_flags": database_modified_flags,
        "admissible_source": admissible_source,
        "requested_tolerance_m": document["requested_tolerance_m"],
        "counts": counts,
        "maximum_errors_units": {
            "closure": maximum_closure_error,
            "z_span": maximum_z_span,
            "area": maximum_area_error,
            "perimeter": maximum_perimeter_error,
        },
        "failures": failures[:100],
        "geometry_passed": geometry_passed,
        "passed": geometry_passed and admissible_source,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help="verify geometry despite nonzero DBMOD, while keeping passed=false",
    )
    arguments = parser.parse_args()
    report = verify(arguments.probe, diagnostic=arguments.diagnostic)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(serialized)
    print(serialized, end="")
    success = report["geometry_passed"] if arguments.diagnostic else report["passed"]
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
