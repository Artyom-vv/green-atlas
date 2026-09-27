"""Prepare a compact source-status audit without filling unknown territory."""
from __future__ import annotations

import html
import json
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.disassemble import recursive_decompose
from ezdxf.path import make_path
from shapely.geometry import LineString, Point, Polygon, box


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".runtime/geometry-audit-20260919"
BOUNDS = (15838.0, -4975.0, 15891.0, -4932.0)
WIDTH, HEIGHT = 1325, 1075
MARGIN = 70


def clipped_line(entity, bounds):
    if entity.dxftype() == "LINE":
        coordinates = [tuple(entity.dxf.start)[:2], tuple(entity.dxf.end)[:2]]
    else:
        coordinates = [tuple(point)[:2] for point in make_path(entity).flattening(0.04)]
    if len(coordinates) < 2:
        return []
    clipped = LineString(coordinates).intersection(bounds)
    if clipped.is_empty:
        return []
    return [clipped] if clipped.geom_type == "LineString" else [g for g in clipped.geoms if g.geom_type == "LineString"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    context = json.loads((ROOT / ".runtime/terrain-context-road-20260918/context.json").read_text())
    trees = json.loads((ROOT / ".runtime/cad-vegetation-20260919/trees.json").read_text())
    terrain = json.loads((ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json").read_text())
    source = Path(context["source"])
    doc = ezdxf.readfile(source)
    extent = box(*BOUNDS)
    road = Polygon(terrain["boundary"])
    layers = {
        "Бортовой камень",
        "Откосы",
        "Граница растительности и грунта",
        "Граница улицы",
        "Здания",
        "Крыльца",
        "Ограды",
        "Леса и газоны",
    }
    lines = []
    for entity in recursive_decompose(doc.modelspace()):
        if entity.dxf.layer not in layers or entity.dxftype() not in {
            "LINE", "LWPOLYLINE", "ARC", "CIRCLE", "ELLIPSE"
        }:
            continue
        try:
            if not bbox.extents([entity], fast=True).has_data:
                continue
            for part in clipped_line(entity, extent):
                lines.append({"layer": entity.dxf.layer, "xy": list(part.coords)})
        except (TypeError, ValueError):
            continue

    selected_trees = [
        tree for tree in trees["matched"]
        if extent.covers(Point(tree["xy"]))
    ]
    conflicts = [tree["number"] for tree in selected_trees if road.covers(Point(tree["xy"]))]
    rejected_context_road = Polygon(context["zones"][0]["xy"])
    rejected_context_conflicts = [
        tree["number"] for tree in selected_trees
        if rejected_context_road.covers(Point(tree["xy"])) and tree["number"] not in conflicts
    ]
    packet = {
        "bbox": BOUNDS,
        "source": str(source),
        "source_lines": lines,
        "terrain": terrain,
        "trees": selected_trees,
        "conflicting_tree_numbers": conflicts,
        "rejected_context_conflicting_tree_numbers": rejected_context_conflicts,
        "statuses": {
            "road": "manual road-side hypothesis with source elevation annotations",
            "linework": "source topography DXF clipped to audit bbox",
            "trees": "IP DXF XY plus nearest inventory annotation hypothesis",
            "unknown": "not modelled and not filled",
        },
    }
    (OUT / "geometry-audit.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2))

    scale = min((WIDTH - 2 * MARGIN) / (BOUNDS[2] - BOUNDS[0]),
                (HEIGHT - 2 * MARGIN) / (BOUNDS[3] - BOUNDS[1]))

    def project(x, y):
        return (MARGIN + (x - BOUNDS[0]) * scale,
                HEIGHT - MARGIN - (y - BOUNDS[1]) * scale)

    def path(points, stroke, width, dash="", fill="none"):
        values = [project(*point) for point in points]
        d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in values)
        return f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" {dash}/>'

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        '<rect width="100%" height="100%" fill="#f5f3ed"/>',
        '<g font-family="Arial, sans-serif">',
        '<text x="70" y="42" font-size="25" font-weight="bold" fill="#1f2d29">Геометрическая проверка · компактный фрагмент</text>',
        '<text x="70" y="67" font-size="15" fill="#51605b">Белое поле = поверхность не восстановлена и не заполнена</text>',
    ]
    bx0, by0 = project(BOUNDS[0], BOUNDS[1])
    bx1, by1 = project(BOUNDS[2], BOUNDS[3])
    svg.append(f'<rect x="{bx0}" y="{by1}" width="{bx1-bx0}" height="{by0-by1}" fill="#fff" stroke="#a4aaa6"/>')
    road_points = terrain["boundary"]
    svg.append(path(road_points, "#087b78", 3, fill="#bde4df"))
    layer_styles = {
        "Бортовой камень": ("#273934", 2.2),
        "Откосы": ("#8b7654", 1.4),
        "Граница растительности и грунта": ("#6a884e", 1.7),
        "Граница улицы": ("#7a6f82", 1.4),
        "Здания": ("#4f5e66", 1.6),
        "Крыльца": ("#78848b", 1.2),
        "Ограды": ("#946e50", 1.2),
        "Леса и газоны": ("#91a66e", 1.1),
    }
    for line in lines:
        color, width = layer_styles[line["layer"]]
        svg.append(path(line["xy"], color, width))
    for vertex in terrain["vertices"]:
        x, y = project(*vertex["xyz"][:2])
        svg.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#087b78"/>')
        svg.append(f'<text x="{x+7}" y="{y-7}" font-size="12" fill="#075c5a">{html.escape(vertex["picket"])} · {vertex["xyz"][2]:.2f}</text>')
    for tree in selected_trees:
        x, y = project(*tree["xy"])
        conflict = tree["number"] in conflicts
        rejected_conflict = tree["number"] in rejected_context_conflicts
        color = "#c28724" if rejected_conflict else "#d6452d" if conflict else "#376b35"
        svg.append(f'<circle cx="{x}" cy="{y}" r="8" fill="#fff" stroke="{color}" stroke-width="3"/>')
        suffix = " · конфликт старой обводки" if rejected_conflict else " · АКТИВНЫЙ КОНФЛИКТ" if conflict else ""
        svg.append(f'<text x="{x+11}" y="{y+5}" font-size="13" font-weight="bold" fill="{color}">{tree["number"]} · {html.escape(tree["species"])} {tree["height_m"]:g}м{suffix}</text>')
    svg.extend([
        '<rect x="70" y="920" width="1185" height="105" rx="9" fill="#eef0ec"/>',
        '<text x="90" y="949" font-size="14" fill="#273934">Бирюзовый — единственная поверхность с собранными отметками; её граница всё ещё гипотеза.</text>',
        '<text x="90" y="977" font-size="14" fill="#273934">Тёмные линии — исходный DXF. Зелёные окружности — деревья. Охра — конфликт только с отброшенной общей обводкой.</text>',
        f'<text x="90" y="1005" font-size="14" fill="#273934">Источник: {html.escape(source.name)} · деревьев {len(selected_trees)} · активных конфликтов {len(conflicts)}</text>',
        '</g></svg>',
    ])
    (OUT / "geometry-audit.svg").write_text("\n".join(svg))
    print(json.dumps({"lines": len(lines), "trees": len(selected_trees), "conflicts": conflicts,
                      "rejected_context_conflicts": rejected_context_conflicts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
