"""Build the reproducible terrain/building-enriched VDNKh DXF fixture."""

from __future__ import annotations

import argparse
import json
from math import cos, pi
from pathlib import Path

import ezdxf
from shapely.geometry import LineString, Point, Polygon, box

from moscow_3d import TerrainGrid, building_height, declare_local_wgs84, declare_terrain, grid_coordinates, load_dem


ROOT = Path(__file__).parent
SOURCE = ROOT / "vdnkh.osm.json"
DEM_CACHE = ROOT / "vdnkh.copernicus-glo90.json"
TARGET = ROOT / "vdnkh-3d.dxf"
EARTH_RADIUS_M = 6_378_137
ORIGIN_LAT = 55.8295
ORIGIN_LON = 37.6355
BOUNDS = (55.8210, 37.6210, 55.8380, 37.6500)
DEM_ROWS = 17
DEM_COLUMNS = 21
TERRAIN_LAYER = "GREEN_ATLAS_TERRAIN_COP90"


def local_xy(lat: float, lon: float) -> tuple[float, float]:
    x = EARTH_RADIUS_M * cos(ORIGIN_LAT * pi / 180) * (lon - ORIGIN_LON) * pi / 180
    y = EARTH_RADIUS_M * (lat - ORIGIN_LAT) * pi / 180
    return round(x, 3), round(y, 3)


def wgs84_at(x: float, y: float) -> tuple[float, float]:
    lat = ORIGIN_LAT + y / EARTH_RADIUS_M * 180 / pi
    lon = ORIGIN_LON + x / (EARTH_RADIUS_M * cos(ORIGIN_LAT * pi / 180)) * 180 / pi
    return lat, lon


def target_layer(tags: dict[str, str]) -> tuple[str, bool]:
    if tags.get("building"):
        return building_height(tags)[1], True
    if tags.get("highway"):
        major = tags["highway"] in {
            "motorway", "trunk", "primary", "secondary", "tertiary",
            "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
        }
        return ("OSM_ROAD_MAJOR" if major else "OSM_ROAD_LOCAL"), False
    if tags.get("waterway") or tags.get("natural") == "water" or tags.get("water"):
        return "OSM_HYDROGRAPHY", tags.get("area") == "yes"
    if tags.get("landuse") or tags.get("leisure") or tags.get("natural"):
        return "OSM_GREEN_EXISTING", True
    return "OSM_REFERENCE", False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-dem", action="store_true")
    args = parser.parse_args()
    dem_payload = load_dem(
        DEM_CACHE,
        BOUNDS,
        (ORIGIN_LAT, ORIGIN_LON),
        DEM_ROWS,
        DEM_COLUMNS,
        args.refresh_dem,
    )
    terrain = TerrainGrid(dem_payload)
    payload = json.loads(SOURCE.read_text())

    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    declare_terrain(document, dem_payload, TERRAIN_LAYER, terrain.datum)
    declare_local_wgs84(
        document,
        "OpenStreetMap snapshot: vdnkh.osm.json",
        (ORIGIN_LAT, ORIGIN_LON),
        EARTH_RADIUS_M,
    )
    for name, color in (
        (TERRAIN_LAYER, 3),
        ("GREEN_ATLAS_BUILDING_OSM_HEIGHT", 8),
        ("GREEN_ATLAS_BUILDING_OSM_LEVELS", 9),
        ("SITE_BORDER", 3), ("OSM_BUILDING", 8), ("OSM_ROAD_MAJOR", 30),
        ("OSM_ROAD_LOCAL", 9), ("OSM_GREEN_EXISTING", 94),
        ("OSM_HYDROGRAPHY", 5), ("OSM_REFERENCE", 7),
    ):
        document.layers.add(name, color=color)
    modelspace = document.modelspace()

    coordinates = grid_coordinates(BOUNDS, DEM_ROWS, DEM_COLUMNS)
    for row in range(DEM_ROWS - 1):
        for column in range(DEM_COLUMNS - 1):
            indices = (
                row * DEM_COLUMNS + column,
                row * DEM_COLUMNS + column + 1,
                (row + 1) * DEM_COLUMNS + column + 1,
                (row + 1) * DEM_COLUMNS + column,
            )
            modelspace.add_3dface(
                [(*local_xy(*coordinates[index]), terrain.values[index]) for index in indices],
                dxfattribs={"layer": TERRAIN_LAYER},
            )

    south, west, north, east = BOUNDS
    clip_bounds = box(*local_xy(south, west), *local_xy(north, east))
    border = [local_xy(south, west), local_xy(south, east), local_xy(north, east), local_xy(north, west)]
    modelspace.add_lwpolyline(border, close=True, dxfattribs={"layer": "SITE_BORDER"})

    by_layer: dict[str, int] = {}
    for element in payload["elements"]:
        tags = element.get("tags") or {}
        layer, area_by_default = target_layer(tags)
        if element.get("type") == "node" and tags.get("natural") == "tree":
            center = local_xy(element["lat"], element["lon"])
            if clip_bounds.covers(Point(center)):
                modelspace.add_circle(center, radius=1.5, dxfattribs={"layer": layer})
                by_layer[layer] = by_layer.get(layer, 0) + 1
            continue
        points = [local_xy(point["lat"], point["lon"]) for point in element.get("geometry") or []]
        if len(points) < 2:
            continue
        closed = len(points) >= 4 and points[0] == points[-1]
        if closed:
            points = points[:-1]
        is_area = closed and (area_by_default or tags.get("area") == "yes")
        source_geometry = Polygon(points) if is_area else LineString(points)
        clipped = source_geometry.intersection(clip_bounds)
        parts = list(clipped.geoms) if hasattr(clipped, "geoms") else [clipped]
        height, _ = building_height(tags) if tags.get("building") else (None, layer)
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
            centroid = part.centroid
            elevation = terrain.at_wgs84(*wgs84_at(float(centroid.x), float(centroid.y)))
            attributes: dict[str, object] = {"layer": layer}
            if tags.get("building"):
                attributes["elevation"] = elevation
            if height is not None:
                attributes["thickness"] = height
            modelspace.add_lwpolyline(clipped_points, close=close, dxfattribs=attributes)
            by_layer[layer] = by_layer.get(layer, 0) + 1

    document.header["$LASTSAVEDBY"] = "Green Atlas evidence-preserving Moscow 3D fixture generator"
    document.saveas(TARGET)
    print(f"{TARGET}: Copernicus datum {terrain.datum:.1f} m ASL")
    for layer, count in sorted(by_layer.items()):
        print(f"  {layer}: {count}")


if __name__ == "__main__":
    main()
