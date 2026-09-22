#!/usr/bin/env python3
"""Resolve the concrete NSPD layer(s) covering a grouped orthophoto tile."""

from __future__ import annotations

import argparse
import json
import math
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GROUP = ROOT / ".runtime/nspd-ortho2000-kustanayskaya-20260920/layer-36344-browser.json"
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", type=Path, default=DEFAULT_GROUP)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def fetch_layer(layer_id: int) -> dict:
    url = f"https://nspd.gov.ru/api/geoportal/v1/layers/{layer_id}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://nspd.gov.ru/map?thematic=PKK",
            "Origin": "https://nspd.gov.ru",
        },
    )
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(request, timeout=30, context=context) as response:
        return json.loads(response.read())


def lonlat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    radius = 6_378_137.0
    x = radius * math.radians(lon)
    y = radius * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))
    return x, y


def main() -> None:
    args = parse_args()
    group = json.loads(args.group.read_text())
    world = json.loads(args.world.read_text())
    layer_ids = [int(item["id"]) for item in group["options"]["groupedLayers"]]
    with ThreadPoolExecutor(max_workers=8) as pool:
        layers = list(pool.map(fetch_layer, layer_ids))
    lon, lat = world["coordinate_frame"]["origin_wgs84_lon_lat"]
    x, y = lonlat_to_mercator(lon, lat)
    candidates = []
    for layer in layers:
        coverage = layer.get("coverage") or {}
        bbox = coverage.get("bbox")
        if bbox and bbox[0] <= x <= bbox[2] and bbox[1] <= y <= bbox[3]:
            candidates.append(layer)
    result = {
        "schema": "green-atlas.nspd-group-layer-resolution.v1",
        "group_id": group["id"],
        "group_name": group["name"],
        "point_wgs84": [lon, lat],
        "point_epsg3857": [x, y],
        "queried_layer_count": len(layers),
        "coverage_candidates": candidates,
        "all_layers": layers,
        "limitation": (
            "Bounding-box membership identifies possible backing layers; it does not prove "
            "which source pixel won when grouped WMTS layers overlap."
        ),
    }
    output = args.output or args.group.parent / "group-layer-resolution.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "queried_layer_count": len(layers),
                "coverage_candidates": [
                    {"id": item["id"], "name": item["name"]} for item in candidates
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
