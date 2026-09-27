"""Render a deterministic plan/profile audit for the prototype terrain."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path


def color(value: float, minimum: float, maximum: float) -> str:
    t = 0.5 if maximum <= minimum else min(max((value - minimum) / (maximum - minimum), 0.0), 1.0)
    stops = ((43, 92, 128), (96, 156, 119), (205, 183, 92))
    if t <= 0.5:
        u = t * 2.0
        a, b = stops[0], stops[1]
    else:
        u = (t - 0.5) * 2.0
        a, b = stops[1], stops[2]
    rgb = tuple(round(a[index] * (1.0 - u) + b[index] * u) for index in range(3))
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = json.loads(args.world.read_text())
    terrain = world["terrain"]
    model = terrain["source_model"]
    rows, columns = int(terrain["rows"]), int(terrain["columns"])
    vertices = [row["xyz_local_m"] for row in terrain["vertices"]]
    xs = [float(vertices[column][0]) for column in range(columns)]
    ys = [float(vertices[row * columns][1]) for row in range(rows)]
    minimum = min(float(row[2]) for row in vertices)
    maximum = max(float(row[2]) for row in vertices)

    def z_at(x: float, y: float) -> float:
        fx = (x - xs[0]) / (xs[-1] - xs[0]) * (columns - 1)
        fy = (y - ys[0]) / (ys[-1] - ys[0]) * (rows - 1)
        x0 = min(max(math.floor(fx), 0), columns - 2)
        y0 = min(max(math.floor(fy), 0), rows - 2)
        tx, ty = min(max(fx - x0, 0.0), 1.0), min(max(fy - y0, 0.0), 1.0)
        a = float(vertices[y0 * columns + x0][2])
        b = float(vertices[y0 * columns + x0 + 1][2])
        c = float(vertices[(y0 + 1) * columns + x0][2])
        d = float(vertices[(y0 + 1) * columns + x0 + 1][2])
        return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty

    width, height = 1600, 1000
    map_box = (55.0, 130.0, 720.0, 815.0)
    profile_box = (860.0, 180.0, 680.0, 590.0)
    min_x, min_y, max_x, max_y = (float(value) for value in world["scope"]["local_bounds_m"])
    map_scale = min(map_box[2] / (max_x - min_x), map_box[3] / (max_y - min_y))
    map_ox = map_box[0] + (map_box[2] - (max_x - min_x) * map_scale) * 0.5
    map_oy = map_box[1] + (map_box[3] - (max_y - min_y) * map_scale) * 0.5

    def map_point(x: float, y: float) -> tuple[float, float]:
        return map_ox + (x - min_x) * map_scale, map_oy + (max_y - y) * map_scale

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f3f1eb"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#17201d}.muted{fill:#53605b}.axis{stroke:#65716c;stroke-width:1}.grid{stroke:#c9cec8;stroke-width:1}</style>',
        '<text x="55" y="58" font-size="30" font-weight="700">Кустанайская — аудит высотной модели</text>',
        '<text x="55" y="92" font-size="17" class="muted">Московская система высот · поверхность по исходным отметкам · без GLO-30 в активном Z</text>',
        '<text x="55" y="122" font-size="16" font-weight="700">План высот</text>',
    ]

    for row in range(rows - 1):
        for column in range(columns - 1):
            indices = (
                row * columns + column,
                row * columns + column + 1,
                (row + 1) * columns + column + 1,
                (row + 1) * columns + column,
            )
            points = [map_point(float(vertices[index][0]), float(vertices[index][1])) for index in indices]
            mean_z = sum(float(vertices[index][2]) for index in indices) / 4.0
            svg.append(
                f'<polygon points="{" ".join(f"{x:.2f},{y:.2f}" for x, y in points)}" fill="{color(mean_z, minimum, maximum)}" stroke="none"/>'
            )

    axis = max(
        (
            row for row in world.get("named_reference_axes", [])
            if row.get("name") == "Кустанайская улица" and row.get("project_geometry_local")
        ),
        key=lambda row: float(row.get("length_in_project_m") or 0.0),
    )
    axis_points = [
        (float(point[0]), float(point[1]))
        for point in axis["project_geometry_local"]["coordinates"]
    ]
    svg.append(
        f'<polyline points="{" ".join(f"{x:.2f},{y:.2f}" for x, y in (map_point(*point) for point in axis_points))}" fill="none" stroke="#ffffff" stroke-width="7" opacity="0.75"/>'
    )
    svg.append(
        f'<polyline points="{" ".join(f"{x:.2f},{y:.2f}" for x, y in (map_point(*point) for point in axis_points))}" fill="none" stroke="#182a24" stroke-width="2.2"/>'
    )
    for building in world["buildings"]:
        geometry = building["geometry_local"]
        polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
        for polygon in polygons:
            ring = polygon[0]
            svg.append(
                f'<polyline points="{" ".join(f"{x:.2f},{y:.2f}" for x, y in (map_point(float(p[0]), float(p[1])) for p in ring))}" fill="none" stroke="#202522" stroke-width="0.8" opacity="0.55"/>'
            )

    profile: list[tuple[float, float]] = [(0.0, z_at(*axis_points[0]))]
    distance = 0.0
    for start, end in zip(axis_points, axis_points[1:]):
        segment = math.dist(start, end)
        steps = max(1, math.ceil(segment / 3.0))
        for step in range(1, steps + 1):
            ratio = step / steps
            x = start[0] + (end[0] - start[0]) * ratio
            y = start[1] + (end[1] - start[1]) * ratio
            profile.append((distance + segment * ratio, z_at(x, y)))
        distance += segment
    profile_min = min(value for _, value in profile)
    profile_max = max(value for _, value in profile)
    profile_padding = max((profile_max - profile_min) * 0.12, 0.5)
    profile_min -= profile_padding
    profile_max += profile_padding

    def profile_point(station: float, elevation: float) -> tuple[float, float]:
        x = profile_box[0] + station / max(distance, 1.0) * profile_box[2]
        y = profile_box[1] + (profile_max - elevation) / (profile_max - profile_min) * profile_box[3]
        return x, y

    svg.extend([
        '<text x="860" y="122" font-size="16" font-weight="700">Продольный профиль оси OSM, уложенный на исходную поверхность</text>',
        f'<rect x="{profile_box[0]}" y="{profile_box[1]}" width="{profile_box[2]}" height="{profile_box[3]}" fill="#ffffff" stroke="#aeb6b1"/>',
    ])
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        station = distance * fraction
        x, _ = profile_point(station, profile_min)
        svg.append(f'<line x1="{x:.2f}" y1="{profile_box[1]}" x2="{x:.2f}" y2="{profile_box[1] + profile_box[3]}" class="grid"/>')
        svg.append(f'<text x="{x:.2f}" y="{profile_box[1] + profile_box[3] + 27}" font-size="14" text-anchor="middle">{station:.0f} м</text>')
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        elevation = profile_min + (profile_max - profile_min) * fraction
        _, y = profile_point(0.0, elevation)
        svg.append(f'<line x1="{profile_box[0]}" y1="{y:.2f}" x2="{profile_box[0] + profile_box[2]}" y2="{y:.2f}" class="grid"/>')
        svg.append(f'<text x="{profile_box[0] - 12}" y="{y + 5:.2f}" font-size="14" text-anchor="end">{model["origin_height_moscow_m"] + elevation:.1f}</text>')
    profile_pixels = [profile_point(*row) for row in profile]
    for start, end in zip(profile_pixels, profile_pixels[1:]):
        svg.append(
            f'<line x1="{start[0]:.2f}" y1="{start[1]:.2f}" x2="{end[0]:.2f}" y2="{end[1]:.2f}" stroke="#08785c" stroke-width="5" stroke-linecap="round"/>'
        )

    audit = model["render_grid_audit"]
    lines = [
        f'Исходные отметки: {model["source_control_count"]}; после XY-дедупликации: {model["deduplicated_bare_earth_supports"]}',
        f'Диапазон сетки: {audit["height_delta_m"]:.2f} м; RMSE гладкой модели: {model["fit_rmse_m"]:.2f} м',
        f'Макс. уклон соседних ячеек: {audit["maximum_neighbor_slope_ratio"] * 100:.2f}%; P99: {audit["p99_neighbor_slope_ratio"] * 100:.2f}%',
        'Локальный микрорельеф и ступени бордюров намеренно не интерполируются без TIN/3D-breaklines.',
    ]
    svg.append('<rect x="835" y="820" width="705" height="130" rx="8" fill="#e4e8e1"/>')
    for index, line in enumerate(lines):
        svg.append(
            f'<text x="860" y="{850 + index * 27}" font-size="15" class="muted">{line}</text>'
        )
    svg.append('</svg>')

    svg_path = args.output / "terrain-elevation-audit.svg"
    png_path = args.output / "terrain-elevation-audit.png"
    svg_path.write_text("\n".join(svg))
    if shutil.which("rsvg-convert"):
        subprocess.run(
            ["rsvg-convert", "-o", str(png_path), str(svg_path)], check=True
        )
    else:
        subprocess.run(["magick", str(svg_path), str(png_path)], check=True)
    print(png_path)


if __name__ == "__main__":
    main()
