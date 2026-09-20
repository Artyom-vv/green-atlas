#!/usr/bin/env python3
"""Independently verify ObjectARX MINSERT cell geometry and provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.cad_bridge import compile_region_probe
from app.cad_bridge.provider import apply_cad_snapshot
from app.dxf_import.adapters import EzdxfReader


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def identity(record: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    return record["handle"], tuple(record.get("instance_chain", []))


def maximum_shift_error(
    baseline: dict[str, Any], candidate: dict[str, Any], offset: list[float]
) -> float:
    errors = []
    if len(baseline["loops"]) != len(candidate["loops"]):
        raise ValueError("MINSERT cell loop count differs from baseline")
    for expected_loop, actual_loop in zip(
        baseline["loops"], candidate["loops"], strict=True
    ):
        if expected_loop["role"] != actual_loop["role"]:
            raise ValueError("MINSERT cell loop role differs from baseline")
        if len(expected_loop["coordinates"]) != len(actual_loop["coordinates"]):
            raise ValueError("MINSERT cell point count differs from baseline")
        for expected, actual in zip(
            expected_loop["coordinates"], actual_loop["coordinates"], strict=True
        ):
            errors.extend(
                abs(actual[index] - (expected[index] + offset[index]))
                for index in range(3)
            )
    return max(errors, default=0.0)


def verify(
    baseline_probe_path: Path, control_source: Path, manifest_path: Path
) -> dict[str, Any]:
    baseline_probe = load(baseline_probe_path)
    probe_path = Path(f"{control_source}.green-atlas.geometry.json")
    probe = load(probe_path)
    manifest = load(manifest_path)
    if digest(control_source) != manifest["control_sha256"]:
        raise ValueError("MINSERT control SHA differs")
    if probe["source"]["sha256"] != manifest["control_sha256"]:
        raise ValueError("native probe belongs to a different MINSERT control")
    if probe.get("plugin_version") not in {"0.1.5", "0.1.6"}:
        raise ValueError("MINSERT control requires bridge 0.1.5 or 0.1.6")
    snapshot = compile_region_probe(
        probe, autocad_version="2027.0.1", target="macos-arm64"
    )
    imported = EzdxfReader().read_prepared_file(control_source)
    merged = apply_cad_snapshot(
        imported, snapshot, source_sha256=manifest["control_sha256"]
    )
    native_features = [
        feature
        for feature in merged.geometry.feature_collection["features"]
        if feature.get("properties", {}).get("source_geometry_provider")
        == "autocad_snapshot_v1"
    ]

    baseline_nested = [
        region
        for region in baseline_probe["regions"]
        if len(region.get("instance_chain", [])) == 2
    ]
    if len(baseline_nested) != 1:
        raise ValueError("baseline must contain one depth-two REGION")
    baseline = baseline_nested[0]
    candidates = {
        identity(region): region
        for region in probe["regions"]
        if region["handle"] == baseline["handle"]
    }
    maximum_error = 0.0
    checked = []
    trailing_chain = baseline["instance_chain"][1:]
    for cell in manifest["cells"]:
        chain = (cell["token"], *trailing_chain)
        candidate = candidates.get((baseline["handle"], chain))
        if candidate is None:
            raise ValueError(f"missing MINSERT cell REGION: {chain}")
        error = maximum_shift_error(baseline, candidate, cell["offset_wcs"])
        maximum_error = max(maximum_error, error)
        if not math.isclose(
            candidate["native_area_units2"],
            baseline["native_area_units2"],
            abs_tol=1e-9,
        ):
            raise ValueError(f"MINSERT cell area differs: {chain}")
        if not math.isclose(
            candidate["native_perimeter_units"],
            baseline["native_perimeter_units"],
            abs_tol=1e-9,
        ):
            raise ValueError(f"MINSERT cell perimeter differs: {chain}")
        checked.append({"instance_chain": list(chain), "maximum_error": error})
    if maximum_error > 1e-8:
        raise ValueError(
            f"MINSERT cell transform error exceeds tolerance: {maximum_error}"
        )
    expected_regions = 1 + len(manifest["cells"])
    if probe["summary"]["regions"] != expected_regions:
        raise ValueError("MINSERT REGION count differs")
    if probe["summary"].get("expanded_minsert_cells") != len(manifest["cells"]):
        raise ValueError("MINSERT expanded cell diagnostic differs")
    if probe["summary"].get("unexpanded_minsert_blocks") != 0:
        raise ValueError("MINSERT remained unexpanded")
    if len(native_features) != expected_regions:
        raise ValueError("provider did not merge every MINSERT REGION instance")
    return {
        "bridge_version": probe["plugin_version"],
        "control": str(control_source.resolve()),
        "control_sha256": manifest["control_sha256"],
        "regions": probe["summary"]["regions"],
        "expanded_cells": probe["summary"]["expanded_minsert_cells"],
        "maximum_coordinate_error_units": maximum_error,
        "snapshot_payload_sha256": snapshot.summary.payload_sha256,
        "provider_native_regions": len(native_features),
        "provider_incomplete_layers": [
            layer.source_name for layer in merged.layers if not layer.geometry_complete
        ],
        "cells": checked,
        "passed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_probe", type=Path)
    parser.add_argument("control_source", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = verify(
        arguments.baseline_probe, arguments.control_source, arguments.manifest
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
