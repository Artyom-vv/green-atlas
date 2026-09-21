"""Prove the bounded AOI path on an immutable DXF and authored boundary.

This diagnostic deliberately avoids the eager full-DXF -> GeoJSON import.  It
uses the same resource-bounded AOI worker as CAD intake, then checks whether the
resulting fragment can enter the ordinary DXF reader.  It does not change a
project and does not claim calculation readiness before semantic mapping is
reviewed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.cad_import.aoi import prepare_aoi
from app.cad_import.aoi_contracts import AoiRequest, AoiSource
from app.cad_import.cache import file_sha256
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.layer_contracts import BoundaryCandidateStatus, LayerKind


def probe_source(source: Path, boundary_handle: str, output_root: Path) -> dict[str, Any]:
    source = source.resolve(strict=True)
    output_root = output_root.resolve()
    digest = file_sha256(source)
    immutable_source = AoiSource(
        original_path=source,
        original_sha256=digest,
        converted_path=source,
        converted_sha256=digest,
    )
    prepared = prepare_aoi(
        AoiRequest(
            source=immutable_source,
            boundary_source=immutable_source,
            boundary_handle=boundary_handle,
        ),
        output_root,
    )

    reader: dict[str, Any]
    try:
        imported = EzdxfReader().read(
            prepared.drawing_path.name, prepared.drawing_path.read_bytes()
        )
        active = [
            layer
            for layer in imported.layers
            if layer.mapped_kind not in {None, LayerKind.IGNORE}
        ]
        unconfirmed = [
            layer
            for layer in active
            if layer.mapping_review_required and not layer.mapping_confirmed
        ]
        incomplete = [layer for layer in active if not layer.geometry_complete]
        usable_boundaries = [
            layer
            for layer in imported.layers
            if layer.boundary_candidate is not None
            and layer.boundary_candidate.status == BoundaryCandidateStatus.USABLE
        ]
        selected_boundaries = [
            layer for layer in active if layer.mapped_kind == LayerKind.SITE_BORDER
        ]
        calculation_gate = (
            "choose_boundary"
            if usable_boundaries and not selected_boundaries
            else "confirm_mapping"
            if unconfirmed
            else "incomplete_geometry"
            if incomplete
            else "ready"
        )
        reader = {
            "status": "readable",
            "entities": imported.entity_count,
            "features": len(imported.geometry.feature_collection.get("features", [])),
            "layers": len(imported.layers),
            "bounds": imported.bounds,
            "warnings": imported.warnings,
            "active_layers": len(active),
            "unconfirmed_active_layers": len(unconfirmed),
            "incomplete_active_layers": len(incomplete),
            "usable_boundary_candidates": len(usable_boundaries),
            "selected_boundaries": len(selected_boundaries),
            "calculation_gate": calculation_gate,
            "review_layers": [
                {
                    "source_name": layer.source_name,
                    "suggested_kind": layer.suggested_kind.value,
                    "mapped_kind": (
                        layer.mapped_kind.value
                        if layer.mapped_kind is not None
                        else None
                    ),
                    "geometry_complete": layer.geometry_complete,
                    "mapping_confirmed": layer.mapping_confirmed,
                }
                for layer in sorted(
                    {layer.id: layer for layer in [*unconfirmed, *incomplete]}.values(),
                    key=lambda item: item.source_name.casefold(),
                )
            ],
        }
    except Exception as error:  # noqa: BLE001 - diagnostic must retain exact failure
        reader = {
            "status": "failed",
            "error": type(error).__name__,
            "message": str(error),
        }

    manifest = prepared.manifest
    return {
        "schema_version": "green-atlas-aoi-probe-v1",
        "scope": "bounded AOI extraction and ordinary reader admission; no project writes",
        "source": {
            "path": str(source),
            "bytes": source.stat().st_size,
            "sha256": digest,
        },
        "boundary_handle": boundary_handle.upper(),
        "prepared": {
            "drawing_path": str(prepared.drawing_path),
            "manifest_path": str(prepared.manifest_path),
            "bytes": manifest.output_bytes,
            "sha256": manifest.output_sha256,
            "elapsed_seconds": prepared.elapsed_seconds,
            "peak_memory_bytes": prepared.peak_memory_bytes,
            "status": manifest.status,
            "calculation_ready": manifest.calculation_ready,
            "selected_entities": manifest.selected_modelspace_entities,
            "excluded_by_bounds": manifest.excluded_by_bounds,
            "unknown_bounds": manifest.unknown_bounds,
            "selection_metrics": manifest.selection_metrics,
            "stage_seconds": manifest.stage_seconds,
            "stage_rss_bytes": manifest.stage_rss_bytes,
            "warning_count": len(manifest.warnings),
            "warnings": manifest.warnings[:50],
        },
        "ordinary_reader": reader,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("boundary_handle")
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = probe_source(args.source, args.boundary_handle, args.output_root)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if report["ordinary_reader"]["status"] == "readable" else 1


if __name__ == "__main__":
    raise SystemExit(main())
