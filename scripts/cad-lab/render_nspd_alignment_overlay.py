#!/usr/bin/env python3
"""Render map-first vectors over the georeferenced NSPD orthophoto.

This is a source-alignment diagnostic.  Orange road polygons still include
estimated widths, cyan buildings are Overture footprints, and green polygons
are OSM cartographic land-use features.  The diagnostic is not render geometry.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import subprocess
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"
DEFAULT_EVIDENCE = ROOT / ".runtime/nspd-ortho2000-kustanayskaya-20260920/manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def iter_rings(geometry: dict) -> Iterable[list[list[float]]]:
    if geometry["type"] == "Polygon":
        yield from geometry["coordinates"]
    elif geometry["type"] == "MultiPolygon":
        for polygon in geometry["coordinates"]:
            yield from polygon


def main() -> None:
    args = parse_args()
    world = json.loads(args.world.read_text())
    evidence = json.loads(args.evidence.read_text())
    output = args.output or args.evidence.parent / "orthophoto-vector-alignment.png"
    width, height = evidence["mosaic"]["cropped_pixel_size"]
    crop_left, crop_top, _, _ = evidence["mosaic"]["crop_pixels"]
    x_min = evidence["mosaic"]["tile_range"]["x"][0]
    y_min = evidence["mosaic"]["tile_range"]["y"][0]
    zoom = evidence["mosaic"]["zoom"]
    origin_lon, origin_lat = world["coordinate_frame"]["origin_wgs84_lon_lat"]
    earth_radius = world["coordinate_frame"]["earth_radius_m"]
    scale = 2**zoom

    def local_to_pixel(point: list[float]) -> tuple[float, float]:
        x_m, y_m = point[:2]
        lon = origin_lon + math.degrees(x_m / (earth_radius * math.cos(math.radians(origin_lat))))
        lat = origin_lat + math.degrees(y_m / earth_radius)
        tile_x = (lon + 180.0) / 360.0 * scale
        tile_y = (
            1.0
            - math.asinh(math.tan(math.radians(lat))) / math.pi
        ) / 2.0 * scale
        return (
            (tile_x - x_min) * 256.0 - crop_left,
            (tile_y - y_min) * 256.0 - crop_top,
        )

    svg: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="none"/>',
    ]

    def add_geometry(geometry: dict, fill: str, opacity: float) -> None:
        for ring in iter_rings(geometry):
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in map(local_to_pixel, ring))
            svg.append(
                f'<polygon points="{points}" fill="{fill}" fill-opacity="{opacity}"/>'
            )

    for road in world["roads"]:
        add_geometry(road["surface_geometry_local"], "#ff6b00", 0.34)
    for green in world["green_areas"]:
        add_geometry(green["geometry_local"], "#00d26a", 0.28)
    for building in world["buildings"]:
        add_geometry(building["geometry_local"], "#00b7ff", 0.38)
    for feature in world["infrastructure"]:
        geometry = feature["geometry_local"]
        if geometry["type"] != "Point":
            continue
        x, y = local_to_pixel(geometry["coordinates"])
        svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.2" fill="#ff00d4"/>')

    legend = [
        ("#ff6b00", "road surface; width may be estimated"),
        ("#00b7ff", "Overture building footprint"),
        ("#00d26a", "OSM green/land-use polygon"),
        ("#ff00d4", "mapped infrastructure point"),
    ]
    svg.append('<rect x="14" y="14" width="310" height="112" rx="5" fill="#ffffff" fill-opacity="0.88"/>')
    for index, (color, label) in enumerate(legend):
        y = 38 + index * 24
        svg.append(f'<rect x="27" y="{y - 11}" width="14" height="14" fill="{color}"/>')
        svg.append(
            f'<text x="49" y="{y}" font-family="Arial,sans-serif" font-size="14" '
            f'fill="#182126">{html.escape(label)}</text>'
        )
    svg.append("</svg>")

    overlay_svg = output.with_suffix(".overlay.svg")
    overlay_png = output.with_suffix(".overlay.png")
    overlay_svg.write_text("\n".join(svg))
    subprocess.run(
        ["magick", "-background", "none", str(overlay_svg), str(overlay_png)],
        check=True,
    )
    source_image = ROOT / evidence["mosaic"]["cropped_path"]
    subprocess.run(
        [
            "magick",
            str(source_image),
            str(overlay_png),
            "-compose",
            "over",
            "-composite",
            str(output),
        ],
        check=True,
    )
    receipt = {
        "schema": "green-atlas.nspd-vector-alignment-diagnostic.v1",
        "world": str(args.world.relative_to(ROOT)),
        "evidence": str(args.evidence.relative_to(ROOT)),
        "output": str(output.relative_to(ROOT)),
        "counts": {
            "roads": len(world["roads"]),
            "buildings": len(world["buildings"]),
            "green_areas": len(world["green_areas"]),
            "infrastructure": len(world["infrastructure"]),
        },
        "status": "diagnostic_only_pending_visual_review",
    }
    output.with_suffix(".receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
