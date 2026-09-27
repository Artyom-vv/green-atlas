"""Classify inventory placement against the current GIS world and map it."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union


COLORS = {
    "building_conflict": "#c92d36",
    "vehicle_surface_review": "#ef7d23",
    "pedestrian_surface_review": "#7b4ca5",
    "mapped_green": "#2f8b57",
    "unclassified_surface": "#2475a7",
}


def polygon_rings(geometry: dict[str, Any]) -> list[list[list[float]]]:
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
    args.output.mkdir(parents=True, exist_ok=True)
    world = json.loads(args.world.read_text())

    vehicle_rows = [
        road for road in world["roads"]
        if road["properties"].get("class") not in {"footway", "steps"}
    ]
    pedestrian_rows = [
        road for road in world["roads"]
        if road["properties"].get("class") in {"footway", "steps"}
    ]
    vehicle = unary_union([shape(row["surface_geometry_local"]) for row in vehicle_rows])
    pedestrian = unary_union([shape(row["surface_geometry_local"]) for row in pedestrian_rows])
    green = unary_union([shape(row["geometry_local"]) for row in world["green_areas"]])
    buildings = unary_union([shape(row["geometry_local"]) for row in world["buildings"]])

    counts: Counter[str] = Counter()
    position_counts: Counter[str] = Counter()
    classified: list[dict[str, Any]] = []
    for row in world["inventory_vegetation"]:
        x, y = (float(value) for value in row["position_local_xyz_m"][:2])
        point = Point(x, y)
        hits = {
            "building": buildings.covers(point),
            "vehicle": vehicle.covers(point),
            "pedestrian": pedestrian.covers(point),
            "green": green.covers(point),
        }
        if hits["building"]:
            placement = "building_conflict"
        elif hits["vehicle"]:
            placement = "vehicle_surface_review"
        elif hits["pedestrian"]:
            placement = "pedestrian_surface_review"
        elif hits["green"]:
            placement = "mapped_green"
        else:
            placement = "unclassified_surface"

        height = float(row.get("height_m") or 4.0)
        if row["kind"] == "tree":
            visual_radius = min(max(height * 0.24, 1.2), 5.5)
        elif row["kind"] == "shrub_group":
            visual_radius = min(max(0.55 + math.sqrt(max(int(row["count"]), 1)) * 0.16, 0.65), 4.5)
        else:
            visual_radius = 0.25
        footprint = point.buffer(visual_radius)
        crown_over_vehicle = footprint.intersects(vehicle) and not hits["vehicle"]
        crown_over_pedestrian = footprint.intersects(pedestrian) and not hits["pedestrian"]

        distance_to_vehicle = float(vehicle.distance(point)) if not hits["vehicle"] else 0.0
        counts[placement] += 1
        position_counts[row["position_status"]] += 1
        if crown_over_vehicle:
            counts["visual_crown_over_vehicle_trunk_elsewhere"] += 1
        if crown_over_pedestrian:
            counts["visual_crown_over_pedestrian_trunk_elsewhere"] += 1
        classified.append(
            {
                "inventory_id": int(row["inventory_id"]),
                "species": row["species"],
                "kind": row["kind"],
                "count": int(row["count"]),
                "local_xy_m": [round(x, 6), round(y, 6)],
                "position_wgs84": row["position_wgs84"],
                "position_status": row["position_status"],
                "position_confidence": row["position_confidence"],
                "placement_class": placement,
                "surface_hits": [name for name, value in hits.items() if value],
                "distance_to_vehicle_surface_m": round(distance_to_vehicle, 3),
                "visual_radius_m": round(visual_radius, 3),
                "visual_crown_over_vehicle": crown_over_vehicle,
                "visual_crown_over_pedestrian": crown_over_pedestrian,
                "automatic_relocation": False,
            }
        )

    report = {
        "schema": "green-atlas.vegetation-placement-audit.v1",
        "status": "review_required_no_automatic_relocation",
        "counts": dict(counts),
        "position_provenance": dict(position_counts),
        "rules": {
            "source_position": "inventory DXF marker when matched; nearest marker or label fallback remains flagged",
            "horizontal_alignment": "candidate DXF-to-WGS84 transform; RMSE 0.852763 m on 4/7 inlier controls",
            "vehicle_surface": "OSM/Overture centerline buffered by explicit width when present, otherwise class/lane proxy",
            "pedestrian_surface": "OSM footway/steps centerline buffered by proxy width",
            "green_surface": "only explicitly mapped OSM green polygons; absence does not mean paved ground",
            "visual_overlap": "asset crown/group radius is visual only and is audited separately from the source trunk/group point",
            "automatic_relocation": False,
        },
        "limitations": [
            "A point on a proxy road edge is a review flag, not proof that the source tree is physically in the carriageway.",
            "A tree point on a mapped footway can represent a real tree pit; the current GIS has no tree-pit polygons.",
            "Shrub inventory rows are represented by one marker although the real group can occupy an authored area.",
            "Building intersections expose horizontal conflation errors or source/date disagreement and block automatic admission.",
            "The audit never snaps, deletes or invents vegetation positions.",
        ],
        "records": classified,
    }
    report_path = args.output / "vegetation-placement-audit.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    radius = float(world["coordinate_frame"]["earth_radius_m"])
    origin_lon, origin_lat = world["coordinate_frame"]["origin_wgs84_lon_lat"]

    def lonlat(x: float, y: float) -> list[float]:
        return [
            origin_lon + math.degrees(x / (radius * math.cos(math.radians(origin_lat)))),
            origin_lat + math.degrees(y / radius),
        ]

    def geometry_wgs84(geometry: dict[str, Any]) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            if (
                isinstance(value, list)
                and len(value) >= 2
                and isinstance(value[0], (int, float))
                and isinstance(value[1], (int, float))
            ):
                return lonlat(float(value[0]), float(value[1]))
            if isinstance(value, list):
                return [convert(child) for child in value]
            return value

        return {"type": geometry["type"], "coordinates": convert(geometry["coordinates"])}

    context_features = []
    for row in vehicle_rows:
        context_features.append({
            "type": "Feature",
            "geometry": geometry_wgs84(row["surface_geometry_local"]),
            "properties": {"layer": "vehicle_surface", "class": row["properties"].get("class"), "width_status": row["properties"].get("width_status")},
        })
    for row in pedestrian_rows:
        context_features.append({
            "type": "Feature",
            "geometry": geometry_wgs84(row["surface_geometry_local"]),
            "properties": {"layer": "pedestrian_surface", "class": row["properties"].get("class"), "width_status": row["properties"].get("width_status")},
        })
    for row in world["green_areas"]:
        context_features.append({
            "type": "Feature",
            "geometry": geometry_wgs84(row["geometry_local"]),
            "properties": {"layer": "mapped_green", "class": row["properties"].get("class")},
        })
    for row in world["buildings"]:
        context_features.append({
            "type": "Feature",
            "geometry": geometry_wgs84(row["geometry_local"]),
            "properties": {"layer": "building", "id": row["id"]},
        })
    point_features = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": row["position_wgs84"]},
        "properties": {key: value for key, value in row.items() if key not in {"position_wgs84", "local_xy_m"}},
    } for row in classified]
    geojson = {"type": "FeatureCollection", "features": context_features + point_features}
    geojson_path = args.output / "vegetation-placement-map.geojson"
    geojson_path.write_text(json.dumps(geojson, ensure_ascii=False) + "\n")

    min_x, min_y, max_x, max_y = (float(value) for value in world["scope"]["local_bounds_m"])
    width, height, margin = 1200, 1800, 45
    scale = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))
    ox = (width - (max_x - min_x) * scale) * 0.5
    oy = (height - (max_y - min_y) * scale) * 0.5

    def screen(x: float, y: float) -> tuple[float, float]:
        return ox + (x - min_x) * scale, height - oy - (y - min_y) * scale

    def path(ring: list[list[float]]) -> str:
        return "M" + " L".join(
            f"{x:.2f},{y:.2f}" for x, y in (screen(float(p[0]), float(p[1])) for p in ring)
        ) + " Z"

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#edf0ec"/>',
        '<g stroke-linejoin="round" stroke-linecap="round">',
    ]
    for row in world["green_areas"]:
        for ring in polygon_rings(row["geometry_local"]):
            svg.append(f'<path d="{path(ring)}" fill="#a8c990" stroke="none"/>')
    for row in vehicle_rows:
        for ring in polygon_rings(row["surface_geometry_local"]):
            svg.append(f'<path d="{path(ring)}" fill="#6f777a" fill-opacity="0.78" stroke="none"/>')
    for row in pedestrian_rows:
        for ring in polygon_rings(row["surface_geometry_local"]):
            svg.append(f'<path d="{path(ring)}" fill="#c7c4bc" stroke="#aaa69e" stroke-width="0.35"/>')
    for row in world["buildings"]:
        for ring in polygon_rings(row["geometry_local"]):
            svg.append(f'<path d="{path(ring)}" fill="#e7dfd1" stroke="#746e65" stroke-width="0.6"/>')
    for row in classified:
        x, y = screen(*row["local_xy_m"])
        radius_px = 2.6 if row["kind"] == "tree" else 2.1
        svg.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius_px}" fill="{COLORS[row["placement_class"]]}" stroke="#ffffff" stroke-width="0.45"/>'
        )
    svg.append('</g>')
    svg.append('<rect x="22" y="18" width="750" height="204" rx="9" fill="#ffffff" fill-opacity="0.94"/>')
    svg.append('<text x="42" y="52" font-family="Arial" font-size="24" font-weight="700">Кустанайская — аудит размещения озеленения</text>')
    legend = [
        ("building_conflict", f'контур здания — {counts["building_conflict"]}'),
        ("vehicle_surface_review", f'автомобильная поверхность, проверить — {counts["vehicle_surface_review"]}'),
        ("pedestrian_surface_review", f'тротуар/ступени, возможна лунка — {counts["pedestrian_surface_review"]}'),
        ("mapped_green", f'OSM-зелёная зона — {counts["mapped_green"]}'),
        ("unclassified_surface", f'поверхность не размечена — {counts["unclassified_surface"]}'),
    ]
    for index, (key, label) in enumerate(legend):
        y = 82 + index * 28
        svg.append(f'<circle cx="47" cy="{y - 5}" r="6" fill="{COLORS[key]}"/>')
        svg.append(f'<text x="64" y="{y}" font-family="Arial" font-size="16">{label}</text>')
    svg.append('</svg>')
    svg_path = args.output / "vegetation-placement-audit.svg"
    png_path = args.output / "vegetation-placement-audit.png"
    svg_path.write_text("\n".join(svg))
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-o", str(png_path), str(svg_path)], check=True)
    else:
        subprocess.run(["magick", str(svg_path), str(png_path)], check=True)

    map_data = json.dumps(geojson, ensure_ascii=False)
    center_lat = (world["scope"]["context_bbox_wgs84"][1] + world["scope"]["context_bbox_wgs84"][3]) / 2
    center_lon = (world["scope"]["context_bbox_wgs84"][0] + world["scope"]["context_bbox_wgs84"][2]) / 2
    html = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Кустанайская — аудит озеленения</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{{height:100%;margin:0}} .panel{{position:absolute;z-index:1000;top:12px;left:52px;background:#fff;padding:12px 15px;border-radius:8px;box-shadow:0 2px 14px #0003;font:14px Arial;max-width:390px}} .panel b{{font-size:17px}} .legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}}</style>
</head><body><div id="map"></div><div class="panel"><b>Кустанайская: проверка 1278 позиций</b><br>Точки не перемещались автоматически.<div class="legend"><br>
<i style="background:{COLORS['building_conflict']}"></i>здание: {counts['building_conflict']} &nbsp;
<i style="background:{COLORS['vehicle_surface_review']}"></i>дорога: {counts['vehicle_surface_review']}<br>
<i style="background:{COLORS['pedestrian_surface_review']}"></i>тротуар: {counts['pedestrian_surface_review']} &nbsp;
<i style="background:{COLORS['mapped_green']}"></i>зелёная зона: {counts['mapped_green']}<br>
<i style="background:{COLORS['unclassified_surface']}"></i>не размечено: {counts['unclassified_surface']}</div></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const data={map_data}; const colors={json.dumps(COLORS)};
const map=L.map('map').setView([{center_lat},{center_lon}],17);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:20,attribution:'© OpenStreetMap contributors'}}).addTo(map);
const layers={{building:L.layerGroup(),vehicle:L.layerGroup(),pedestrian:L.layerGroup(),green:L.layerGroup(),vegetation:L.layerGroup()}};
function style(f){{const k=f.properties.layer; if(k==='building')return{{color:'#746e65',weight:1,fillColor:'#e7dfd1',fillOpacity:.45}};if(k==='vehicle_surface')return{{color:'#50575a',weight:1,fillColor:'#6f777a',fillOpacity:.35}};if(k==='pedestrian_surface')return{{color:'#98958f',weight:1,fillColor:'#c7c4bc',fillOpacity:.45}};return{{color:'#53824d',weight:1,fillColor:'#a8c990',fillOpacity:.35}}}}
data.features.forEach(f=>{{if(f.geometry.type==='Point'){{const p=f.properties;const m=L.circleMarker([f.geometry.coordinates[1],f.geometry.coordinates[0]],{{radius:4,color:'#fff',weight:1,fillColor:colors[p.placement_class],fillOpacity:.95}});m.bindPopup(`<b>№${{p.inventory_id}} — ${{p.species}}</b><br>${{p.kind}}, количество: ${{p.count}}<br>позиция: ${{p.position_status}}<br>класс: ${{p.placement_class}}<br>до автодороги: ${{p.distance_to_vehicle_surface_m}} м<br>автоперенос: нет`);m.addTo(layers.vegetation)}}else{{const k=f.properties.layer==='vehicle_surface'?'vehicle':f.properties.layer==='pedestrian_surface'?'pedestrian':f.properties.layer==='mapped_green'?'green':'building';L.geoJSON(f,{{style}}).addTo(layers[k])}}}});
Object.values(layers).forEach(x=>x.addTo(map));L.control.layers(null,{{'Озеленение':layers.vegetation,'Здания':layers.building,'Автодороги (частично proxy)':layers.vehicle,'Тротуары (proxy)':layers.pedestrian,'OSM зелёные зоны':layers.green}},{{collapsed:false}}).addTo(map);
map.fitBounds([[{world['scope']['context_bbox_wgs84'][1]},{world['scope']['context_bbox_wgs84'][0]}],[{world['scope']['context_bbox_wgs84'][3]},{world['scope']['context_bbox_wgs84'][2]}]]);
</script></body></html>'''
    html_path = args.output / "vegetation-placement-map.html"
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
