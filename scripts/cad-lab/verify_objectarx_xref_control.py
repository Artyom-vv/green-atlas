#!/usr/bin/env python3
"""Verify resolved-XREF geometry, dependency bytes and snapshot admission."""

from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.cad_bridge import compile_region_probe
from app.cad_bridge.provider import apply_cad_snapshot
from app.dxf_import.contracts import DxfImportResult
from app.geometry.contracts import GeometrySnapshot


def transformed_point(
    point: list[float], manifest: dict[str, object]
) -> list[float]:
    x, y, z = point
    sx, sy, sz = manifest["scale"]
    angle = math.radians(manifest["rotation_degrees"])
    cosine, sine = math.cos(angle), math.sin(angle)
    tx, ty, tz = manifest["insert"]
    return [
        tx + cosine * sx * x - sine * sy * y,
        ty + sine * sx * x + cosine * sy * y,
        tz + sz * z,
    ]


def distance(left: list[float], right: list[float]) -> float:
    return math.dist(left, right)


def verify(
    manifest_path: Path,
    baseline_path: Path,
    probe_path: Path,
) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text())
    baseline = json.loads(baseline_path.read_text())
    probe = json.loads(probe_path.read_text())
    if probe.get("plugin_version") != "0.1.6":
        raise ValueError("XREF proof requires ObjectARX bridge 0.1.6")
    dependencies = probe.get("xref_dependencies") or []
    if len(dependencies) != 1:
        raise ValueError("expected exactly one resolved XREF dependency")
    dependency = dependencies[0]
    if dependency.get("status") != "resolved":
        raise ValueError("XREF dependency was not resolved")
    if dependency.get("sha256") != manifest["child_sha256"]:
        raise ValueError("native XREF hash differs from the package child")

    insert_handle = manifest["insert_handle"]
    actual_by_key = {
        (item["handle"], tuple(item["instance_chain"])): item
        for item in probe["regions"]
    }
    maximum_coordinate_error = 0.0
    maximum_area_error = 0.0
    maximum_perimeter_error = 0.0
    determinant = abs(manifest["scale"][0] * manifest["scale"][1])
    for source in baseline["regions"]:
        chain = (insert_handle, *source["instance_chain"])
        actual = actual_by_key[(source["handle"], chain)]
        expected_area = source["native_area_units2"] * determinant
        maximum_area_error = max(
            maximum_area_error,
            abs(actual["native_area_units2"] - expected_area),
        )
        expected_perimeter = 0.0
        for source_loop, actual_loop in zip(
            source["loops"], actual["loops"], strict=True
        ):
            if source_loop["role"] != actual_loop["role"]:
                raise ValueError("XREF loop role changed")
            expected_coordinates = [
                transformed_point(point, manifest)
                for point in source_loop["coordinates"]
            ]
            if len(expected_coordinates) != len(actual_loop["coordinates"]):
                raise ValueError("XREF loop point count changed")
            maximum_coordinate_error = max(
                maximum_coordinate_error,
                *(
                    distance(expected, actual_point)
                    for expected, actual_point in zip(
                        expected_coordinates,
                        actual_loop["coordinates"],
                        strict=True,
                    )
                ),
            )
            expected_perimeter += sum(
                distance(left, right)
                for left, right in pairwise(expected_coordinates)
            )
        maximum_perimeter_error = max(
            maximum_perimeter_error,
            abs(actual["native_perimeter_units"] - expected_perimeter),
        )
    if maximum_coordinate_error > 1e-9:
        raise ValueError("XREF coordinates differ from independent transform")
    if maximum_area_error > 1e-9 or maximum_perimeter_error > 1e-9:
        raise ValueError("XREF measurements differ from independent transform")

    package_root = Path(manifest["package_root"])
    snapshot = compile_region_probe(
        probe,
        autocad_version="2027.0.1",
        target="macos-arm64",
        package_root=package_root,
    )
    imported = DxfImportResult(
        layers=[],
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []}
        ),
        dxf_version="AC1032",
        units="м",
        entity_count=1,
    )
    verified_dependencies = {
        item.path: item.sha256 for item in snapshot.dependencies or []
    }
    merged = apply_cad_snapshot(
        imported,
        snapshot,
        source_sha256=probe["source"]["sha256"],
        verified_dependencies=verified_dependencies,
    )
    return {
        "plugin_version": probe["plugin_version"],
        "root_sha256": probe["source"]["sha256"],
        "dependency": snapshot.dependencies[0].model_dump(mode="json"),
        "source_instances": snapshot.summary.source_instances,
        "regions": len(snapshot.geometry),
        "provider_features": len(merged.geometry.feature_collection["features"]),
        "maximum_coordinate_error": maximum_coordinate_error,
        "maximum_area_error": maximum_area_error,
        "maximum_perimeter_error": maximum_perimeter_error,
        "payload_sha256": snapshot.summary.payload_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("probe", type=Path)
    parser.add_argument("receipt", type=Path)
    arguments = parser.parse_args()
    receipt = verify(arguments.manifest, arguments.baseline, arguments.probe)
    arguments.receipt.parent.mkdir(parents=True, exist_ok=True)
    arguments.receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
