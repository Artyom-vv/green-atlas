"""Render a fast full-street SVG/PNG audit of the working prototype."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def rings(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [polygon[0] for polygon in geometry["coordinates"]]
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    world = json.loads(args.world.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    min_x, min_y, max_x, max_y = (float(value) for value in world["scope"]["local_bounds_m"])
    width, height, margin = 1200, 1800, 45
    scale = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))
    offset_x = (width - (max_x - min_x) * scale) * 0.5
    offset_y = (height - (max_y - min_y) * scale) * 0.5

    def point(x: float, y: float) -> tuple[float, float]:
        return offset_x + (x - min_x) * scale, height - offset_y - (y - min_y) * scale

    def path(coords: list[list[float]]) -> str:
        return "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in (point(float(p[0]), float(p[1])) for p in coords)) + " Z"

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#e7ebe4"/>',
        '<g stroke-linejoin="round" stroke-linecap="round">',
    ]
    layer_colors = {
        "road_explicit": "#555b5e", "road_corroborated": "#5d6365", "road_estimated": "#73797a",
        "grass": "#a9c48b", "woodland": "#77966a", "recreation": "#d0b783",
    }
    for layer in world["render_layers"]:
        color = layer_colors.get(layer["semantic"], "#aeb6aa")
        for triangle in layer["triangles_xyz_local_m"]:
            svg.append(f'<path d="{path(triangle)}" fill="{color}" stroke="none"/>')
    for building in world["buildings"]:
        fill = "#efeee9" if building["properties"].get("height_m") is not None else "#dec6ac"
        for ring in rings(building["geometry_local"]):
            svg.append(f'<path d="{path(ring)}" fill="{fill}" stroke="#7b7770" stroke-width="0.7"/>')
    for row in world["inventory_vegetation"]:
        x, y, _z = row["position_local_xyz_m"]
        px, py = point(float(x), float(y))
        if row["kind"] == "tree":
            radius = max(1.4, min(float(row.get("height_m") or 4.0) * 0.16, 4.2))
            fill = "#244f2d" if any(token in row["species"].casefold() for token in ("ель", "сосн", "туя")) else "#397343"
            svg.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="{radius:.2f}" fill="{fill}" fill-opacity="0.82" stroke="#173a20" stroke-width="0.45"/>')
        elif row["kind"] == "shrub_group":
            radius = max(1.0, min((int(row["count"]) ** 0.5) * 0.55, 4.0))
            svg.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="{radius:.2f}" fill="#75a83e" fill-opacity="0.74" stroke="#456d27" stroke-width="0.4"/>')
        else:
            svg.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="1" fill="#7a4421"/>')
    svg.extend([
        '</g>',
        '<g font-family="Arial, sans-serif" fill="#162019">',
        '<rect x="25" y="20" width="650" height="78" rx="8" fill="#ffffff" fill-opacity="0.9"/>',
        '<text x="45" y="52" font-size="24" font-weight="700">Кустанайская — GIS + инвентаризация</text>',
        f'<text x="45" y="80" font-size="16">{len(world["buildings"])} зданий · {len(world["roads"])} дорог · {len(world["inventory_vegetation"])} записей озеленения</text>',
        '</g>',
        '</svg>',
    ])
    svg_path = args.output / "prototype-overview.svg"
    png_path = args.output / "prototype-overview.png"
    svg_path.write_text("\n".join(svg))
    subprocess.run(["magick", str(svg_path), str(png_path)], check=True)
    print(png_path)


if __name__ == "__main__":
    main()
