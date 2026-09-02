"""Convert the checked-in OpenStreetMap Kitay-gorod extract to a layered DXF fixture."""

from __future__ import annotations

import json
from math import cos, pi
from pathlib import Path

import ezdxf
from shapely.geometry import LineString, Point, Polygon, box


ROOT = Path(__file__).parent
SOURCE = ROOT / "kitay-gorod.osm.json"
TARGET = ROOT / "kitay-gorod-large.dxf"
EARTH_RADIUS_M = 6_378_137
ORIGIN_LAT = 55.75225
ORIGIN_LON = 37.62350
BOUNDS = (55.7460, 37.6100, 55.7585, 37.6370)


def local_xy(lat: float, lon: float) -> tuple[float, float]:
    x = EARTH_RADIUS_M * cos(ORIGIN_LAT * pi / 180) * (lon - ORIGIN_LON) * pi / 180
    y = EARTH_RADIUS_M * (lat - ORIGIN_LAT) * pi / 180
    return round(x, 3), round(y, 3)


def target_layer(tags: dict[str, str]) -> tuple[str, bool]:
    if tags.get("building"):
        return "OSM_BUILDING", True
    highway = tags.get("highway")
    if highway:
        if highway in {"footway", "path", "pedestrian", "steps", "cycleway", "bridleway"}:
            return "OSM_PATH", False
        major = highway in {
            "motorway", "trunk", "primary", "secondary", "tertiary",
            "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
        }
        return ("OSM_ROAD_MAJOR" if major else "OSM_ROAD_LOCAL"), False
    if tags.get("railway"):
        return "OSM_RAILWAY", False
    if tags.get("waterway") or tags.get("natural") == "water" or tags.get("water"):
        return "OSM_HYDROGRAPHY", True
    if tags.get("natural") == "tree":
        return "OSM_GREEN_EXISTING", True
    if tags.get("landuse") or tags.get("leisure") or tags.get("natural"):
        return "OSM_GREEN_EXISTING", True
    if tags.get("barrier"):
        return "OSM_BARRIER", False
    if tags.get("man_made"):
        return "OSM_TECHNICAL", False
    return "OSM_REFERENCE", False


payload = json.loads(SOURCE.read_text())
document = ezdxf.new("R2013", setup=True)
document.units = ezdxf.units.M
for name, color in (
    ("SITE_BORDER", 3),
    ("OSM_BUILDING", 8),
    ("OSM_ROAD_MAJOR", 30),
    ("OSM_ROAD_LOCAL", 9),
    ("OSM_PATH", 32),
    ("OSM_RAILWAY", 6),
    ("OSM_GREEN_EXISTING", 94),
    ("OSM_HYDROGRAPHY", 5),
    ("OSM_BARRIER", 1),
    ("OSM_TECHNICAL", 4),
    ("OSM_REFERENCE", 7),
):
    document.layers.add(name, color=color)

modelspace = document.modelspace()
south, west, north, east = BOUNDS
clip_bounds = box(*local_xy(south, west), *local_xy(north, east))
modelspace.add_lwpolyline(
    [local_xy(south, west), local_xy(south, east), local_xy(north, east), local_xy(north, west)],
    close=True,
    dxfattribs={"layer": "SITE_BORDER"},
)

written = 0
vertices = 0
by_layer: dict[str, int] = {}
for element in payload["elements"]:
    tags = element.get("tags") or {}
    layer, area_by_default = target_layer(tags)
    if element.get("type") == "node" and tags.get("natural") == "tree":
        center = local_xy(element["lat"], element["lon"])
        if not clip_bounds.covers(Point(center)):
            continue
        modelspace.add_circle(center, radius=1.5, dxfattribs={"layer": layer})
        written += 1
        vertices += 1
        by_layer[layer] = by_layer.get(layer, 0) + 1
        continue

    geometry = element.get("geometry") or []
    points = [local_xy(point["lat"], point["lon"]) for point in geometry]
    if len(points) < 2:
        continue
    closed = len(points) >= 4 and points[0] == points[-1]
    if closed:
        points = points[:-1]
    is_area = closed and (area_by_default or tags.get("area") == "yes")
    source_geometry = Polygon(points) if is_area else LineString(points)
    clipped = source_geometry.intersection(clip_bounds)
    parts = list(clipped.geoms) if hasattr(clipped, "geoms") else [clipped]
    for part in parts:
        if part.is_empty:
            continue
        if part.geom_type == "Polygon":
            clipped_points = [(round(x, 3), round(y, 3)) for x, y in list(part.exterior.coords)[:-1]]
            close = True
        elif part.geom_type == "LineString":
            clipped_points = [(round(x, 3), round(y, 3)) for x, y in part.coords]
            close = False
        else:
            continue
        if len(clipped_points) < (3 if close else 2):
            continue
        modelspace.add_lwpolyline(clipped_points, close=close, dxfattribs={"layer": layer})
        written += 1
        vertices += len(clipped_points)
        by_layer[layer] = by_layer.get(layer, 0) + 1

document.header["$LASTSAVEDBY"] = "Green Atlas OSM fixture generator"
document.saveas(TARGET)
print(f"{TARGET}: {written + 1} entities, {vertices + 4} vertices")
for layer, count in sorted(by_layer.items()):
    print(f"  {layer}: {count}")
