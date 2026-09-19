#!/usr/bin/env python3
"""Derive a rotated, non-uniformly scaled MINSERT control from a native source."""

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
    source_sha256 = digest(source)
    document = ezdxf.readfile(source)
    inserts = list(document.modelspace().query("INSERT"))
    if len(inserts) != 1:
        raise ValueError("control source must contain exactly one model-space INSERT")
    insert = inserts[0]
    insert.dxf.row_count = 2
    insert.dxf.column_count = 2
    insert.dxf.row_spacing = 75.0
    insert.dxf.column_spacing = 60.0
    output.parent.mkdir(parents=True, exist_ok=True)
    document.saveas(output)
    if digest(source) != source_sha256:
        raise RuntimeError("source changed while deriving MINSERT control")

    reopened = ezdxf.readfile(output)
    stored = next(iter(reopened.modelspace().query("INSERT")))
    virtual = list(stored.multi_insert())
    if len(virtual) != 4:
        raise ValueError(f"expected four MINSERT cells, got {len(virtual)}")
    ocs = stored.ocs()
    origin = ocs.to_wcs(virtual[0].dxf.insert)
    offsets = []
    for row in range(stored.dxf.row_count):
        for column in range(stored.dxf.column_count):
            cell = virtual[row * stored.dxf.column_count + column]
            position = ocs.to_wcs(cell.dxf.insert)
            offset = position - origin
            offsets.append(
                {
                    "row": row,
                    "column": column,
                    "token": f"MINSERT:{stored.dxf.handle}:R{row}:C{column}",
                    "offset_wcs": [offset.x, offset.y, offset.z],
                }
            )
    manifest = {
        "source": str(source.resolve()),
        "source_sha256": source_sha256,
        "control": str(output.resolve()),
        "control_sha256": digest(output),
        "minsert_handle": stored.dxf.handle,
        "rows": stored.dxf.row_count,
        "columns": stored.dxf.column_count,
        "row_spacing": stored.dxf.row_spacing,
        "column_spacing": stored.dxf.column_spacing,
        "cells": offsets,
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.source, arguments.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
