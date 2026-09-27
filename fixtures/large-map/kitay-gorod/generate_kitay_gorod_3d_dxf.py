"""Build a reproducible, georeferenced 3D DXF of central Moscow.

The checked-in OSM snapshot remains the horizontal source of truth. Terrain is
sampled from Open-Meteo's Copernicus DEM GLO-90 endpoint and cached beside the
fixture. Buildings with an explicit ``height`` are kept separate from heights
estimated from ``building:levels`` so downstream UI can state provenance.
"""

from __future__ import annotations

import argparse
import json
import re
from math import cos, pi
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import ezdxf
from shapely.geometry import LineString, Point, Polygon, box


ROOT = Path(__file__).parent
SOURCE = ROOT / "kitay-gorod.osm.json"
DEM_CACHE = ROOT / "kitay-gorod.copernicus-glo90.json"
TARGET = ROOT / "kitay-gorod-3d.dxf"
EARTH_RADIUS_M = 6_378_137
ORIGIN_LAT = 55.75225
ORIGIN_LON = 37.62350
BOUNDS = (55.7460, 37.6100, 55.7585, 37.6370)
DEM_ROWS = 17
DEM_COLUMNS = 21
LEVEL_HEIGHT_M = 3.0
COPERNICUS_GLO90_LICENSE_URL = (
    "https://dataspace.copernicus.eu/sites/default/files/media/files/2025-06/"
    "copernicus_contributing_mission_data_access_v2_cop_dem_licenses.pdf"
)
COPERNICUS_GLO90_NOTICE = (
    "produced using Copernicus WorldDEM-90 © DLR e.V. 2010-2014 and "
    "© Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS "
    "by the European Union and ESA; all rights reserved; elevation delivery by Open-Meteo"
)


def local_xy(lat: float, lon: float) -> tuple[float, float]:
    x = EARTH_RADIUS_M * cos(ORIGIN_LAT * pi / 180) * (lon - ORIGIN_LON) * pi / 180
    y = EARTH_RADIUS_M * (lat - ORIGIN_LAT) * pi / 180
    return round(x, 3), round(y, 3)


def dem_coordinates() -> list[tuple[float, float]]:
    south, west, north, east = BOUNDS
    return [
        (
            south + (north - south) * row / (DEM_ROWS - 1),
            west + (east - west) * column / (DEM_COLUMNS - 1),
        )
        for row in range(DEM_ROWS)
        for column in range(DEM_COLUMNS)
    ]


def download_dem() -> dict:
    coordinates = dem_coordinates()
    elevations: list[float] = []
    for offset in range(0, len(coordinates), 100):
        chunk = coordinates[offset:offset + 100]
        query = urlencode({
            "latitude": ",".join(f"{lat:.7f}" for lat, _ in chunk),
            "longitude": ",".join(f"{lon:.7f}" for _, lon in chunk),
        })
        with urlopen(f"https://api.open-meteo.com/v1/elevation?{query}", timeout=45) as response:
            values = json.load(response).get("elevation", [])
        if len(values) != len(chunk):
            raise RuntimeError("Open-Meteo returned an incomplete DEM response")
        elevations.extend(float(value) for value in values)
    payload = {
        "source": "Copernicus DEM 2021 GLO-90 via Open-Meteo Elevation API",
        "source_url": "https://open-meteo.com/en/docs/elevation-api",
        "license_url": COPERNICUS_GLO90_LICENSE_URL,
        "attribution": COPERNICUS_GLO90_NOTICE,
        "confidence": "estimated",
        "product_type": "DSM",
        "horizontal_resolution_m": 90,
        "absolute_vertical_accuracy_m_le90": 4,
        "absolute_horizontal_accuracy_m_ce90": 6,
        "bounds_wgs84": list(BOUNDS),
        "origin_wgs84": [ORIGIN_LAT, ORIGIN_LON],
        "rows": DEM_ROWS,
        "columns": DEM_COLUMNS,
        "coordinates_wgs84": coordinates,
        "elevations_m_asl": elevations,
    }
    DEM_CACHE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def load_dem(refresh: bool) -> dict:
    return download_dem() if refresh or not DEM_CACHE.exists() else json.loads(DEM_CACHE.read_text())


def number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", value)
    if not match:
        return None
    result = float(match.group(0).replace(",", "."))
    return result if result > 0 else None


def building_height(tags: dict[str, str]) -> tuple[float | None, str]:
    if explicit := number(tags.get("height")):
        return min(explicit, 240.0), "GREEN_ATLAS_BUILDING_OSM_HEIGHT"
    if levels := number(tags.get("building:levels")):
        roof = number(tags.get("roof:height")) or 0.0
        return min(levels * LEVEL_HEIGHT_M + roof, 240.0), "GREEN_ATLAS_BUILDING_OSM_LEVELS"
    return None, "OSM_BUILDING"


def target_layer(tags: dict[str, str]) -> tuple[str, bool]:
    if tags.get("building"):
        return building_height(tags)[1], True
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
    if tags.get("natural") == "tree" or tags.get("landuse") or tags.get("leisure") or tags.get("natural"):
        return "OSM_GREEN_EXISTING", True
    if tags.get("barrier"):
        return "OSM_BARRIER", False
    if tags.get("man_made"):
        return "OSM_TECHNICAL", False
    return "OSM_REFERENCE", False


