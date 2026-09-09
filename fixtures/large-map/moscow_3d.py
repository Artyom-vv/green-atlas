"""Shared evidence-preserving helpers for Moscow 3D fixture generators."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


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


def grid_coordinates(bounds: tuple[float, float, float, float], rows: int, columns: int):
    south, west, north, east = bounds
    return [
        (south + (north - south) * row / (rows - 1), west + (east - west) * column / (columns - 1))
        for row in range(rows)
        for column in range(columns)
    ]


def load_dem(
    cache: Path,
    bounds: tuple[float, float, float, float],
    origin: tuple[float, float],
    rows: int,
    columns: int,
    refresh: bool,
) -> dict:
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    coordinates = grid_coordinates(bounds, rows, columns)
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
        "bounds_wgs84": list(bounds),
        "origin_wgs84": list(origin),
        "rows": rows,
        "columns": columns,
        "coordinates_wgs84": coordinates,
        "elevations_m_asl": elevations,
    }
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


class TerrainGrid:
    def __init__(self, payload: dict):
        self.rows = int(payload["rows"])
        self.columns = int(payload["columns"])
        self.bounds = tuple(float(value) for value in payload["bounds_wgs84"])
        elevations = [float(value) for value in payload["elevations_m_asl"]]
        if len(elevations) != self.rows * self.columns:
            raise ValueError("DEM cache dimensions do not match elevation count")
        self.datum = min(elevations)
        self.values = [value - self.datum for value in elevations]

    def at_grid(self, row: int, column: int) -> float:
        return self.values[row * self.columns + column]

    def at_wgs84(self, lat: float, lon: float) -> float:
        south, west, north, east = self.bounds
        row = max(0.0, min(self.rows - 1.0, (lat - south) / (north - south) * (self.rows - 1)))
        column = max(0.0, min(self.columns - 1.0, (lon - west) / (east - west) * (self.columns - 1)))
        r0, c0 = int(row), int(column)
        r1, c1 = min(self.rows - 1, r0 + 1), min(self.columns - 1, c0 + 1)
        rt, ct = row - r0, column - c0
        top = self.at_grid(r0, c0) * (1 - ct) + self.at_grid(r0, c1) * ct
        bottom = self.at_grid(r1, c0) * (1 - ct) + self.at_grid(r1, c1) * ct
        return round(top * (1 - rt) + bottom * rt, 3)


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


def declare_terrain(document, payload: dict, layer: str, datum: float) -> None:
    for tag, value in (
        ("GREEN_ATLAS_TERRAIN_LAYER", layer),
        ("GREEN_ATLAS_TERRAIN_DATASET", str(payload["source"])),
        ("GREEN_ATLAS_TERRAIN_SOURCE_URL", str(payload["source_url"])),
        ("GREEN_ATLAS_TERRAIN_ATTRIBUTION", str(payload["attribution"])),
        ("GREEN_ATLAS_TERRAIN_CONFIDENCE", str(payload["confidence"])),
        ("GREEN_ATLAS_VERTICAL_DATUM", "local_zero_at_dem_minimum"),
        ("GREEN_ATLAS_VERTICAL_DATUM_OFFSET_M_ASL", f"{datum:.3f}"),
    ):
        document.header.custom_vars.append(tag, value)


def declare_local_wgs84(document, source: str, origin: tuple[float, float], earth_radius_m: float) -> None:
    """Declare the exact local XY formula without pretending it is a CRS."""
    for tag, value in (
        ("GREEN_ATLAS_HORIZONTAL_SOURCE", source),
        ("GREEN_ATLAS_ORIGIN_WGS84", f"{origin[0]:.7f},{origin[1]:.7f}"),
        ("GREEN_ATLAS_LOCAL_PROJECTION", "local_equirectangular_wgs84"),
        ("GREEN_ATLAS_EARTH_RADIUS_M", f"{earth_radius_m:.3f}"),
    ):
        document.header.custom_vars.append(tag, value)
