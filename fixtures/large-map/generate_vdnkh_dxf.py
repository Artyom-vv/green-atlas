"""Convert the checked-in OpenStreetMap extract around VDNKh to a large DXF fixture."""

from __future__ import annotations

import json
from math import cos, pi
from pathlib import Path

import ezdxf


ROOT = Path(__file__).parent
SOURCE = ROOT / "vdnkh.osm.json"
TARGET = ROOT / "vdnkh-large.dxf"
EARTH_RADIUS_M = 6_378_137
ORIGIN_LAT = 55.8295
ORIGIN_LON = 37.6355


def local_xy(lat: float, lon: float) -> tuple[float, float]:
    x = EARTH_RADIUS_M * cos(ORIGIN_LAT * pi / 180) * (lon - ORIGIN_LON) * pi / 180
    y = EARTH_RADIUS_M * (lat - ORIGIN_LAT) * pi / 180
    return round(x, 3), round(y, 3)


def target_layer(tags: dict[str, str]) -> tuple[str, bool]:
    if tags.get("building"):
        return "OSM_BUILDING", True
    if tags.get("highway"):
        major = tags["highway"] in {"motorway", "trunk", "primary", "secondary", "tertiary", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
        return ("OSM_ROAD_MAJOR" if major else "OSM_ROAD_LOCAL"), False
    if tags.get("waterway") or tags.get("natural") == "water" or tags.get("water"):
        return "OSM_HYDROGRAPHY", tags.get("area") == "yes"
    if tags.get("landuse") or tags.get("leisure") or tags.get("natural"):
        return "OSM_GREEN_EXISTING", True
    return "OSM_REFERENCE", False


payload = json.loads(SOURCE.read_text())
document = ezdxf.new("R2013", setup=True)
document.units = ezdxf.units.M
for name, color in (
    ("SITE_BORDER", 3),
    ("OSM_BUILDING", 8),
    ("OSM_ROAD_MAJOR", 30),
    ("OSM_ROAD_LOCAL", 9),
    ("OSM_GREEN_EXISTING", 94),
    ("OSM_HYDROGRAPHY", 5),
    ("OSM_REFERENCE", 7),
):
    document.layers.add(name, color=color)

modelspace = document.modelspace()
modelspace.add_lwpolyline(
    [local_xy(55.8210, 37.6210), local_xy(55.8210, 37.6500), local_xy(55.8380, 37.6500), local_xy(55.8380, 37.6210)],
    close=True,
    dxfattribs={"layer": "SITE_BORDER"},
)

written = 0
vertices = 0
for element in payload["elements"]:
    geometry = element.get("geometry") or []
    points = [local_xy(point["lat"], point["lon"]) for point in geometry]
    if len(points) < 2:
        continue
    tags = element.get("tags") or {}
    layer, area_by_default = target_layer(tags)
    closed = len(points) >= 4 and points[0] == points[-1]
    if closed:
        points = points[:-1]
    should_close = closed and (area_by_default or tags.get("area") == "yes")
    modelspace.add_lwpolyline(points, close=should_close, dxfattribs={"layer": layer})
    written += 1
    vertices += len(points)

document.header["$LASTSAVEDBY"] = "Green Atlas OSM fixture generator"
document.saveas(TARGET)
print(f"{TARGET}: {written + 1} entities, {vertices + 4} vertices")
