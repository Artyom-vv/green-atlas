#!/usr/bin/env python3
"""Build a package-local resolved-XREF control from native REGION evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import ezdxf
from ezdxf import xref


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(source: Path, output: Path) -> dict[str, object]:
    source = source.resolve(strict=True)
    source_sha256 = digest(source)
    output.mkdir(parents=True, exist_ok=True)
    references = output / "references"
    references.mkdir(exist_ok=True)
    child = references / f"native-region-child{source.suffix.lower()}"
    shutil.copyfile(source, child)

    document = ezdxf.new("R2018")
    document.units = 6
    insert = xref.attach(
        document,
        block_name="GA_NATIVE_REGION_CHILD",
        filename=child.relative_to(output).as_posix(),
        insert=(300.0, 200.0, 0.0),
        rotation=20.0,
    )
    insert.dxf.xscale = 1.5
    insert.dxf.yscale = 0.75
    root = output / "resolved-xref-positive.dxf"
    document.saveas(root)
    if digest(source) != source_sha256:
        raise RuntimeError("native source changed while deriving XREF control")

    manifest = {
        "source": str(source),
        "source_sha256": source_sha256,
        "package_root": str(output.resolve()),
        "root": str(root.resolve()),
        "root_sha256": digest(root),
        "child": str(child.resolve()),
        "child_path": child.relative_to(output).as_posix(),
        "child_sha256": digest(child),
        "block_name": "GA_NATIVE_REGION_CHILD",
        "insert_handle": insert.dxf.handle,
        "insert": [300.0, 200.0, 0.0],
        "scale": [1.5, 0.75, 1.0],
        "rotation_degrees": 20.0,
    }
    manifest_path = output / "resolved-xref-positive.manifest.json"
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
