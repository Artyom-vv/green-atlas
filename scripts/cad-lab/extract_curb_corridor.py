"""Recover two source curb chains and the clipped corridor between them.

The source curb uses dashed LINE primitives inside INSERTs. Dash gaps are
bridged only inside the same INSERT. The corridor is closed by the audit crop,
so those closing edges are explicitly not source road boundaries.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import ezdxf
from ezdxf.path import make_path
from shapely.geometry import LineString, Point, Polygon, box, mapping
from shapely.ops import linemerge, polygonize, unary_union


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / ".runtime/geometry-audit-20260919/geometry-audit.json"
OUT = ROOT / ".runtime/curb-corridor-audit-20260919"
MAX_DASH_GAP = 0.55


def connect(parts):
    endpoints = [(i, side, tuple(part[side])) for i, part in enumerate(parts) for side in (0, -1)]
    nearest = {}
    for index, (owner, _side, point) in enumerate(endpoints):
        choices = [
            (math.dist(point, other_point), candidate)
            for candidate, (other_owner, _other_side, other_point) in enumerate(endpoints)
            if owner != other_owner
        ]
        if choices:
            distance, candidate = min(choices)
            if distance <= MAX_DASH_GAP:
                nearest[index] = (distance, candidate)
    joins = []
    for index, (distance, candidate) in nearest.items():
        if index < candidate and nearest.get(candidate, (None, None))[1] == index and distance > 1e-7:
            joins.append({
                "start": endpoints[index][2],
                "end": endpoints[candidate][2],
                "gap_m": distance,
                "part_indices": [endpoints[index][0], endpoints[candidate][0]],
            })
    return joins


def primitive_xy(entity):
    if entity.dxftype() == "LINE":
        return [tuple(entity.dxf.start)[:2], tuple(entity.dxf.end)[:2]]
    if entity.dxftype() == "LWPOLYLINE":
        return [tuple(point)[:2] for point in make_path(entity).flattening(0.02)]
    return []


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    audit = json.loads(AUDIT.read_text())
    extent = box(*audit["bbox"])
    document = ezdxf.readfile(audit["source"])
    chains = []
    all_source_parts = []
    all_joins = []
    for insert in document.modelspace().query("INSERT"):
        if insert.dxf.layer != "Бортовой камень":
            continue
        parts = []
        for entity in insert.virtual_entities():
            coordinates = primitive_xy(entity)
            if len(coordinates) >= 2 and LineString(coordinates).intersects(extent.buffer(3)):
                parts.append(coordinates)
        if not parts:
            continue
        joins = connect(parts)
        merged = linemerge(unary_union(
            [LineString(part) for part in parts]
            + [LineString([join["start"], join["end"]]) for join in joins]
        ))
        if merged.geom_type != "LineString":
            raise RuntimeError(f"Curb INSERT {insert.dxf.handle} did not form one chain")
        clipped = merged.intersection(extent)
        if clipped.geom_type != "LineString":
            raise RuntimeError(f"Curb INSERT {insert.dxf.handle} did not clip to one chain")
        chain = {
            "source_insert": insert.dxf.handle,
            "block": insert.dxf.name,
            "part_count": len(parts),
            "join_count": len(joins),
            "join_gap_min_m": min(join["gap_m"] for join in joins),
            "join_gap_max_m": max(join["gap_m"] for join in joins),
            "length_in_crop_m": clipped.length,
            "xy": list(clipped.coords),
            "status": "source dashes plus within-INSERT inferred dash-gap joins",
        }
        chains.append(chain)
        all_source_parts.extend(parts)
        all_joins.extend({**join, "source_insert": insert.dxf.handle} for join in joins)

    if len(chains) != 2:
        raise RuntimeError(f"Expected two curb chains in audit crop, got {len(chains)}")
    faces = list(polygonize(unary_union(
        [LineString(chain["xy"]) for chain in chains] + [extent.boundary]
    )))
    previous_road = Polygon(audit["terrain"]["boundary"])
    corridor_candidates = [face for face in faces if face.covers(previous_road.centroid)]
    if len(corridor_candidates) != 1:
        raise RuntimeError(f"Expected one corridor around prior road centroid, got {len(corridor_candidates)}")
    corridor = corridor_candidates[0]
    overlap = corridor.intersection(previous_road).area / previous_road.area
    trees = []
    for tree in audit["trees"]:
        trees.append({**tree, "inside_curb_corridor": corridor.covers(Point(tree["xy"]))})
    receipt = {
        "bbox": audit["bbox"],
        "source": audit["source"],
        "chains": chains,
        "inferred_dash_gap_joins": all_joins,
        "corridor": mapping(corridor),
        "corridor_area_m2": corridor.area,
        "closure_status": "two source-derived curb chains plus audit bbox boundary",
        "previous_road_area_m2": previous_road.area,
        "previous_road_overlap_ratio": overlap,
        "tree_positions": trees,
        "limitations": [
            "The two crop-closing edges belong to the audit bbox, not the source road.",
            "Dash-gap joins are inferred within each original INSERT; primitives are not moved.",
            "The prior manual road surface is used only to choose the middle polygonized face.",
            "No elevations are assigned outside the prior eight-point terrain trial.",
        ],
    }
    (OUT / "curb-corridor.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))

    width, height, margin = 1325, 1075, 70
    x0, y0, x1, y1 = audit["bbox"]
    scale = min((width-2*margin)/(x1-x0), (height-2*margin)/(y1-y0))

    def project(x, y):
        return margin+(x-x0)*scale, height-margin-(y-y0)*scale

    def path(points, stroke, stroke_width, fill="none", dash=""):
        projected = [project(*point) for point in points]
        data = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in projected)
        return f'<path d="{data}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" {dash}/>'

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f5f3ed"/>',
        '<g font-family="Arial, sans-serif">',
        '<text x="70" y="42" font-size="25" font-weight="bold" fill="#1f2d29">Бортовой камень · восстановление штриховых цепочек</text>',
        '<text x="70" y="67" font-size="15" fill="#51605b">Заливка замкнута границей окна и действительна только как фрагмент дорожного коридора</text>',
    ]
    corridor_xy = list(corridor.exterior.coords)
    svg.append(path(corridor_xy, "#16817e", 2.4, fill="#d7ebe8"))
    svg.append(path(audit["terrain"]["boundary"], "#8b4c8c", 2, dash='stroke-dasharray="8 6"'))
    for part in all_source_parts:
        clipped = LineString(part).intersection(extent)
        if clipped.is_empty:
            continue
        geometries = [clipped] if clipped.geom_type == "LineString" else list(clipped.geoms)
        for geometry in geometries:
            if geometry.geom_type == "LineString":
                svg.append(path(list(geometry.coords), "#283b38", 2.2))
    for join in all_joins:
        segment = LineString([join["start"], join["end"]]).intersection(extent)
        if not segment.is_empty and segment.geom_type == "LineString":
            svg.append(path(list(segment.coords), "#d07827", 2.2))
    for tree in trees:
        x, y = project(*tree["xy"])
        color = "#c4412f" if tree["inside_curb_corridor"] else "#376b35"
        svg.append(f'<circle cx="{x}" cy="{y}" r="7" fill="#fff" stroke="{color}" stroke-width="3"/>')
        svg.append(f'<text x="{x+10}" y="{y+5}" font-size="13" font-weight="bold" fill="{color}">{tree["number"]}</text>')
    svg.extend([
        '<rect x="70" y="920" width="1185" height="105" rx="9" fill="#eef0ec"/>',
        '<text x="90" y="949" font-size="14" fill="#273934">Тёмное — исходные штрихи бордюра. Оранжевое — восстановленные разрывы около 0,5 м внутри того же INSERT.</text>',
        '<text x="90" y="977" font-size="14" fill="#273934">Бирюзовый — коридор между двумя цепочками. Фиолетовый пунктир — прежняя ручная поверхность для сравнения.</text>',
        f'<text x="90" y="1005" font-size="14" fill="#273934">Площадь фрагмента {corridor.area:.1f} м² · прежняя {previous_road.area:.1f} м² · покрытие прежней поверхности {overlap*100:.1f}%</text>',
        '</g></svg>',
    ])
    (OUT / "curb-corridor.svg").write_text("\n".join(svg))
    print(json.dumps({
        "chains": [(chain["source_insert"], chain["part_count"], chain["join_count"]) for chain in chains],
        "corridor_area_m2": corridor.area,
        "previous_overlap_ratio": overlap,
        "trees_inside": [tree["number"] for tree in trees if tree["inside_curb_corridor"]],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
