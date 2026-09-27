#!/usr/bin/env python3
"""Render orthophoto surface candidates, source vectors, and KartaView cameras."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / ".runtime/nspd-surface-trace-kustanayskaya-20260920/surface-packet.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def rings(geometry: dict) -> Iterable[list[list[float]]]:
    if geometry["type"] == "Polygon":
        yield from geometry["coordinates"]
    elif geometry["type"] == "MultiPolygon":
        for polygon in geometry["coordinates"]:
            yield from polygon


def main() -> None:
    args = parse_args()
    packet = json.loads(args.packet.read_text())
    ortho_manifest_path = ROOT / packet["source"]["orthophoto_manifest"]
    world_path = ROOT / packet["source"]["world"]
    ortho = json.loads(ortho_manifest_path.read_text())
    world = json.loads(world_path.read_text())
    width, height = ortho["mosaic"]["cropped_pixel_size"]
    zoom = int(ortho["mosaic"]["zoom"])
    scale = 2**zoom
    x_min = int(ortho["mosaic"]["tile_range"]["x"][0])
    y_min = int(ortho["mosaic"]["tile_range"]["y"][0])
    crop_left, crop_top, _, _ = ortho["mosaic"]["crop_pixels"]
    origin_lon, origin_lat = world["coordinate_frame"]["origin_wgs84_lon_lat"]
    earth_radius = float(world["coordinate_frame"]["earth_radius_m"])

    def local_to_pixel(point: list[float]) -> tuple[float, float]:
        x_m, y_m = map(float, point[:2])
        lon = origin_lon + math.degrees(x_m / (earth_radius * math.cos(math.radians(origin_lat))))
        lat = origin_lat + math.degrees(y_m / earth_radius)
        tile_x = (lon + 180.0) / 360.0 * scale
        tile_y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * scale
        return (
            (tile_x - x_min) * 256.0 - crop_left,
            (tile_y - y_min) * 256.0 - crop_top,
        )

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
    ]

    colors = {"parking_apron": "#ffd000", "sidewalk": "#00c8ff", "lawn": "#39d353"}
    for feature in packet["traced_surfaces"]:
        color = colors[feature["semantic"]]
        for ring in rings(feature["resolved_geometry_local"]):
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in map(local_to_pixel, ring))
            svg.append(f'<polygon points="{points}" fill="{color}" fill-opacity="0.52"/>')

    reference_colors = {"road": "#ff5b00", "building": "#00a8ff"}
    for feature in packet["referenced_world_features"]:
        color = reference_colors[feature["semantic"]]
        for ring in rings(feature["geometry_local"]):
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in map(local_to_pixel, ring))
            svg.append(f'<polygon points="{points}" fill="{color}" fill-opacity="0.35"/>')

    camera_pixels = [local_to_pixel(camera["local_xy_m"]) for camera in packet["kartaview_cameras"]]
    if camera_pixels:
        points = " ".join(f"{x:.2f},{y:.2f}" for x, y in camera_pixels)
        svg.append(f'<polyline points="{points}" fill="none" stroke="#ff00d4" stroke-width="3"/>')
        for index, ((x, y), camera) in enumerate(zip(camera_pixels, packet["kartaview_cameras"]), start=1):
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="#ff00d4"/>')
            svg.append(
                f'<text x="{x + 7:.2f}" y="{y - 7:.2f}" font-family="Arial" font-size="13" '
                f'fill="white">{index}</text>'
            )

    for candidate in packet.get("vegetation_candidates", []):
        x, y = local_to_pixel(candidate["local_xy_m"])
        svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="9" fill="none" stroke="#ff2d2d" stroke-width="3"/>')
        svg.append(f'<path d="M {x - 6:.2f} {y:.2f} L {x + 6:.2f} {y:.2f} M {x:.2f} {y - 6:.2f} L {x:.2f} {y + 6:.2f}" stroke="#ff2d2d" stroke-width="2"/>')
        svg.append(
            f'<text x="{x + 12:.2f}" y="{y - 10:.2f}" font-family="Arial" font-size="12" '
            f'fill="white">{candidate["asset_species"]} {candidate["height_m"]:.1f}m</text>'
        )

    x0, y0, x1, y1 = packet["scene_roi"]["pixels"]
    svg.extend(
        [
            f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="none" stroke="white" stroke-width="3"/>',
            '<rect x="14" y="14" width="390" height="180" rx="5" fill="white" fill-opacity="0.88"/>',
            '<rect x="26" y="29" width="15" height="15" fill="#ff5b00"/><text x="50" y="42" font-family="Arial" font-size="14">satellite-width road</text>',
            '<rect x="26" y="54" width="15" height="15" fill="#ffd000"/><text x="50" y="67" font-family="Arial" font-size="14">orthophoto parking candidate</text>',
            '<rect x="26" y="79" width="15" height="15" fill="#00c8ff"/><text x="50" y="92" font-family="Arial" font-size="14">orthophoto sidewalk candidate</text>',
            '<rect x="26" y="104" width="15" height="15" fill="#00a8ff"/><text x="50" y="117" font-family="Arial" font-size="14">corroborated building footprint</text>',
            '<rect x="26" y="129" width="15" height="15" fill="#39d353"/><text x="50" y="142" font-family="Arial" font-size="14">orthophoto lawn candidate</text>',
            '<circle cx="33" cy="163" r="8" fill="none" stroke="#ff2d2d" stroke-width="3"/><text x="50" y="168" font-family="Arial" font-size="14">reviewed vegetation candidate</text>',
            '<circle cx="318" cy="36" r="5" fill="#ff00d4"/><text x="330" y="41" font-family="Arial" font-size="14">KartaView path</text>',
            "</svg>",
        ]
    )
    output = args.output or args.packet.parent / "surface-trace-overlay.png"
    svg_path = output.with_suffix(".svg")
    overlay = output.with_suffix(".overlay.png")
    svg_path.write_text("\n".join(svg))
    subprocess.run(["magick", "-background", "none", str(svg_path), str(overlay)], check=True)
    source = ROOT / ortho["mosaic"]["cropped_path"]
    subprocess.run(["magick", str(source), str(overlay), "-compose", "over", "-composite", str(output)], check=True)
    crop_output = output.with_name(f"{output.stem}-roi.png")
    subprocess.run(
        [
            "magick",
            str(output),
            "-crop",
            f"{x1 - x0}x{y1 - y0}+{x0}+{y0}",
            "+repage",
            "-resize",
            f"{(x1 - x0) * 2}x{(y1 - y0) * 2}",
            str(crop_output),
        ],
        check=True,
    )
    print(json.dumps({"output": str(output), "roi_output": str(crop_output)}, indent=2))


if __name__ == "__main__":
    main()
