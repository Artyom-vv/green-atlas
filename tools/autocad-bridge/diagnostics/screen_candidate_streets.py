"""Run the existing AutoCAD bridge on selected real DWGs, without opening UI drawings.

The native bridge writes adjacent evidence sidecars, never modifies source DWGs.
This is a screening pass, not full-street or planting acceptance.
"""

import argparse
import hashlib
import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BRIDGE = ROOT / "tools/autocad-bridge/mcp/green_atlas_autocad_mcp.py"


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument(
        "--candidate",
        nargs=2,
        action="append",
        required=True,
        metavar=("STREET_PREFIX", "DWG_FILENAME"),
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    specification = importlib.util.spec_from_file_location("green_atlas_bridge", BRIDGE)
    if specification is None or specification.loader is None:
        raise RuntimeError("Bridge module is unavailable")
    bridge = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(bridge)
    status = bridge.bridge_status()
    if not status.get("ready"):
        raise RuntimeError(f"Installed AutoCAD bridge is not ready: {status}")
    report = {
        "schema": "green-atlas.candidate-streets-native-screen/1",
        "scope": "existing AutoCAD ObjectARX export and admission; not project/planting acceptance",
        "bridge_status": status,
        "candidates": [],
    }
    for prefix, filename in args.candidate:
        streets = [
            p
            for p in args.data_root.iterdir()
            if p.is_dir() and p.name.startswith(prefix + " ")
        ]
        if len(streets) != 1:
            raise ValueError(f"Expected one street matching {prefix}: {streets}")
        street = streets[0]
        matches = (
            [street / filename] if "/" in filename else list(street.rglob(filename))
        )
        matches = [path for path in matches if path.is_file()]
        if len(matches) != 1:
            raise ValueError(f"Expected one DWG named {filename}: {matches}")
        source = matches[0]
        before = digest(source)
        row = {
            "street": street.name,
            "source": str(source.resolve()),
            "source_sha256": before,
            "source_bytes": source.stat().st_size,
        }
        if Path(str(source) + ".green-atlas.geometry.json").exists():
            row["status"] = "skipped_existing_sidecar"
            report["candidates"].append(row)
            continue
        print(f"START {street.name}: {filename}", flush=True)
        started = time.monotonic()
        try:
            result = bridge.prepare_dxf(
                {
                    "source_path": str(source.resolve()),
                    "package_root": str(street.resolve()),
                    "timeout_seconds": 180,
                }
            )
            row.update(
                {
                    "status": "admitted",
                    "snapshot_path": result["snapshot_path"],
                    "snapshot_sha256": result["snapshot_sha256"],
                    "native_evidence": result["native_evidence"],
                    "native_summary": result["native_probe"]["summary"],
                    "admission": result["admission"],
                }
            )
        except Exception as error:  # noqa: BLE001 - diagnostic records native failures
            row.update(
                {
                    "status": "failed",
                    "error": str(error),
                    "error_details": getattr(error, "details", None),
                }
            )
        row["elapsed_seconds"] = round(time.monotonic() - started, 2)
        row["source_unchanged"] = digest(source) == before
        report["candidates"].append(row)
        print(
            json.dumps(
                {
                    k: v
                    for k, v in row.items()
                    if k not in {"native_evidence", "admission", "error_details"}
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    with args.output.open("x") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
