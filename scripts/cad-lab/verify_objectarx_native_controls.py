#!/usr/bin/env python3
"""Verify native affine REGION success and fail-closed traversal controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.cad_bridge import CadSnapshotAdmissionError, compile_region_probe
from app.cad_bridge.provider import apply_cad_snapshot
from app.dxf_import.adapters import EzdxfReader


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(positive_source: Path, negative_manifest: Path) -> dict[str, Any]:
    positive_probe_path = Path(f"{positive_source}.green-atlas.geometry.json")
    positive_probe = load(positive_probe_path)
    if positive_probe["source"]["sha256"] != digest(positive_source):
        raise ValueError("positive probe source hash differs")
    if positive_probe.get("plugin_version") not in {"0.1.4", "0.1.5", "0.1.6"}:
        raise ValueError("positive control requires bridge 0.1.4 or newer")
    snapshot = compile_region_probe(
        positive_probe, autocad_version="2027.0.1", target="macos-arm64"
    )
    imported = EzdxfReader().read_prepared_file(positive_source)
    merged = apply_cad_snapshot(
        imported, snapshot, source_sha256=digest(positive_source)
    )
    native = [
        feature
        for feature in merged.geometry.feature_collection["features"]
        if feature["properties"].get("source_geometry_provider")
        == "autocad_snapshot_v1"
    ]
    observed = sorted(
        (
            feature["properties"]["source_handle"],
            feature["properties"]["source_instance_chain"],
            shape(feature["geometry"]).area,
            len(feature["geometry"]["coordinates"]) - 1,
        )
        for feature in native
    )
    if len(observed) != 2:
        raise ValueError(f"expected two merged native REGION features, got {len(observed)}")
    by_chain_depth = {len(chain): (handle, area, holes) for handle, chain, area, holes in observed}
    root = by_chain_depth.get(0)
    nested = by_chain_depth.get(2)
    if root is None or not math.isclose(root[1], 96.0, abs_tol=1e-9) or root[2] != 0:
        raise ValueError(f"root REGION differs: {root}")
    if nested is None or not math.isclose(nested[1], 900.0, abs_tol=5e-5) or nested[2] != 1:
        raise ValueError(f"affine nested REGION differs: {nested}")

    negatives = load(negative_manifest)["controls"]
    rejected: dict[str, dict[str, Any]] = {}
    for name, control in negatives.items():
        source = Path(control["path"])
        if digest(source) != control["sha256"]:
            raise ValueError(f"{name} control source hash differs")
        probe = load(Path(f"{source}.green-atlas.geometry.json"))
        blocker = control["expected_blocker"]
        count = probe["summary"].get(blocker)
        if not isinstance(count, int) or count <= 0:
            raise ValueError(f"{name} did not expose {blocker}")
        try:
            compile_region_probe(
                probe, autocad_version="2027.0.1", target="macos-arm64"
            )
        except CadSnapshotAdmissionError as error:
            rejected[name] = {"blocker": blocker, "count": count, "reason": str(error)}
        else:
            raise ValueError(f"{name} negative control was admitted")

    return {
        "bridge_version": positive_probe["plugin_version"],
        "positive": {
            "source": str(positive_source.resolve()),
            "source_sha256": digest(positive_source),
            "regions": len(native),
            "affine_nested_area": nested[1],
            "affine_nested_holes": nested[2],
            "snapshot_payload_sha256": snapshot.summary.payload_sha256,
            "incomplete_layers": [
                layer.source_name for layer in merged.layers if not layer.geometry_complete
            ],
        },
        "negative": rejected,
        "passed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("positive_source", type=Path)
    parser.add_argument("negative_manifest", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = verify(arguments.positive_source, arguments.negative_manifest)
    if arguments.output:
        arguments.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
