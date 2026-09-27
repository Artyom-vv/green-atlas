"""Build a deterministic broad-context terrain grid from Copernicus GLO-30.

The grid is context grade. It preserves the source pixel information through
bilinear interpolation and applies one recorded candidate vertical offset. It
must never replace the denser source-annotation terrain inside the project.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEM = ROOT / ".runtime/overture-kustanayskaya-20260920/Copernicus_DSM_COG_10_N55_00_E037_00_DEM.tif"
DEFAULT_ALIGNMENT = ROOT / ".runtime/deterministic-render-audit-20260919/candidate-osm-alignment.json"
DEFAULT_WORLD = ROOT / ".runtime/world-base-packet-20260920/world-base-packet.json"
DEFAULT_OUTPUT = ROOT / ".runtime/continuous-context-terrain-20260920"
EARTH_RADIUS_M = 6_378_137.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_crop(dem: Path, left: int, top: int, width: int, height: int) -> list[list[float]]:
    command = [
        "magick", f"{dem}[0]", "-crop", f"{width}x{height}+{left}+{top}",
        "txt:-",
    ]
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    values = [[math.nan] * width for _ in range(height)]
    pattern = re.compile(r"^(\d+),(\d+):.*gray\(([-+0-9.eE]+)%\)")
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if match:
            x, y, percentage = match.groups()
            values[int(y)][int(x)] = float(percentage) / 100.0
    if any(not math.isfinite(value) for row in values for value in row):
        raise ValueError("ImageMagick did not return every requested DEM pixel")
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dem", type=Path, default=DEFAULT_DEM)
    parser.add_argument("--alignment", type=Path, default=DEFAULT_ALIGNMENT)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--half-extent-m", type=float, default=300.0)
    parser.add_argument("--grid-step-m", type=float, default=10.0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    alignment = json.loads(args.alignment.read_text())
    world = json.loads(args.world.read_text())
    contract = alignment["similarity_transform_row_vector"]
    scale = float(contract["scale"])
    rotation = contract["rotation"]
    translation = contract["translation"]
    lon0, lat0 = alignment["projection_before_fit"]["reference_lon_lat"]
    origin = world["coordinates"]["local_origin_dxf_xyz"]
    vertical_offset = world["vertical_tie_audit"]["candidate_vertical_offset_m"]

    def local_to_lonlat(x: float, y: float) -> tuple[float, float]:
        dxf_x, dxf_y = x + origin[0], y + origin[1]
        qx = (dxf_x - translation[0]) / scale
        qy = (dxf_y - translation[1]) / scale
        tangent_x = qx * rotation[0][0] + qy * rotation[0][1]
        tangent_y = qx * rotation[1][0] + qy * rotation[1][1]
        lon = lon0 + math.degrees(tangent_x / (EARTH_RADIUS_M * math.cos(math.radians(lat0))))
        lat = lat0 + math.degrees(tangent_y / EARTH_RADIUS_M)
        return lon, lat

    def pixel(lon: float, lat: float) -> tuple[float, float]:
        return (lon - 37.0) * 2400.0, (56.0 - lat) * 3600.0

    count = round(2 * args.half_extent_m / args.grid_step_m) + 1
    axis = [-args.half_extent_m + i * args.grid_step_m for i in range(count)]
    pixel_points = [pixel(*local_to_lonlat(x, y)) for y in axis for x in axis]
    left = math.floor(min(point[0] for point in pixel_points)) - 1
    right = math.ceil(max(point[0] for point in pixel_points)) + 1
    top = math.floor(min(point[1] for point in pixel_points)) - 1
    bottom = math.ceil(max(point[1] for point in pixel_points)) + 1
    crop = extract_crop(args.dem, left, top, right-left+1, bottom-top+1)

    def sample(px: float, py: float) -> float:
        x = px - left
        y = py - top
        x0, y0 = math.floor(x), math.floor(y)
        fx, fy = x-x0, y-y0
        a, b = crop[y0][x0], crop[y0][x0+1]
        c, d = crop[y0+1][x0], crop[y0+1][x0+1]
        return (a*(1-fx)+b*fx)*(1-fy) + (c*(1-fx)+d*fx)*fy

    vertices = []
    for x, y, (px, py) in zip(
        [x for y in axis for x in axis],
        [y for y in axis for x in axis],
        pixel_points,
    ):
        dem_z = sample(px, py)
        vertices.append({
            "xyz": [round(x, 6), round(y, 6), round(dem_z + vertical_offset, 6)],
            "dem_z_egm2008_m": round(dem_z, 6),
            "height_status": "copernicus_glo30_candidate_offset_context_only",
        })
    triangles = []
    for row in range(count-1):
        for column in range(count-1):
            a = row*count+column
            b, c, d = a+1, a+count, a+count+1
            triangles.extend([[a, b, d], [a, d, c]])

    packet = {
        "schema": "green-atlas.continuous-context-terrain.v1",
        "status": "continuous_context_only_not_local_truth",
        "inputs": {
            "dem": {"path": str(args.dem.resolve()), "sha256": sha256(args.dem)},
            "alignment": {"path": str(args.alignment.resolve()), "sha256": sha256(args.alignment)},
            "world": {"path": str(args.world.resolve()), "sha256": sha256(args.world)},
        },
        "coordinate_frame": {
            "horizontal": "metres relative to world local_origin_dxf_xyz",
            "local_origin_dxf_xyz": origin,
            "vertical": "Copernicus EGM2008 plus candidate median offset",
            "candidate_vertical_offset_m": vertical_offset,
            "vertical_residual_stddev_m": world["vertical_tie_audit"]["residual_stddev_m"],
            "vertical_max_abs_residual_m": world["vertical_tie_audit"]["max_abs_residual_m"],
        },
        "grid": {
            "half_extent_m": args.half_extent_m,
            "step_m": args.grid_step_m,
            "rows": count,
            "columns": count,
            "dem_crop_pixel_bounds": [left, top, right, bottom],
        },
        "vertices": vertices,
        "triangles": triangles,
        "limitations": [
            "GLO-30 is a 30 m digital surface model and can include roofs, vegetation, and infrastructure.",
            "Bilinear interpolation adds no new spatial detail.",
            "The candidate vertical offset has metre-scale residuals and is unsuitable for curbs or tree contact.",
            "Annotation-derived terrain must override this grid wherever it exists.",
        ],
    }
    terrain_path = args.output / "terrain.json"
    terrain_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n")
    receipt = {
        "schema": "green-atlas.continuous-context-terrain-receipt.v1",
        "terrain": {"path": str(terrain_path.resolve()), "sha256": sha256(terrain_path)},
        "vertices": len(vertices),
        "triangles": len(triangles),
        "height_range_m": [
            min(row["xyz"][2] for row in vertices),
            max(row["xyz"][2] for row in vertices),
        ],
        "status": packet["status"],
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
