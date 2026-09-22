"""Build source-backed vertical curb faces from CAD breaklines and height pairs.

Dashed curb primitives are joined only within their original INSERT.  Global
line merging may connect exact shared endpoints, but never bridges a new gap.
Upper/lower heights are interpolated only between accepted source controls on
the same merged chain.  No road width or horizontal curb cap is invented.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any

import ezdxf
from ezdxf.path import make_path
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import linemerge, unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOPOGRAPHY = next(
    (ROOT / ".runtime/kustanayskaya-mac-ready-20260917/dxf").rglob(
        "00.1_10004141_Топография.dxf"
    )
)
DEFAULT_CONTROLS = (
    ROOT / ".runtime/topographic-elevation-controls-20260920/elevation-controls.json"
)
DEFAULT_OUTPUT = ROOT / ".runtime/source-curb-mesh-20260920"
MAX_DASH_GAP_M = 0.55


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def primitive_line(entity: Any) -> LineString | None:
    if entity.dxftype() == "LINE":
        coordinates = [tuple(entity.dxf.start)[:2], tuple(entity.dxf.end)[:2]]
    elif entity.dxftype() == "LWPOLYLINE":
        coordinates = [tuple(point)[:2] for point in make_path(entity).flattening(0.02)]
    else:
        return None
    return LineString(coordinates) if len(coordinates) >= 2 else None


def within_insert_joins(parts: list[LineString], max_gap: float = MAX_DASH_GAP_M) -> list[LineString]:
    endpoints = [
        (index, side, tuple(line.coords[0] if side == 0 else line.coords[-1]))
        for index, line in enumerate(parts) for side in (0, -1)
    ]
    nearest = {}
    for endpoint_index, (owner, _side, point) in enumerate(endpoints):
        choices = [
            (math.dist(point, other_point), candidate)
            for candidate, (other_owner, _other_side, other_point) in enumerate(endpoints)
            if other_owner != owner
        ]
        if choices:
            distance, candidate = min(choices)
            if distance <= max_gap:
                nearest[endpoint_index] = (distance, candidate)
    joins = []
    for endpoint_index, (distance, candidate) in nearest.items():
        if (
            endpoint_index < candidate and distance > 1e-7
            and nearest.get(candidate, (None, None))[1] == endpoint_index
        ):
            joins.append(LineString([
                endpoints[endpoint_index][2], endpoints[candidate][2]
            ]))
    return joins


def extract_chains(topography_path: Path, scope: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    document = ezdxf.readfile(topography_path)
    all_lines = []
    by_insert = []
    parts_total = joins_total = 0
    for insert in document.modelspace().query("INSERT"):
        if insert.dxf.layer != "Бортовой камень":
            continue
        parts = [
            line for line in (primitive_line(entity) for entity in insert.virtual_entities())
            if line is not None
        ]
        if not parts:
            continue
        joins = within_insert_joins(parts)
        geometry = unary_union(parts + joins)
        by_insert.append({
            "handle": insert.dxf.handle,
            "block": insert.dxf.name,
            "geometry": geometry,
            "parts": len(parts),
            "joins": len(joins),
        })
        all_lines.extend(parts + joins)
        parts_total += len(parts)
        joins_total += len(joins)
    merged = linemerge(unary_union(all_lines))
    geometries = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
    geometries = [line for line in geometries if line.intersects(scope.buffer(2.0))]
    geometries.sort(key=lambda line: (round(line.bounds[0], 6), round(line.bounds[1], 6), -line.length))
    chains = []
    for index, line in enumerate(geometries):
        source_inserts = sorted(
            row["handle"] for row in by_insert
            if row["geometry"].distance(line) <= 1e-7
            and row["geometry"].intersection(line.buffer(1e-7)).length > 1e-7
        )
        chains.append({
            "id": f"curb-chain:{index:04d}",
            "geometry": line,
            "source_insert_handles": source_inserts,
        })
    return chains, {
        "source_inserts": len(by_insert),
        "source_parts": parts_total,
        "within_insert_gap_joins": joins_total,
        "merged_chains_in_scope": len(chains),
        "maximum_join_gap_m": MAX_DASH_GAP_M,
    }


def interpolate(left: dict[str, Any], right: dict[str, Any], station: float, key: str) -> float:
    if not left["station_m"] - 1e-8 <= station <= right["station_m"] + 1e-8:
        raise ValueError("Curb height extrapolation is forbidden")
    ratio = (station-left["station_m"]) / max(right["station_m"]-left["station_m"], 1e-12)
    return left[key] + ratio*(right[key]-left[key])


def build(
    topography_path: Path,
    controls_path: Path,
    max_chain_distance_m: float = 0.30,
    minimum_chain_margin_m: float = 0.10,
    max_control_interval_m: float = 40.0,
) -> dict[str, Any]:
    controls_packet = json.loads(controls_path.read_text())
    if controls_packet["source"]["sha256"] != sha256(topography_path):
        raise ValueError("Elevation controls belong to a different topography source")
    surface_geojson = json.loads(Path(controls_packet["surface_source"]["path"]).read_text())
    surface_geometry = unary_union([
        shape(feature["geometry"]) for feature in surface_geojson["features"]
    ])
    scope = box(*surface_geometry.bounds)
    chains, source_audit = extract_chains(topography_path, scope)

    pairs: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for control in controls_packet["controls"]:
        if control["kind"] == "curb_pair_elevation":
            pairs[control["marker_handle"]][control["curb_role"]] = control
    controls_by_chain: dict[int, list[dict[str, Any]]] = defaultdict(list)
    association_rows = []
    for marker_handle, pair in sorted(pairs.items()):
        if set(pair) != {"lower", "upper"}:
            continue
        point = Point(pair["lower"]["xy"])
        if not scope.buffer(2.0).covers(point):
            continue
        ranked = sorted((row["geometry"].distance(point), index) for index, row in enumerate(chains))
        nearest_distance, chain_index = ranked[0]
        margin = ranked[1][0] - nearest_distance
        accepted = nearest_distance <= max_chain_distance_m and margin >= minimum_chain_margin_m
        association = {
            "marker_handle": marker_handle,
            "xy": pair["lower"]["xy"],
            "lower_z_m": pair["lower"]["z_m"],
            "upper_z_m": pair["upper"]["z_m"],
            "height_m": round(pair["upper"]["z_m"]-pair["lower"]["z_m"], 6),
            "chain_id": chains[chain_index]["id"],
            "chain_distance_m": round(nearest_distance, 9),
            "second_chain_margin_m": round(margin, 9),
            "status": "accepted_unique_curb_chain" if accepted else "rejected_chain_ambiguous_or_distant",
            "label_handles": sorted({
                label["label_handle"] for role in pair.values() for label in role["labels"]
            }),
        }
        if accepted:
            station = chains[chain_index]["geometry"].project(point)
            association["chain_station_m"] = round(station, 9)
            controls_by_chain[chain_index].append({**association, "station_m": station})
        association_rows.append(association)

    vertices = []
    vertex_index: dict[tuple[str, float, float, str], int] = {}
    faces = []
    intervals = []

    def add_vertex(chain_id: str, x: float, y: float, z: float, role: str) -> int:
        key = (chain_id, round(x, 8), round(y, 8), role)
        if key in vertex_index:
            index = vertex_index[key]
            if abs(vertices[index]["xyz"][2]-z) > 1e-4:
                raise ValueError(f"Curb mesh crack at {key}")
            return index
        index = len(vertices)
        vertex_index[key] = index
        vertices.append({
            "xyz": [key[1], key[2], round(z, 6)],
            "chain_id": chain_id,
            "curb_role": role,
            "height_status": "interpolated_between_source_annotation_pairs",
        })
        return index

    for chain_index, rows in sorted(controls_by_chain.items()):
        chain = chains[chain_index]
        line = chain["geometry"]
        ordered = sorted(rows, key=lambda row: row["station_m"])
        for left, right in zip(ordered, ordered[1:]):
            length = right["station_m"] - left["station_m"]
            if length <= 1e-6 or length > max_control_interval_m:
                continue
            samples = [(left["station_m"], line.interpolate(left["station_m"]))]
            for coordinate in line.coords:
                station = line.project(Point(coordinate))
                if left["station_m"] + 1e-7 < station < right["station_m"] - 1e-7:
                    samples.append((station, Point(coordinate)))
            samples.append((right["station_m"], line.interpolate(right["station_m"])))
            samples = sorted({round(station, 8): (station, point) for station, point in samples}.values())
            interval_faces = 0
            for (station_a, point_a), (station_b, point_b) in zip(samples, samples[1:]):
                lower_a = interpolate(left, right, station_a, "lower_z_m")
                lower_b = interpolate(left, right, station_b, "lower_z_m")
                upper_a = interpolate(left, right, station_a, "upper_z_m")
                upper_b = interpolate(left, right, station_b, "upper_z_m")
                face = [
                    add_vertex(chain["id"], point_a.x, point_a.y, lower_a, "lower"),
                    add_vertex(chain["id"], point_b.x, point_b.y, lower_b, "lower"),
                    add_vertex(chain["id"], point_b.x, point_b.y, upper_b, "upper"),
                    add_vertex(chain["id"], point_a.x, point_a.y, upper_a, "upper"),
                ]
                if len(set(face)) == 4:
                    faces.append(face)
                    interval_faces += 1
            intervals.append({
                "chain_id": chain["id"],
                "source_insert_handles": chain["source_insert_handles"],
                "start_marker": left["marker_handle"],
                "end_marker": right["marker_handle"],
                "station_range_m": [round(left["station_m"], 6), round(right["station_m"], 6)],
                "length_m": round(length, 6),
                "faces": interval_faces,
                "status": "interpolated_only_between_source_controls",
            })
    covered_length = sum(row["length_m"] for row in intervals)
    return {
        "schema": "green-atlas.source-curb-mesh.v1",
        "status": "source_breakline_curb_requires_pair_role_review",
        "inputs": {
            "topography": {"path": str(topography_path.resolve()), "sha256": sha256(topography_path)},
            "elevation_controls": {"path": str(controls_path.resolve()), "sha256": sha256(controls_path)},
        },
        "rules": {
            "within_insert_max_dash_gap_m": MAX_DASH_GAP_M,
            "max_control_to_chain_distance_m": max_chain_distance_m,
            "minimum_second_chain_margin_m": minimum_chain_margin_m,
            "max_control_interval_m": max_control_interval_m,
            "height_interpolation": "linear only between adjacent controls on same source chain",
            "road_width": "not inferred",
            "curb_cap_width": "not inferred",
        },
        "quality": {
            **source_audit,
            "pair_controls_in_scope": len(association_rows),
            "accepted_pair_controls": sum(row["status"].startswith("accepted") for row in association_rows),
            "chains_with_controls": len(controls_by_chain),
            "chains_with_mesh_intervals": len({row["chain_id"] for row in intervals}),
            "mesh_intervals": len(intervals),
            "covered_curb_length_m": round(covered_length, 6),
            "vertices": len(vertices),
            "faces": len(faces),
        },
        "associations": association_rows,
        "intervals": intervals,
        "vertices": vertices,
        "faces": faces,
        "limitations": [
            "The upper/lower numeric ordering is source-backed, while the semantic curb role still needs review.",
            "Only the vertical curb riser is built; no curb cap width or road surface is invented.",
            "No height extrapolation occurs before the first or after the last control on a chain.",
            "An admitted engineering TIN/breakline model supersedes this mesh when supplied.",
        ],
    }


def write_svg(mesh: dict[str, Any], output: Path) -> None:
    controls_packet = json.loads(Path(mesh["inputs"]["elevation_controls"]["path"]).read_text())
    surfaces = json.loads(Path(controls_packet["surface_source"]["path"]).read_text())
    surface_rows = [shape(feature["geometry"]) for feature in surfaces["features"]]
    surface_union = unary_union(surface_rows)
    if surface_union.is_empty:
        return
    min_x, min_y, max_x, max_y = surface_union.bounds
    width, height, margin = 1400, 980, 45
    scale = min((width-2*margin)/(max_x-min_x), (height-2*margin)/(max_y-min_y))

    def project(x: float, y: float) -> tuple[float, float]:
        return margin+(x-min_x)*scale, height-margin-(y-min_y)*scale

    interval_lines: dict[str, list[LineString]] = defaultdict(list)
    for face in mesh["faces"]:
        left, right = (mesh["vertices"][index] for index in face[:2])
        interval_lines[left["chain_id"]].append(LineString([
            left["xyz"][:2], right["xyz"][:2]
        ]))
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f1f3f2"/>',
        '<g stroke-linecap="round" stroke-linejoin="round">',
    ]
    background_colors = {
        "road": "#636a70", "sidewalk": "#d4cbc1", "lawn": "#a9bc91",
        "special_surface": "#c4a184", "stairs": "#b9b4a9", "unclassified": "#e1ddd4",
    }
    for feature in surfaces["features"]:
        geometry = shape(feature["geometry"])
        if geometry.geom_type != "Polygon":
            continue
        points = [project(*coordinate[:2]) for coordinate in geometry.exterior.coords]
        data = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z"
        color = background_colors.get(feature["properties"].get("class"), "#e1ddd4")
        svg.append(f'<path d="{data}" fill="{color}" fill-opacity="0.34" stroke="#737d7e" stroke-width="0.45"/>')
    for chain_id, rows in sorted(interval_lines.items()):
        merged = linemerge(unary_union(rows))
        geometries = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
        for geometry in geometries:
            points = [project(*coordinate) for coordinate in geometry.coords]
            data = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points)
            svg.append(f'<path d="{data}" fill="none" stroke="#117d8a" stroke-width="4"/>')
    svg.append('</g><g>')
    for row in mesh["associations"]:
        x, y = project(*row["xy"])
        color = "#237447" if row["status"].startswith("accepted") else "#b94737"
        svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#fff" stroke="{color}" stroke-width="2"/>')
    quality = mesh["quality"]
    svg.extend([
        '<rect x="28" y="24" width="770" height="112" rx="8" fill="#ffffff" fill-opacity="0.95"/>',
        '<text x="48" y="55" font-family="Arial" font-size="22" font-weight="700" fill="#172426">3D-борт из исходных breaklines и пар высот</text>',
        f'<text x="48" y="84" font-family="Arial" font-size="15" fill="#435255">{quality["accepted_pair_controls"]} контролей · {quality["covered_curb_length_m"]:.1f} м борта · {quality["faces"]} faces</text>',
        '<text x="48" y="110" font-family="Arial" font-size="14" fill="#9a3c31">Только вертикальная грань; ширина борта и дороги не придуманы.</text>',
        '</g></svg>',
    ])
    target = output / "curb-coverage.svg"
    target.write_text("\n".join(svg) + "\n")
    converter = shutil.which("rsvg-convert")
    if converter:
        subprocess.run([converter, "-o", str(output / "curb-coverage.png"), str(target)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topography", type=Path, default=DEFAULT_TOPOGRAPHY)
    parser.add_argument("--controls", type=Path, default=DEFAULT_CONTROLS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    mesh = build(args.topography, args.controls)
    target = args.output / "curb-mesh.json"
    target.write_text(json.dumps(mesh, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_svg(mesh, args.output)
    receipt = {
        "schema": "green-atlas.source-curb-mesh-receipt.v1",
        "status": mesh["status"],
        "mesh": {"path": str(target.resolve()), "bytes": target.stat().st_size, "sha256": sha256(target)},
        "quality": mesh["quality"],
    }
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
