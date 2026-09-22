"""Stream-filter Microsoft Road Detections to a small site evidence packet.

The Eastern Europe release is a single 1.4 GB deflated TSV member.  This tool
never stores that archive: it incrementally inflates the first ZIP member and
retains only road features intersecting the requested WGS84 bounding box.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import time
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "https://usaminedroads.z19.web.core.windows.net/drops/2025.04.28/Eastern_Europe.zip"
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
DEFAULT_OUTPUT = ROOT / ".runtime/microsoft-road-evidence-20260920"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_feature_id(feature: dict[str, Any]) -> str:
    encoded = json.dumps(feature["geometry"], sort_keys=True, separators=(",", ":")).encode()
    return "microsoft-road:" + hashlib.sha256(encoded).hexdigest()[:24]


def geometry_bounds(coordinates: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [float(point[0]) for point in coordinates]
    ys = [float(point[1]) for point in coordinates]
    return min(xs), min(ys), max(xs), max(ys)


def intersects(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def iter_zip_member_lines(response: Any, chunk_size: int) -> Iterator[tuple[int, bytes]]:
    header = response.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise RuntimeError("Response is not a ZIP local file header")
    _, _, flags, compression, _, _, _, _, _, name_length, extra_length = struct.unpack(
        "<4s5H3L2H", header
    )
    if flags & 0x1:
        raise RuntimeError("Encrypted ZIP members are unsupported")
    if compression != 8:
        raise RuntimeError(f"Expected deflate compression, got {compression}")
    member_name = response.read(name_length).decode("utf-8")
    response.read(extra_length)
    if member_name != "Eastern_Europe.tsv":
        raise RuntimeError(f"Unexpected ZIP member: {member_name}")

    decompressor = zlib.decompressobj(-15)
    pending = b""
    compressed_bytes = 30 + name_length + extra_length
    while not decompressor.eof:
        chunk = response.read(chunk_size)
        if not chunk:
            raise RuntimeError("Archive ended before the deflate member completed")
        compressed_bytes += len(chunk)
        raw = decompressor.decompress(chunk)
        if not raw:
            continue
        pending += raw
        lines = pending.split(b"\n")
        pending = lines.pop()
        for line in lines:
            yield compressed_bytes, line.rstrip(b"\r")
    if pending:
        yield compressed_bytes, pending.rstrip(b"\r")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--country", default="RUS")
    parser.add_argument("--chunk-mib", type=int, default=2)
    parser.add_argument("--progress-mib", type=int, default=64)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    world = json.loads(args.world.read_text())
    scope = tuple(float(value) for value in world["scope"]["context_bbox_wgs84"])
    request = urllib.request.Request(args.url, headers={"User-Agent": "GreenAtlas/1.0 source audit"})
    started = time.monotonic()
    total_rows = country_rows = rejected_country = malformed_rows = 0
    retained: dict[str, dict[str, Any]] = {}
    last_progress = 0
    with urllib.request.urlopen(request, timeout=120) as response:
        content_length = int(response.headers.get("Content-Length") or 0)
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")
        for compressed_bytes, line in iter_zip_member_lines(response, args.chunk_mib * 1024 * 1024):
            total_rows += 1
            if not line.startswith(args.country.encode() + b"\t"):
                rejected_country += 1
            else:
                country_rows += 1
                try:
                    feature = json.loads(line.split(b"\t", 1)[1])
                    geometry = feature.get("geometry") or {}
                    if geometry.get("type") != "LineString":
                        continue
                    coordinates = geometry.get("coordinates") or []
                    if len(coordinates) < 2 or not intersects(geometry_bounds(coordinates), scope):
                        continue
                    feature_id = canonical_feature_id(feature)
                    properties = feature.setdefault("properties", {})
                    raw_width = properties.get("WidthMeters")
                    properties.update(
                        {
                            "evidence_id": feature_id,
                            "width_m": float(raw_width) if raw_width is not None else None,
                            "geometry_status": "satellite_extracted_independent_candidate",
                            "width_status": "satellite_extracted_approximate",
                            "render_admission": "blocked_pending_osm_conflation_and_review",
                        }
                    )
                    retained[feature_id] = feature
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    malformed_rows += 1

            progress_step = args.progress_mib * 1024 * 1024
            if compressed_bytes - last_progress >= progress_step:
                last_progress = compressed_bytes
                elapsed = max(time.monotonic() - started, 0.001)
                percent = compressed_bytes / content_length * 100 if content_length else 0.0
                print(
                    f"progress compressed={compressed_bytes / 1024**2:.1f}MiB "
                    f"percent={percent:.1f} rows={total_rows} rus={country_rows} "
                    f"retained={len(retained)} speed={compressed_bytes / 1024**2 / elapsed:.2f}MiB/s",
                    file=sys.stderr,
                    flush=True,
                )

    features = sorted(retained.values(), key=lambda row: row["properties"]["evidence_id"])
    collection = {
        "type": "FeatureCollection",
        "name": "Microsoft Road Detections candidates around Kustanayskaya",
        "features": features,
        "metadata": {
            "status": "evidence_only_not_render_admitted",
            "scope_bbox_wgs84": list(scope),
            "upstream": args.url,
            "release": "2025-04-28",
            "license": "Open Data Commons Open Database License (ODbL)",
            "source_method": "Microsoft satellite-image semantic segmentation and geometry generation",
            "limitations": [
                "WidthMeters is approximate and requires comparison with OSM/Overture and imagery.",
                "The exact image acquisition date varies and is not published per feature.",
                "Candidate geometry is not survey-grade and is not admitted directly to the render.",
            ],
        },
    }
    geojson_path = args.output / "roads.geojson"
    geojson_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    widths = sorted(
        float(row["properties"]["width_m"])
        for row in features
        if row["properties"].get("width_m") is not None
    )
    receipt = {
        "schema": "green-atlas.microsoft-road-evidence-receipt.v1",
        "status": "evidence_only_not_render_admitted",
        "source": {
            "url": args.url,
            "content_length": content_length,
            "etag": etag,
            "last_modified": last_modified,
            "release": "2025-04-28",
            "license": "ODbL",
        },
        "world": {"path": str(args.world.resolve()), "sha256": sha256(args.world)},
        "output": {"path": str(geojson_path.resolve()), "sha256": sha256(geojson_path)},
        "scope_bbox_wgs84": list(scope),
        "counts": {
            "archive_rows": total_rows,
            "country_rows": country_rows,
            "other_country_rows": rejected_country,
            "malformed_country_rows": malformed_rows,
            "retained_features": len(features),
        },
        "width_m": {
            "minimum": round(widths[0], 6) if widths else None,
            "median": round(widths[len(widths) // 2], 6) if widths else None,
            "maximum": round(widths[-1], 6) if widths else None,
        },
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    receipt_path = args.output / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
