"""Attach reviewed height pairs to the two compact curb chains.

The result is a local render mesh, not an admitted terrain model. Heights are
interpolated/extrapolated along curb chains and IDW-interpolated inside the
clipped corridor. Crop-closing edges remain synthetic.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from shapely import delaunay_triangles
from shapely.geometry import LineString, MultiPoint, Point, Polygon, shape


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".runtime/curb-corridor-audit-20260919/curb-corridor.json"
OUT = ROOT / ".runtime/compact-street-render-20260919"


def interpolate(samples, position):
    samples = sorted(samples)
    if len(samples) < 2:
        raise ValueError("At least two height samples are required")
    left, right = (samples[0], samples[1]) if position <= samples[0][0] else (
        (samples[-2], samples[-1]) if position >= samples[-1][0] else
        next((samples[index], samples[index+1]) for index in range(len(samples)-1)
             if samples[index][0] <= position <= samples[index+1][0])
    )
    if abs(right[0]-left[0]) < 1e-9:
        return left[1]
    ratio = (position-left[0])/(right[0]-left[0])
    return left[1] + ratio*(right[1]-left[1])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(SOURCE.read_text())
    corridor = shape(data["corridor"])
    terrain = json.loads((ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json").read_text())
    chain_lines = [LineString(chain["xy"]) for chain in data["chains"]]
    controls = []
    chain_controls = [[] for _ in chain_lines]
    interior_controls = []
    for row in terrain["vertices"]:
        point = Point(row["xyz"][:2])
        distances = [point.distance(line) for line in chain_lines]
        closest = min(range(len(distances)), key=distances.__getitem__)
        control = {
            "picket": row["picket"],
            "xy": row["xyz"][:2],
            "road_z": row["xyz"][2],
            "curb_top_z": row["alternative_value"],
            "chain_distance_m": distances[closest],
        }
        if row["alternative_value"] is not None and distances[closest] <= 0.03:
            control["chain_index"] = closest
            control["station_m"] = chain_lines[closest].project(point)
            chain_controls[closest].append(control)
        else:
            control["chain_index"] = None
            interior_controls.append(control)
        controls.append(control)
    if [len(group) for group in chain_controls] != [2, 3]:
        raise RuntimeError(f"Expected paired controls [2,3], got {[len(group) for group in chain_controls]}")

    def chain_height(chain_index, xy, key):
        station = chain_lines[chain_index].project(Point(xy))
        return interpolate([(row["station_m"], row[key]) for row in chain_controls[chain_index]], station)

    all_height_points = [
        (row["xy"][0], row["xy"][1], row["road_z"])
        for row in controls
    ]

    def idw(xy):
        ranked = sorted((math.dist(xy, (x, y)), z) for x, y, z in all_height_points)
        if ranked[0][0] < 1e-8:
            return ranked[0][1]
        weighted = [(1/max(distance, 0.5)**2, z) for distance, z in ranked]
        return sum(weight*z for weight, z in weighted)/sum(weight for weight, _z in weighted)

    exterior = list(corridor.exterior.coords)[:-1]
    triangulation_points = exterior + [tuple(row["xy"]) for row in interior_controls]
    triangles = [
        triangle for triangle in delaunay_triangles(MultiPoint(triangulation_points)).geoms
        if corridor.covers(triangle)
    ]
    vertices = []
    vertex_lookup = {}
    faces = []

    def road_height(xy):
        distances = [Point(xy).distance(line) for line in chain_lines]
        closest = min(range(len(distances)), key=distances.__getitem__)
        if distances[closest] < 1e-5:
            return chain_height(closest, xy, "road_z")
        return idw(xy)

    for triangle in triangles:
        face = []
        for xy in list(triangle.exterior.coords)[:3]:
            key = (round(xy[0], 8), round(xy[1], 8))
            if key not in vertex_lookup:
                vertex_lookup[key] = len(vertices)
                vertices.append([xy[0], xy[1], road_height(xy)])
            face.append(vertex_lookup[key])
        faces.append(face)

    chain_geometry = []
    for index, chain in enumerate(data["chains"]):
        samples = []
        for xy in chain["xy"]:
            samples.append({
                "xy": list(xy),
                "road_z": chain_height(index, xy, "road_z"),
                "curb_top_z": chain_height(index, xy, "curb_top_z"),
            })
        chain_geometry.append({**chain, "samples": samples})

    result = {
        "source": str(SOURCE),
        "origin": [data["bbox"][0], data["bbox"][1], 150.0],
        "controls": controls,
        "chains": chain_geometry,
        "road_mesh": {"vertices": vertices, "triangles": faces},
        "corridor_area_m2": corridor.area,
        "height_method": {
            "curbs": "piecewise linear interpolation/extrapolation along each chain from paired marks",
            "interior": "inverse-distance squared from eight reviewed trial controls",
            "status": "visual_trial_not_surveyed_dtm",
        },
        "limitations": data["limitations"] + [
            "Exterior crop-closure heights use IDW and are not source curb elevations.",
            "Curb top values come from the alternative member of manually paired annotations.",
        ],
    }
    (OUT / "elevated-corridor.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({
        "control_counts": [len(group) for group in chain_controls],
        "max_chain_distance_m": max(row["chain_distance_m"] for group in chain_controls for row in group),
        "vertices": len(vertices),
        "triangles": len(faces),
        "z_range": [min(v[2] for v in vertices), max(v[2] for v in vertices)],
    }))


if __name__ == "__main__":
    main()
