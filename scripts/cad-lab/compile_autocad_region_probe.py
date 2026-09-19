#!/usr/bin/env python3
"""Compile an admitted ObjectARX REGION probe into snapshot-v1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from app.cad_bridge import compile_region_probe


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--autocad-version", required=True)
    parser.add_argument(
        "--target", choices=("macos-arm64", "windows-x86_64"), required=True
    )
    parser.add_argument(
        "--package-root",
        type=Path,
        help="Required when the native probe traversed resolved XREF files",
    )
    arguments = parser.parse_args()
    probe = json.loads(arguments.probe.read_text())
    output = arguments.output
    if output is None:
        source_path = Path(probe.get("source", {}).get("path", ""))
        if not source_path.name:
            parser.error("probe has no source path; provide output explicitly")
        output = source_path.with_name(
            f"{source_path.name}.green-atlas.snapshot.json"
        )
    snapshot = compile_region_probe(
        probe,
        autocad_version=arguments.autocad_version,
        target=arguments.target,
        package_root=arguments.package_root,
    )
    payload = snapshot.model_dump(by_alias=True, mode="json", exclude_none=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    temporary.replace(output)
    print(
        json.dumps(
            {
                "output": str(output),
                "source_instances": snapshot.summary.source_instances,
                "native": snapshot.summary.native,
                "unresolved": snapshot.summary.unresolved,
                "payload_sha256": snapshot.summary.payload_sha256,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
