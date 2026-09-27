#!/usr/bin/env python3
"""Validate provenance, integrity, and georeferencing of NSPD raster evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / ".runtime/nspd-ortho2000-kustanayskaya-20260920/manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    return struct.unpack(">II", header[16:24])


def main() -> None:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text())
    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: object) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    check(
        "schema",
        manifest.get("schema") == "green-atlas.nspd-orthophoto-evidence.v1",
        manifest.get("schema"),
    )
    world_path = ROOT / manifest["world"]["path"]
    check(
        "world_hash",
        world_path.exists() and sha256(world_path) == manifest["world"]["sha256"],
        manifest["world"]["sha256"],
    )
    x_min, x_max = manifest["mosaic"]["tile_range"]["x"]
    y_min, y_max = manifest["mosaic"]["tile_range"]["y"]
    expected_tile_count = (x_max - x_min + 1) * (y_max - y_min + 1)
    check(
        "tile_grid_complete",
        len(manifest["tiles"]) == expected_tile_count == manifest["mosaic"]["tile_count"],
        {"expected": expected_tile_count, "actual": len(manifest["tiles"])},
    )
    invalid_tiles = []
    tile_indices = set()
    for tile in manifest["tiles"]:
        path = ROOT / tile["path"]
        index = (tile["x"], tile["y"], tile["z"])
        tile_indices.add(index)
        if (
            not path.exists()
            or sha256(path) != tile["sha256"]
            or png_size(path) != (256, 256)
        ):
            invalid_tiles.append(index)
    expected_indices = {
        (x, y, manifest["mosaic"]["zoom"])
        for y in range(y_min, y_max + 1)
        for x in range(x_min, x_max + 1)
    }
    check(
        "tile_hashes_and_dimensions",
        not invalid_tiles and tile_indices == expected_indices,
        {"invalid": invalid_tiles, "missing": sorted(expected_indices - tile_indices)},
    )
    for label in ("full", "cropped"):
        path = ROOT / manifest["mosaic"][f"{label}_path"]
        expected_size = tuple(manifest["mosaic"][f"{label}_pixel_size"])
        check(
            f"{label}_mosaic",
            path.exists()
            and sha256(path) == manifest["mosaic"][f"{label}_sha256"]
            and png_size(path) == expected_size,
            {"path": str(path), "expected_size": expected_size},
        )
    requested = manifest["world"]["requested_bbox_wgs84"]
    actual = manifest["mosaic"]["bbox_wgs84"]
    contains = (
        actual[0] <= requested[0]
        and actual[1] <= requested[1]
        and actual[2] >= requested[2]
        and actual[3] >= requested[3]
    )
    check("bbox_coverage", contains, {"requested": requested, "actual": actual})
    all_source_text = json.dumps(manifest, ensure_ascii=False).lower()
    check("no_dxf_dependency", ".dxf" not in all_source_text and ".dwg" not in all_source_text, None)
    check(
        "truth_gate_stays_closed",
        manifest["admission"]["render_base"] == "evidence_candidate_pending_visual_review",
        manifest["admission"],
    )
    result = {
        "schema": "green-atlas.nspd-orthophoto-validation.v1",
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
    output = args.manifest.parent / "validation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
