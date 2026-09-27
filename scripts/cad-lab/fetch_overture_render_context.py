"""Fetch version-pinned Overture context for an already georeferenced AOI.

This tool never reads CAD files. A caller must provide a verified WGS84 bbox
from the single AutoCAD capture/alignment path. The downloaded data is context,
not a replacement for authored surfaces, plantings or ground controls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path


KINDS = ("building", "building_part", "segment")


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    values = tuple(float(part) for part in raw.split(","))
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        raise ValueError("bbox must contain four finite WGS84 coordinates")
    west, south, east, north = values
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox is not a valid WGS84 west,south,east,north extent")
    if east - west > 0.1 or north - south > 0.1:
        raise ValueError("bbox exceeds 0.1 degrees; split large areas explicitly")
    return values


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(*, bbox: tuple[float, float, float, float], release: str,
                   output: Path, paths: dict[str, Path], methods: dict[str, str]) -> dict:
    entries = {}
    for kind in KINDS:
        path = paths[kind]
        data = json.loads(path.read_text())
        if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
            raise ValueError(f"Unexpected Overture GeoJSON: {path}")
        features = data["features"]
        properties = [feature.get("properties") or {} for feature in features]
        counts = {}
        fields = (("height", "num_floors", "facade_color", "facade_material", "roof_shape")
                  if kind in {"building", "building_part"} else
                  ("width_rules", "road_surface", "names") if kind == "segment" else ())
        for field in fields:
            counts[field] = sum(value.get(field) is not None and value.get(field) != []
                                for value in properties)
        entries[kind] = {"path": path.name, "sha256": sha256(path),
                         "feature_count": len(data["features"]),
                         "attribute_counts": counts,
                         "query_method": methods[kind],
                         "coverage_status": "candidate_features" if features else "empty_response_not_absence_proof"}
    return {"schema": "green-atlas.overture-render-context.v1",
            "source": "Overture Maps", "release": release, "bbox_wgs84": list(bbox),
            "status": "unmatched_external_context", "features": entries,
            "usage": "candidate existing context; not surveyed CAD or approved scene objects"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True, help="verified WGS84 west,south,east,north")
    parser.add_argument("--release", required=True, help="pinned Overture release, e.g. 2026-08-19.0")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        bbox = parse_bbox(args.bbox)
    except ValueError as error:
        parser.error(str(error))
    if not args.release.strip() or args.release.lower() == "latest":
        parser.error("Pin a concrete Overture release; latest is not reproducible")
    args.output.mkdir(parents=True, exist_ok=True)
    bbox_arg = ",".join(str(value) for value in bbox)
    temporary_paths = {kind: args.output / f"{kind}.download.geojson" for kind in KINDS}
    published_paths: dict[str, Path] = {}
    methods: dict[str, str] = {}
    for path in temporary_paths.values():
        path.unlink(missing_ok=True)
    try:
        for kind in KINDS:
            temporary = temporary_paths[kind]
            command = [sys.executable, "-m", "overturemaps", "download",
                       "--bbox", bbox_arg, "--release", args.release,
                       "-f", "geojson", "--type", kind, "-o", str(temporary)]
            subprocess.run(command, check=True)
            methods[kind] = "stac"
            if not temporary.exists():
                # In one verified Moscow bbox the STAC filter returned zero
                # for a pinned release while the full Parquet scan found rows.
                # An absent file therefore requires a second, slower query.
                subprocess.run(command[:-2] + ["--no-stac"] + command[-2:], check=True)
                methods[kind] = "no_stac_retry"
            # The client exits successfully without a file for a truly empty
            # bbox even after the unfiltered scan.
            if not temporary.exists():
                temporary.write_text('{"type":"FeatureCollection","features":[]}\n')
            data = json.loads(temporary.read_text())
            if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
                raise ValueError(f"Unexpected Overture GeoJSON for {kind}")
        for kind, temporary in temporary_paths.items():
            published = args.output / f"{kind}-{sha256(temporary)[:16]}.geojson"
            if published.exists():
                if sha256(published) != sha256(temporary):
                    raise ValueError(f"Published content hash mismatch: {published}")
            else:
                temporary.replace(published)
            published_paths[kind] = published
    finally:
        for path in temporary_paths.values():
            path.unlink(missing_ok=True)
            Path(str(path) + ".state").unlink(missing_ok=True)
    manifest = write_manifest(bbox=bbox, release=args.release, output=args.output,
                              paths=published_paths, methods=methods)
    path = args.output / "context-manifest.json"
    temporary_manifest = args.output / "context-manifest.download.json"
    temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    temporary_manifest.replace(path)
    print(json.dumps({"manifest": str(path), "features": manifest["features"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
