#!/usr/bin/env python3
"""Derive fail-closed ObjectARX controls from an AutoCAD-built REGION DXF."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import ezdxf


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(source: Path, output: Path) -> dict[str, object]:
    before = digest(source)
    output.mkdir(parents=True, exist_ok=True)

    xref_path = output / "missing-xref-negative.dxf"
    xref = ezdxf.new("R2018")
    xref.units = 6
    missing_target = output / "intentionally-absent-reference.dxf"
    xref.add_xref_def(str(missing_target.resolve()), "GA_MISSING_XREF")
    xref.modelspace().add_blockref("GA_MISSING_XREF", (0, 0))
    xref.saveas(xref_path)

    if digest(source) != before:
        raise RuntimeError("positive source changed while deriving controls")
    result = {
        "source": str(source.resolve()),
        "source_sha256": before,
        "controls": {
            "missing_xref": {
                "path": str(xref_path.resolve()),
                "sha256": digest(xref_path),
                "expected_blocker": "unresolved_xref_block_references",
                "missing_target": str(missing_target.resolve()),
            },
        },
    }
    (output / "negative-controls.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.source, arguments.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