class Terrain:
    def __init__(self, payload: dict):
        elevations = [float(value) for value in payload["elevations_m_asl"]]
        self.datum = min(elevations)
        self.values = [value - self.datum for value in elevations]

    def at_grid(self, row: int, column: int) -> float:
        return self.values[row * DEM_COLUMNS + column]

    def at_wgs84(self, lat: float, lon: float) -> float:
        south, west, north, east = BOUNDS
        row = max(0.0, min(DEM_ROWS - 1.0, (lat - south) / (north - south) * (DEM_ROWS - 1)))
        column = max(0.0, min(DEM_COLUMNS - 1.0, (lon - west) / (east - west) * (DEM_COLUMNS - 1)))
        r0, c0 = int(row), int(column)
        r1, c1 = min(DEM_ROWS - 1, r0 + 1), min(DEM_COLUMNS - 1, c0 + 1)
        rt, ct = row - r0, column - c0
        top = self.at_grid(r0, c0) * (1 - ct) + self.at_grid(r0, c1) * ct
        bottom = self.at_grid(r1, c0) * (1 - ct) + self.at_grid(r1, c1) * ct
        return round(top * (1 - rt) + bottom * rt, 3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-dem", action="store_true")
    args = parser.parse_args()
    dem_payload = load_dem(args.refresh_dem)
    terrain = Terrain(dem_payload)
    payload = json.loads(SOURCE.read_text())

    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    # These paired custom properties are the machine-readable evidence used
    # by the importer. The layer name by itself must never prove terrain.
    for tag, value in (
        ("GREEN_ATLAS_TERRAIN_LAYER", "GREEN_ATLAS_TERRAIN_COP90"),
        ("GREEN_ATLAS_TERRAIN_DATASET", str(dem_payload["source"])),
        ("GREEN_ATLAS_TERRAIN_SOURCE_URL", str(dem_payload["source_url"])),
        ("GREEN_ATLAS_TERRAIN_ATTRIBUTION", str(dem_payload["attribution"])),
        ("GREEN_ATLAS_TERRAIN_CONFIDENCE", str(dem_payload["confidence"])),
        ("GREEN_ATLAS_VERTICAL_DATUM", "local_zero_at_dem_minimum"),
        ("GREEN_ATLAS_VERTICAL_DATUM_OFFSET_M_ASL", f"{terrain.datum:.3f}"),
    ):
        document.header.custom_vars.append(tag, value)
    for tag, value in (
        ("GREEN_ATLAS_HORIZONTAL_SOURCE", "OpenStreetMap snapshot: kitay-gorod.osm.json"),
        ("GREEN_ATLAS_ORIGIN_WGS84", f"{ORIGIN_LAT:.7f},{ORIGIN_LON:.7f}"),
        ("GREEN_ATLAS_LOCAL_PROJECTION", "local_equirectangular_wgs84"),
        ("GREEN_ATLAS_EARTH_RADIUS_M", f"{EARTH_RADIUS_M:.3f}"),
    ):
        document.header.custom_vars.append(tag, value)
    layers = (
        ("GREEN_ATLAS_TERRAIN_COP90", 3),
        ("GREEN_ATLAS_BUILDING_OSM_HEIGHT", 8),
        ("GREEN_ATLAS_BUILDING_OSM_LEVELS", 9),
        ("OSM_BUILDING", 8), ("OSM_ROAD_MAJOR", 30), ("OSM_ROAD_LOCAL", 9),
        ("OSM_PATH", 32), ("OSM_RAILWAY", 6), ("OSM_GREEN_EXISTING", 94),
        ("OSM_HYDROGRAPHY", 5), ("OSM_BARRIER", 1), ("OSM_TECHNICAL", 4),
        ("OSM_REFERENCE", 7), ("SITE_BORDER", 3),
    )
    for name, color in layers:
        document.layers.add(name, color=color)
    modelspace = document.modelspace()

    coordinates = dem_coordinates()
    for row in range(DEM_ROWS - 1):
        for column in range(DEM_COLUMNS - 1):
            indices = (
                row * DEM_COLUMNS + column,
                row * DEM_COLUMNS + column + 1,
                (row + 1) * DEM_COLUMNS + column + 1,
                (row + 1) * DEM_COLUMNS + column,
            )
            vertices = [
                (*local_xy(*coordinates[index]), terrain.values[index])
                for index in indices
            ]
            modelspace.add_3dface(vertices, dxfattribs={"layer": "GREEN_ATLAS_TERRAIN_COP90"})

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
            if not clip_bounds.covers(Point(center)):
                continue
            modelspace.add_circle(
                center,
                radius=1.5,
                dxfattribs={"layer": layer},
            )
            by_layer[layer] = by_layer.get(layer, 0) + 1
            continue

        source_points = element.get("geometry") or []
        points = [local_xy(point["lat"], point["lon"]) for point in source_points]
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
            representative = source_points[len(source_points) // 2]
            elevation = terrain.at_wgs84(representative["lat"], representative["lon"])
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
