#!/usr/bin/env python3
"""Download a georeferenced NSPD orthophoto mosaic for a map-first world.

The script uses the public URL pattern emitted by the current NSPD web client.
It deliberately requires a geographic bbox from the compiled world and does not
read CAD/DXF geometry.  Every source tile is retained and hashed so later scene
geometry can point back to exact raster evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import ssl
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
DEFAULT_OUTPUT = ROOT / ".runtime/nspd-ortho2000-kustanayskaya-20260920"
DEFAULT_LAYER_ID = 36344
DEFAULT_ZOOM = 18
TILE_SIZE = 256
WEB_MERCATOR_HALF_WORLD_M = 20_037_508.342789244
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--layer-id", type=int, default=DEFAULT_LAYER_ID)
    parser.add_argument("--zoom", type=int, default=DEFAULT_ZOOM)
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    scale = 2**zoom
    x = (lon + 180.0) / 360.0 * scale
    latitude = math.radians(max(-85.05112878, min(85.05112878, lat)))
    y = (1.0 - math.asinh(math.tan(latitude)) / math.pi) / 2.0 * scale
    return x, y


def tile_to_lonlat(x: float, y: float, zoom: int) -> tuple[float, float]:
    scale = 2**zoom
    lon = x / scale * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / scale))))
    return lon, lat


def fetch_tile(url: str, target: Path) -> tuple[int, str | None]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Referer": "https://nspd.gov.ru/map?thematic=PKK",
            "Origin": "https://nspd.gov.ru",
        },
    )
    # nspd.gov.ru currently serves a certificate chain that macOS/Python cannot
    # validate locally.  This is recorded in the manifest rather than hidden.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        payload = response.read()
        content_type = response.headers.get("Content-Type")
        status = response.status
    if status != 200 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError(f"NSPD tile is not a PNG: status={status}, url={url}")
    target.write_bytes(payload)
    return status, content_type


def main() -> None:
    args = parse_args()
    world = json.loads(args.world.read_text())
    bbox = world["scope"]["context_bbox_wgs84"]
    west, south, east, north = map(float, bbox)
    tile_west, tile_north = lonlat_to_tile(west, north, args.zoom)
    tile_east, tile_south = lonlat_to_tile(east, south, args.zoom)
    x_min, x_max = math.floor(tile_west), math.floor(tile_east)
    y_min, y_max = math.floor(tile_north), math.floor(tile_south)

    args.output.mkdir(parents=True, exist_ok=True)
    tile_dir = args.output / "tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)

    source_tiles: list[dict[str, object]] = []
    tile_paths: list[Path] = []
    for y in range(y_min, y_max + 1):
        for x in range(x_min, x_max + 1):
            url = (
                f"https://nspd.gov.ru/api/aeggis/v4/{args.layer_id}/"
                f"wmts/{args.zoom}/{x}/{y}.png"
            )
            path = tile_dir / f"{args.zoom}-{x}-{y}.png"
            status: int | str = "cached"
            content_type: str | None = "image/png"
            if args.refresh or not path.exists() or not path.read_bytes().startswith(b"\x89PNG"):
                status, content_type = fetch_tile(url, path)
            west_bound, north_bound = tile_to_lonlat(x, y, args.zoom)
            east_bound, south_bound = tile_to_lonlat(x + 1, y + 1, args.zoom)
            source_tiles.append(
                {
                    "x": x,
                    "y": y,
                    "z": args.zoom,
                    "url": url,
                    "http_status": status,
                    "content_type": content_type,
                    "path": str(path.relative_to(ROOT)),
                    "sha256": sha256(path),
                    "bbox_wgs84": [west_bound, south_bound, east_bound, north_bound],
                }
            )
            tile_paths.append(path)

    columns = x_max - x_min + 1
    rows = y_max - y_min + 1
    full_mosaic = args.output / "orthophoto-full-tiles.png"
    subprocess.run(
        [
            "magick",
            "montage",
            *map(str, tile_paths),
            "-tile",
            f"{columns}x{rows}",
            "-geometry",
            f"{TILE_SIZE}x{TILE_SIZE}+0+0",
            str(full_mosaic),
        ],
        check=True,
    )

    crop_left = math.floor((tile_west - x_min) * TILE_SIZE)
    crop_top = math.floor((tile_north - y_min) * TILE_SIZE)
    crop_right = math.ceil((tile_east - x_min) * TILE_SIZE)
    crop_bottom = math.ceil((tile_south - y_min) * TILE_SIZE)
    crop_width = crop_right - crop_left
    crop_height = crop_bottom - crop_top
    cropped = args.output / "orthophoto-context.png"
    subprocess.run(
        [
            "magick",
            str(full_mosaic),
            "-crop",
            f"{crop_width}x{crop_height}+{crop_left}+{crop_top}",
            "+repage",
            str(cropped),
        ],
        check=True,
    )

    resolution_m = 2.0 * WEB_MERCATOR_HALF_WORLD_M / (TILE_SIZE * 2**args.zoom)
    x_upper_left = (
        -WEB_MERCATOR_HALF_WORLD_M
        + (x_min * TILE_SIZE + crop_left + 0.5) * resolution_m
    )
    y_upper_left = (
        WEB_MERCATOR_HALF_WORLD_M
        - (y_min * TILE_SIZE + crop_top + 0.5) * resolution_m
    )
    world_file = cropped.with_suffix(".pgw")
    world_file.write_text(
        "\n".join(
            [
                f"{resolution_m:.12f}",
                "0.0",
                "0.0",
                f"{-resolution_m:.12f}",
                f"{x_upper_left:.6f}",
                f"{y_upper_left:.6f}",
            ]
        )
        + "\n"
    )
    projection_file = cropped.with_suffix(".prj")
    projection_file.write_text(
        'PROJCS["WGS 84 / Pseudo-Mercator",GEOGCS["WGS 84",'
        'DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
        'PROJECTION["Mercator_1SP"],PARAMETER["central_meridian",0],'
        'PARAMETER["scale_factor",1],PARAMETER["false_easting",0],'
        'PARAMETER["false_northing",0],UNIT["metre",1],'
        'AXIS["Easting",EAST],AXIS["Northing",NORTH],AUTHORITY["EPSG","3857"]]\n'
    )

    actual_west, actual_north = tile_to_lonlat(
        x_min + crop_left / TILE_SIZE,
        y_min + crop_top / TILE_SIZE,
        args.zoom,
    )
    actual_east, actual_south = tile_to_lonlat(
        x_min + crop_right / TILE_SIZE,
        y_min + crop_bottom / TILE_SIZE,
        args.zoom,
    )
    manifest = {
        "schema": "green-atlas.nspd-orthophoto-evidence.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "world": {
            "path": str(args.world.relative_to(ROOT)),
            "sha256": sha256(args.world),
            "requested_bbox_wgs84": bbox,
        },
        "source": {
            "provider": "NSPD / Rosreestr public map",
            "layer_id": args.layer_id,
            "layer_name": "Ортофотопланы 2000",
            "endpoint_pattern": (
                "https://nspd.gov.ru/api/aeggis/v4/{layer_id}/"
                "wmts/{z}/{x}/{y}.png"
            ),
            "portal": "https://nspd.gov.ru/map",
            "tls_certificate_verified": False,
            "tls_limitation": (
                "The public host presented a certificate chain rejected by the local "
                "macOS/Python trust store; payload integrity is recorded per tile by SHA-256."
            ),
            "access": "public portal request with ordinary browser headers; no account token",
        },
        "mosaic": {
            "zoom": args.zoom,
            "tile_range": {"x": [x_min, x_max], "y": [y_min, y_max]},
            "tile_count": len(source_tiles),
            "full_pixel_size": [columns * TILE_SIZE, rows * TILE_SIZE],
            "crop_pixels": [crop_left, crop_top, crop_right, crop_bottom],
            "cropped_pixel_size": [crop_width, crop_height],
            "bbox_wgs84": [actual_west, actual_south, actual_east, actual_north],
            "crs": "EPSG:3857",
            "pixel_size_projected_m": resolution_m,
            "full_path": str(full_mosaic.relative_to(ROOT)),
            "full_sha256": sha256(full_mosaic),
            "cropped_path": str(cropped.relative_to(ROOT)),
            "cropped_sha256": sha256(cropped),
            "world_file_path": str(world_file.relative_to(ROOT)),
            "projection_file_path": str(projection_file.relative_to(ROOT)),
        },
        "tiles": source_tiles,
        "admission": {
            "render_base": "evidence_candidate_pending_visual_review",
            "metric_geometry": (
                "Raster supports tracing visible surface boundaries at image resolution; "
                "it is not a cadastral or engineering survey and does not provide curb Z."
            ),
        },
    }
    manifest_path = args.output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest["mosaic"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
