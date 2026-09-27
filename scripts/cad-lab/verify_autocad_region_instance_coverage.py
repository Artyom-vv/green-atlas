#!/usr/bin/env python3
"""Cross-check native REGION instances against an independent DXF inventory.

The full-drawing probe is not trusted by itself. This verifier requires the
separately produced definition probe and inventory, compares REGION handles and
INSERT chains, then independently applies the recorded matrices to every
sampled definition point.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def transform(point: list[float], matrix: list[float]) -> list[float]:
    x, y, z = point
    return [
        x * matrix[0] + y * matrix[4] + z * matrix[8] + matrix[12],
        x * matrix[1] + y * matrix[5] + z * matrix[9] + matrix[13],
        x * matrix[2] + y * matrix[6] + z * matrix[10] + matrix[14],
    ]


def flattened_coordinates(region: dict[str, Any]) -> list[list[float]]:
    return [point for loop in region["loops"] for point in loop["coordinates"]]


def verify(
    inventory_path: Path,
    definition_probe_path: Path,
    full_probe_path: Path,
    *,
    coordinate_tolerance: float = 1e-8,
) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text())
    definitions = json.loads(definition_probe_path.read_text())
    full = json.loads(full_probe_path.read_text())

    failures: list[dict[str, Any]] = []
    if inventory["source_sha256"] != full["source"]["sha256"]:
        failures.append({"reason": "full probe source hash differs from inventory"})

    expected = {item["handle"]: item for item in inventory["acis"]}
    actual = {item["handle"]: item for item in full["regions"]}
    definitions_by_source = {
        item["source_layer"].removeprefix("SOURCE_"): item
        for item in definitions["regions"]
    }
    if len(actual) != len(full["regions"]):
        failures.append({"reason": "duplicate REGION handles in full probe"})

    missing_handles = sorted(set(expected) - set(actual))
    extra_handles = sorted(set(actual) - set(expected))
    missing_definitions = sorted(set(expected) - set(definitions_by_source))
    if missing_handles:
        failures.append({"reason": "missing REGION handles", "handles": missing_handles[:100]})
    if extra_handles:
        failures.append({"reason": "unexpected REGION handles", "handles": extra_handles[:100]})
    if missing_definitions:
        failures.append(
            {"reason": "missing definition probes", "handles": missing_definitions[:100]}
        )

    instances_by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for instance in inventory["acis_instances"]:
        instances_by_block[instance["block"]].append(instance)

    compared = 0
    maximum_coordinate_error = 0.0
    chain_depths: dict[int, int] = defaultdict(int)
    for handle in sorted(set(expected) & set(actual) & set(definitions_by_source)):
        metadata = expected[handle]
        region = actual[handle]
        definition = definitions_by_source[handle]
        chain = region["instance_chain"]
        candidates = instances_by_block.get(metadata["layout"], [])
        instance = next(
            (
                candidate
                for candidate in candidates
                if [part["handle"] for part in candidate["chain"]] == chain
            ),
            None,
        )
        if instance is None:
            failures.append(
                {
                    "handle": handle,
                    "reason": "INSERT chain does not match independent inventory",
                    "chain": chain,
                }
            )
            continue

        world_points = flattened_coordinates(region)
        definition_points = flattened_coordinates(definition)
        if len(world_points) != len(definition_points):
            failures.append(
                {
                    "handle": handle,
                    "reason": "definition and world point counts differ",
                }
            )
            continue

        region_maximum_error = 0.0
        for definition_point, world_point in zip(
            definition_points, world_points, strict=True
        ):
            error = math.dist(transform(definition_point, instance["matrix"]), world_point)
            region_maximum_error = max(region_maximum_error, error)
        maximum_coordinate_error = max(maximum_coordinate_error, region_maximum_error)
        if region_maximum_error > coordinate_tolerance:
            failures.append(
                {
                    "handle": handle,
                    "reason": "world coordinates differ from independent matrix transform",
                    "maximum_error": region_maximum_error,
                }
            )
            continue
        compared += 1
        chain_depths[len(chain)] += 1

    return {
        "inventory": str(inventory_path),
        "definition_probe": str(definition_probe_path),
        "full_probe": str(full_probe_path),
        "source_sha256": inventory["source_sha256"],
        "coordinate_tolerance_units": coordinate_tolerance,
        "counts": {
            "expected_regions": len(expected),
            "actual_regions": len(actual),
            "compared_regions": compared,
            "failures": len(failures),
        },
        "chain_depths": dict(sorted(chain_depths.items())),
        "maximum_coordinate_error_units": maximum_coordinate_error,
        "failures": failures[:100],
        "passed": not failures and compared == len(expected),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("definition_probe", type=Path)
    parser.add_argument("full_probe", type=Path)
    parser.add_argument("--coordinate-tolerance", type=float, default=1e-8)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = verify(
        arguments.inventory,
        arguments.definition_probe,
        arguments.full_probe,
        coordinate_tolerance=arguments.coordinate_tolerance,
    )
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(serialized)
    print(serialized, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
