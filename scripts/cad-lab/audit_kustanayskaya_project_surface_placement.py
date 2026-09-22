"""Audit inventory points against exact authored project HATCH surfaces."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import ezdxf
from ezdxf import bbox as ezdxf_bbox
from ezdxf.path import make_path
from shapely.geometry import LineString, Point, box, shape
from shapely.ops import unary_union


SURFACE_COLORS = {
    "road": "#686f72",
    "sidewalk": "#d5d0c5",
    "lawn": "#91bc80",
    "special_surface": "#c8a76a",
    "stairs": "#aaa59a",
    "unclassified": "#d95d4f",
}
POINT_COLORS = {
    "lawn": "#176d3e",
    "road": "#e33e38",
    "sidewalk": "#7b4ca5",
    "special_surface": "#9b6700",
    "stairs": "#725a49",
    "unclassified": "#c33c52",
    "unknown": "#2475a7",
}
PRIORITY = ("road", "sidewalk", "stairs", "special_surface", "lawn", "unclassified")
CURB_LAYERS = {
    "ДВ_Борт_БР100.30.15": "curb_standard",
    "ДВ_ГП_П_Борт_Пониженный": "curb_lowered",
    "ДВ_Борт_БР100.45.18": "curb_heavy",
}
DETAIL_COLORS = {
    "curb_standard": "#202a27",
    "curb_lowered": "#d67b18",
    "curb_heavy": "#6d3128",
    "bench": "#5d3929",
    "waste_basket": "#702f52",
    "transit_shelter": "#236e87",
}


def polygon_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [polygon[0] for polygon in geometry["coordinates"]]
    return []


def extract_project_details(path: Path, scope: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    document = ezdxf.readfile(path)
    modelspace = document.modelspace()
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for entity in modelspace.query("LWPOLYLINE"):
        detail_class = CURB_LAYERS.get(entity.dxf.layer)
        if detail_class is None:
            continue
        coordinates = [list(point)[:2] for point in make_path(entity).flattening(0.04)]
        if len(coordinates) < 2:
            continue
        line = LineString(coordinates)
        if not line.intersects(scope):
            continue
        rows.append({
            "kind": "line",
            "class": detail_class,
            "source_layer": entity.dxf.layer,
            "source_handle": entity.dxf.handle,
            "geometry": line,
        })
        counts[detail_class] += 1
    for entity in modelspace.query("INSERT"):
        detail_class = None
        block = entity.dxf.name
        if entity.dxf.layer == "ДВ_ГП_П_МАФ":
            if block == "лавочка 1":
                detail_class = "bench"
            elif block == "урна2":
                detail_class = "waste_basket"
        elif entity.dxf.layer == "ДВ_ГП_П_Павильон_ООТ":
            detail_class = "transit_shelter"
        if detail_class is None:
            continue
        # Several source blocks have a remote internal base point, so the raw
        # INSERT coordinate is not the visible object location.  The extents
        # centre is derived from transformed block geometry in WCS.
        extents = ezdxf_bbox.extents([entity], fast=True)
        if not extents.has_data:
            continue
        centre = extents.center
        point = Point(float(centre.x), float(centre.y))
        if not scope.covers(point):
            continue
        rows.append({
            "kind": "point",
            "class": detail_class,
            "source_layer": entity.dxf.layer,
            "source_handle": entity.dxf.handle,
            "source_block": block,
            "rotation_deg": float(entity.dxf.rotation or 0.0),
            "geometry": point,
        })
        counts[detail_class] += 1
    return rows, dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--surfaces", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--project-dxf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    world = json.loads(args.world.read_text())
    source = json.loads(args.surfaces.read_text())
    alignment = json.loads(args.alignment.read_text())

    by_class: dict[str, list[Any]] = defaultdict(list)
    for feature in source["features"]:
        by_class[feature["properties"]["class"]].append(shape(feature["geometry"]))
    unions = {key: unary_union(value) for key, value in by_class.items()}
    surface_scope = box(*unary_union([shape(feature["geometry"]) for feature in source["features"]]).bounds)
    project_details: list[dict[str, Any]] = []
    project_detail_counts: dict[str, int] = {}
    if args.project_dxf:
        project_details, project_detail_counts = extract_project_details(args.project_dxf, surface_scope)

    counts: Counter[str] = Counter()
    details: Counter[tuple[str, str, str]] = Counter()
    records = []
    for row in world["inventory_vegetation"]:
        point = Point(row["xy_dxf_m"])
        hits = [key for key in PRIORITY if key in unions and unions[key].covers(point)]
        primary = hits[0] if hits else "unknown"
        counts[primary] += 1
        details[(primary, row["kind"], row["position_status"])] += 1
        records.append({
            "inventory_id": int(row["inventory_id"]),
            "species": row["species"],
            "kind": row["kind"],
            "count": int(row["count"]),
            "xy_dxf_m": row["xy_dxf_m"],
            "position_wgs84": row["position_wgs84"],
            "position_status": row["position_status"],
            "project_surface": primary,
            "all_project_surface_hits": hits,
            "automatic_relocation": False,
        })

    report = {
        "schema": "green-atlas.project-surface-placement-audit.v1",
        "status": "source_project_surface_comparison_no_mutation",
        "counts": dict(counts),
        "detail_counts": [
            {"surface": key[0], "kind": key[1], "position_status": key[2], "count": value}
            for key, value in sorted(details.items())
        ],
        "source_semantics": {
            "surface_features": len(source["features"]),
            "classes": dict(Counter(feature["properties"]["class"] for feature in source["features"])),
            "position_source": "inventory DXF",
            "surface_source": "03_10004141_Project_solutions exact HATCH",
            "project_detail_source": str(args.project_dxf.resolve()) if args.project_dxf else None,
            "project_detail_counts": project_detail_counts,
            "automatic_relocation": False,
        },
        "interpretation": [
            "The project surface classification is more authoritative for this design than OSM class-width buffers.",
            "A shrub-group label fallback is not an exact group footprint and remains review-only.",
            "A tree crown may visually overlap road or sidewalk while its source trunk point remains on lawn.",
            "Existing inventory is not the same as the final proposed planting schedule.",
        ],
        "records": records,
    }
    report_path = args.output / "project-surface-placement-audit.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    xs = [float(row["xy_dxf_m"][0]) for row in world["inventory_vegetation"]]
    ys = [float(row["xy_dxf_m"][1]) for row in world["inventory_vegetation"]]
    min_x, min_y, max_x, max_y = min(xs) - 20, min(ys) - 20, max(xs) + 20, max(ys) + 20
    width, height, margin = 1150, 1900, 55
    scale = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))
    ox = (width - (max_x - min_x) * scale) * 0.5
    oy = (height - (max_y - min_y) * scale) * 0.5

    def screen(x: float, y: float) -> tuple[float, float]:
        return ox + (x - min_x) * scale, height - oy - (y - min_y) * scale

    def path(ring: list[list[float]]) -> str:
        return "M" + " L".join(
            f"{x:.2f},{y:.2f}" for x, y in (screen(float(p[0]), float(p[1])) for p in ring)
        ) + " Z"

    def line_path(line: Any) -> str:
        return "M" + " L".join(
            f"{x:.2f},{y:.2f}" for x, y in (screen(float(p[0]), float(p[1])) for p in line.coords)
        )

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f4f3ef"/>',
        '<g stroke-linejoin="round">',
    ]
    for surface_class in ("road", "sidewalk", "special_surface", "stairs", "lawn", "unclassified"):
        for feature in source["features"]:
            if feature["properties"]["class"] != surface_class:
                continue
            for ring in polygon_rings(feature["geometry"]):
                svg.append(
                    f'<path d="{path(ring)}" fill="{SURFACE_COLORS[surface_class]}" stroke="#3e4945" stroke-width="0.35"/>'
                )
    for row in project_details:
        if row["kind"] != "line":
            continue
        svg.append(
            f'<path d="{line_path(row["geometry"])}" fill="none" stroke="{DETAIL_COLORS[row["class"]]}" stroke-width="{1.15 if row["class"] == "curb_standard" else 1.65}"/>'
        )
    for row in project_details:
        if row["kind"] != "point":
            continue
        x, y = screen(row["geometry"].x, row["geometry"].y)
        svg.append(
            f'<rect x="{x-2.4:.2f}" y="{y-2.4:.2f}" width="4.8" height="4.8" fill="{DETAIL_COLORS[row["class"]]}" stroke="#fff" stroke-width="0.5"/>'
        )
    for row in records:
        x, y = screen(float(row["xy_dxf_m"][0]), float(row["xy_dxf_m"][1]))
        ring = row["position_status"] != "matched_marker"
        svg.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{2.7 if row["kind"] == "tree" else 2.2}" fill="{POINT_COLORS[row["project_surface"]]}" stroke="{"#ffffff" if not ring else "#111111"}" stroke-width="{0.45 if not ring else 1.0}"/>'
        )
    svg.extend([
        '</g>',
        '<rect x="22" y="18" width="740" height="190" rx="9" fill="#ffffff" fill-opacity="0.95"/>',
        '<text x="42" y="51" font-family="Arial" font-size="24" font-weight="700">Целевая поверхность проекта + существующая инвентаризация</text>',
        f'<text x="42" y="82" font-family="Arial" font-size="16">Газон: {counts["lawn"]} · дорога: {counts["road"]} · тротуар: {counts["sidewalk"]} · спецпокрытие: {counts["special_surface"]} · вне HATCH: {counts["unknown"]}</text>',
        '<text x="42" y="112" font-family="Arial" font-size="15" fill="#4c5853">Чёрная обводка точки = позиция требует проверки маркера/подписи.</text>',
        '<text x="42" y="140" font-family="Arial" font-size="15" fill="#4c5853">Положение не исправлялось и не привязывалось автоматически.</text>',
        '<circle cx="48" cy="171" r="6" fill="#176d3e"/><text x="62" y="176" font-family="Arial" font-size="15">на газоне</text>',
        '<circle cx="168" cy="171" r="6" fill="#e33e38"/><text x="182" y="176" font-family="Arial" font-size="15">на дороге</text>',
        '<circle cx="282" cy="171" r="6" fill="#7b4ca5"/><text x="296" y="176" font-family="Arial" font-size="15">на тротуаре</text>',
        '<circle cx="422" cy="171" r="6" fill="#2475a7"/><text x="436" y="176" font-family="Arial" font-size="15">вне поверхности</text>',
        '<line x1="565" y1="171" x2="596" y2="171" stroke="#202a27" stroke-width="3"/><text x="604" y="176" font-family="Arial" font-size="15">борт</text>',
        '</svg>',
    ])
    svg_path = args.output / "project-target-placement-map.svg"
    png_path = args.output / "project-target-placement-map.png"
    svg_path.write_text("\n".join(svg))
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-o", str(png_path), str(svg_path)], check=True)
    else:
        subprocess.run(["magick", str(svg_path), str(png_path)], check=True)

    transform = alignment["similarity_transform_row_vector"]
    rotation = np.asarray(transform["rotation"], dtype=float)
    inverse_rotation = np.linalg.inv(rotation)
    translation = np.asarray(transform["translation"], dtype=float)
    scale_value = float(transform["scale"])
    lon0, lat0 = alignment["projection_before_fit"]["reference_lon_lat"]
    radius = float(alignment["projection_before_fit"]["earth_radius_m"])

    def dxf_lonlat(x: float, y: float) -> list[float]:
        tangent = (np.asarray([x, y], dtype=float) - translation) @ inverse_rotation / scale_value
        return [
            lon0 + math.degrees(tangent[0] / (radius * math.cos(math.radians(lat0)))),
            lat0 + math.degrees(tangent[1] / radius),
        ]

    def transform_geometry(geometry: dict[str, Any]) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
                return dxf_lonlat(float(value[0]), float(value[1]))
            if isinstance(value, list):
                return [convert(child) for child in value]
            return value
        return {"type": geometry["type"], "coordinates": convert(geometry["coordinates"])}

    features = [{
        "type": "Feature",
        "geometry": transform_geometry(feature["geometry"]),
        "properties": {"layer": "project_surface", "class": feature["properties"]["class"], "source_layer": feature["properties"]["source_layer"]},
    } for feature in source["features"]]
    features += [{
        "type": "Feature",
        "geometry": (
            {"type": "LineString", "coordinates": [dxf_lonlat(float(x), float(y)) for x, y in row["geometry"].coords]}
            if row["kind"] == "line"
            else {"type": "Point", "coordinates": dxf_lonlat(row["geometry"].x, row["geometry"].y)}
        ),
        "properties": {
            "layer": "project_detail",
            "class": row["class"],
            "source_layer": row["source_layer"],
            "source_handle": row["source_handle"],
            **({"source_block": row["source_block"], "rotation_deg": row["rotation_deg"]} if row["kind"] == "point" else {}),
        },
    } for row in project_details]
    features += [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": row["position_wgs84"]},
        "properties": {key: value for key, value in row.items() if key not in {"position_wgs84", "xy_dxf_m"}},
    } for row in records]
    geojson = {"type": "FeatureCollection", "features": features}
    geojson_path = args.output / "project-target-placement-map.geojson"
    geojson_path.write_text(json.dumps(geojson, ensure_ascii=False) + "\n")

    map_json = json.dumps(geojson, ensure_ascii=False)
    html = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Кустанайская — целевые поверхности</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"><style>html,body,#map{{height:100%;margin:0}}.panel{{position:absolute;z-index:1000;top:12px;left:52px;background:#fff;padding:12px 15px;border-radius:8px;box-shadow:0 2px 14px #0003;font:14px Arial;max-width:480px}}.panel b{{font-size:17px}}</style></head><body><div id="map"></div><div class="panel"><b>Проектные поверхности + существующая инвентаризация</b><br>Газон {counts['lawn']} · дорога {counts['road']} · тротуар {counts['sidewalk']} · вне HATCH {counts['unknown']}<br>Положение точек не изменялось.</div><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={map_json};const sc={json.dumps(SURFACE_COLORS)};const pc={json.dumps(POINT_COLORS)};const dc={json.dumps(DETAIL_COLORS)};const map=L.map('map').setView([55.61877,37.75213],17);L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:20,attribution:'© OpenStreetMap contributors'}}).addTo(map);const surfaces=L.layerGroup().addTo(map),details=L.layerGroup().addTo(map),plants=L.layerGroup().addTo(map);data.features.forEach(f=>{{const p=f.properties;if(p.layer==='project_detail'){{if(f.geometry.type==='Point'){{const m=L.circleMarker([f.geometry.coordinates[1],f.geometry.coordinates[0]],{{radius:4,color:'#fff',weight:1,fillColor:dc[p.class],fillOpacity:1}});m.bindPopup(`<b>${{p.class}}</b><br>${{p.source_layer}}<br>блок: ${{p.source_block||'—'}}`);m.addTo(details)}}else{{L.geoJSON(f,{{style:{{color:dc[p.class],weight:p.class==='curb_standard'?2:3,opacity:.95}}}}).addTo(details)}}}}else if(f.geometry.type==='Point'){{const m=L.circleMarker([f.geometry.coordinates[1],f.geometry.coordinates[0]],{{radius:4,color:p.position_status==='matched_marker'?'#fff':'#111',weight:p.position_status==='matched_marker'?1:2,fillColor:pc[p.project_surface],fillOpacity:.95}});m.bindPopup(`<b>№${{p.inventory_id}} — ${{p.species}}</b><br>${{p.kind}}, количество ${{p.count}}<br>проектная поверхность: ${{p.project_surface}}<br>позиция: ${{p.position_status}}<br>автоперенос: нет`);m.addTo(plants)}}else{{L.geoJSON(f,{{style:{{color:'#3e4945',weight:.5,fillColor:sc[p.class],fillOpacity:.46}}}}).addTo(surfaces)}}}});L.control.layers(null,{{'Точные проектные поверхности':surfaces,'Борта и проектные объекты':details,'Инвентаризация':plants}},{{collapsed:false}}).addTo(map);map.fitBounds([[{world['scope']['context_bbox_wgs84'][1]},{world['scope']['context_bbox_wgs84'][0]}],[{world['scope']['context_bbox_wgs84'][3]},{world['scope']['context_bbox_wgs84'][2]}]]);</script></body></html>'''
    html_path = args.output / "project-target-placement-map.html"
    html_path.write_text(html)
    print(json.dumps({
        "report": str(report_path.resolve()),
        "png": str(png_path.resolve()),
        "geojson": str(geojson_path.resolve()),
        "interactive_map": str(html_path.resolve()),
        "counts": dict(counts),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
