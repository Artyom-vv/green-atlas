#!/usr/bin/env python3
"""Run the portable reader plus admitted native snapshot on a complete DXF."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.cad_bridge import compile_region_probe
from app.cad_bridge.provider import apply_cad_snapshot
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.capacity import SourceGeometryCapacity


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(
    source: Path,
    probe_path: Path,
    snapshot_path: Path,
    package_root: Path | None,
) -> dict[str, object]:
    source_sha256 = digest(source)
    probe = json.loads(probe_path.read_text(encoding="utf-8"))
    if probe["source"]["sha256"] != source_sha256:
        raise ValueError("native probe belongs to a different full DXF")
    snapshot = compile_region_probe(
        probe,
        autocad_version="2027.0.1",
        target="macos-arm64",
        package_root=package_root,
    )
    snapshot_path.write_text(
        snapshot.model_dump_json(by_alias=True, exclude_none=True), encoding="utf-8"
    )
    started = perf_counter()
    imported = EzdxfReader(
        capacity=SourceGeometryCapacity.process_bounded()
    ).read_prepared_file(source)
    reader_seconds = perf_counter() - started
    before = len(imported.geometry.feature_collection["features"])
    started = perf_counter()
    verified_dependencies = {
        item.path: item.sha256 for item in snapshot.dependencies or []
    }
    merged = apply_cad_snapshot(
        imported,
        snapshot,
        source_sha256=source_sha256,
        capacity=SourceGeometryCapacity.process_bounded(),
        verified_dependencies=verified_dependencies or None,
    )
    provider_seconds = perf_counter() - started
    after = len(merged.geometry.feature_collection["features"])
    native_features = [
        feature
        for feature in merged.geometry.feature_collection["features"]
        if feature.get("properties", {}).get("source_geometry_provider")
        == "autocad_snapshot_v1"
    ]
    region_layers = {
        record.layer
        for record in snapshot.coverage
        if record.entity_type == "AcDbRegion"
    }
    incomplete_region_layers = [
        layer.source_name
        for layer in merged.layers
        if layer.source_name in region_layers and not layer.geometry_complete
    ]
    if len(native_features) != len(snapshot.geometry):
        raise ValueError("provider did not merge every native geometry")
    if incomplete_region_layers:
        raise ValueError("provider left REGION-bearing layers incomplete")
    return {
        "source": str(source.resolve()),
        "source_sha256": source_sha256,
        "plugin_version": snapshot.extraction.plugin_version,
        "source_instances": snapshot.summary.source_instances,
        "native_regions": len(snapshot.geometry),
        "features_before": before,
        "features_after": after,
        "reader_seconds": reader_seconds,
        "provider_seconds": provider_seconds,
        "region_layers": sorted(region_layers),
        "incomplete_region_layers": incomplete_region_layers,
        "dependencies": [
            item.model_dump(mode="json") for item in snapshot.dependencies or []
        ],
        "payload_sha256": snapshot.summary.payload_sha256,
        "passed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("probe", type=Path)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = verify(
        arguments.source,
        arguments.probe,
        arguments.snapshot,
        arguments.package_root,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if arguments.output:
        arguments.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
