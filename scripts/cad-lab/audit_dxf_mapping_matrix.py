"""Audit DXF reader coverage and automatic mapping without changing projects.

Each source is read independently. The report distinguishes technical geometry
coverage from semantic mapping confidence; a readable file is never presented
as calculation-ready while a critical role still needs operator review.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.layer_contracts import LayerKind
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES
from app.geometry.geojson_size import coordinate_count


def audit_source(source: Path) -> dict[str, Any]:
    content = source.read_bytes()
    started = time.monotonic()
    result: dict[str, Any] = {
        "source": str(source.resolve()),
        "bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "ordinary_upload_bytes_allowed": len(content) <= MAX_DXF_CONTENT_BYTES,
        "mapping_verified": False,
        "calculation_executed": False,
    }
    try:
        imported = EzdxfReader().read(source.name, content)
    except Exception as error:  # noqa: BLE001 - the audit records reader failures
        result.update(
            status="reader_failed",
            elapsed_seconds=time.monotonic() - started,
            error=type(error).__name__,
            message=str(error),
        )
        return result

    features = imported.geometry.feature_collection.get("features", [])
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
        and layer.boundary_candidate.status == "usable"
    ]
    selected_boundaries = [
        layer for layer in active if layer.mapped_kind == LayerKind.SITE_BORDER
    ]
    review_layers = sorted(
        {
            layer.id: {
                "layer_id": layer.id,
                "source_name": layer.source_name,
                "suggested_kind": layer.suggested_kind.value,
                "mapped_kind": layer.mapped_kind.value
                if layer.mapped_kind is not None
                else None,
                "confidence": layer.suggestion_confidence.value
                if layer.suggestion_confidence is not None
                else None,
                "reasons": layer.suggestion_reasons,
                "review_required": bool(layer.mapping_review_required),
                "confirmed": bool(layer.mapping_confirmed),
                "geometry_complete": layer.geometry_complete,
                "object_count": layer.object_count,
                "entity_types": layer.entity_types,
                "boundary_status": layer.boundary_candidate.status.value
                if layer.boundary_candidate is not None
                else None,
            }
            for layer in [*unconfirmed, *incomplete, *usable_boundaries]
        }.values(),
        key=lambda row: (row["source_name"].casefold(), row["layer_id"]),
    )
    calculation_gate = (
        "choose_boundary"
        if usable_boundaries and not selected_boundaries
        else "confirm_mapping"
        if unconfirmed
        else "incomplete_geometry"
        if incomplete
        else "ready"
    )
    result.update(
        status="readable",
        elapsed_seconds=time.monotonic() - started,
        dxf_version=imported.dxf_version,
        units=imported.units,
        units_assumed=imported.units_assumed,
        entities=imported.entity_count,
        features=len(features),
        coordinates=sum(
            coordinate_count(feature["geometry"])
            for feature in features
            if feature.get("geometry") is not None
        ),
        bounds=imported.bounds,
        warnings=imported.warnings,
        layer_count=len(imported.layers),
        active_layer_count=len(active),
        incomplete_active_layer_count=len(incomplete),
        unconfirmed_active_layer_count=len(unconfirmed),
        usable_boundary_candidate_count=len(usable_boundaries),
        selected_boundary_count=len(selected_boundaries),
        proposed_roles=dict(
            sorted(Counter(layer.suggested_kind.value for layer in imported.layers).items())
        ),
        confidence=dict(
            sorted(
                Counter(
                    layer.suggestion_confidence.value
                    if layer.suggestion_confidence is not None
                    else "legacy_unknown"
                    for layer in imported.layers
                ).items()
            )
        ),
        calculation_gate=calculation_gate,
        review_layers=review_layers,
    )
    return result


def discover(sources: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for source in sources:
        if source.is_file() and source.suffix.casefold() == ".dxf":
            paths.append(source)
        elif source.is_dir():
            paths.extend(source.rglob("*.dxf"))
    return sorted({path.resolve() for path in paths}, key=lambda path: str(path).casefold())


def run_matrix(
    sources: list[Path], *, timeout_seconds: float, max_files: int | None
) -> dict[str, Any]:
    files = discover(sources)
    if max_files is not None:
        files = files[:max_files]
    rows: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for index, source in enumerate(files, 1):
        digest = sha256(source.read_bytes()).hexdigest()
        if digest in seen:
            rows.append(
                {
                    "source": str(source),
                    "sha256": digest,
                    "status": "duplicate",
                    "duplicate_of": seen[digest],
                }
            )
            continue
        seen[digest] = str(source)
        try:
            process = subprocess.run(
                [sys.executable, str(Path(__file__)), "--worker", str(source)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf8",
                timeout=timeout_seconds,
                check=False,
            )
            row = json.loads(process.stdout)
            if process.returncode != 0:
                row.setdefault("status", "worker_failed")
                row["worker_exit_code"] = process.returncode
                row["worker_stderr"] = process.stderr[-4000:]
        except subprocess.TimeoutExpired:
            row = {
                "source": str(source),
                "sha256": digest,
                "status": "timeout",
                "timeout_seconds": timeout_seconds,
            }
        rows.append(row)
        print(
            json.dumps(
                {
                    "done": index,
                    "total": len(files),
                    "file": source.name,
                    "status": row["status"],
                    "gate": row.get("calculation_gate"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return {
        "scope": "DXF reader and draft semantic mapping; no project writes or calculation",
        "source_count": len(files),
        "unique_content_count": len(seen),
        "statuses": dict(sorted(Counter(row["status"] for row in rows).items())),
        "calculation_gates": dict(
            sorted(
                Counter(
                    row["calculation_gate"]
                    for row in rows
                    if row.get("calculation_gate")
                ).items()
            )
        ),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--worker", type=Path)
    args = parser.parse_args()
    if args.worker is not None:
        print(json.dumps(audit_source(args.worker), ensure_ascii=False))
        return 0
    if not args.sources or args.output is None:
        parser.error("sources and --output are required")
    if args.output.exists():
        raise FileExistsError(args.output)
    result = run_matrix(
        args.sources,
        timeout_seconds=args.timeout_seconds,
        max_files=args.max_files,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
