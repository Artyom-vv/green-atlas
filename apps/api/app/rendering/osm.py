"""Bounded OSM height evidence for existing scene footprints.

This module never adds map geometry to a project. Fetching keeps WGS84 data;
the caller must put candidates and CAD footprints into the same metric frame.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.strtree import STRtree

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
CACHE_TTL = timedelta(days=7)
MAX_BBOX_AREA_M2 = 4_000_000
EARTH_RADIUS_M = 6_378_137


class OsmEvidenceError(ValueError):
    """An unavailable or incomplete external source, never an empty success."""


def parse_height(tags: dict[str, Any]) -> tuple[float | None, str | None]:
    """OSM height includes the roof; levels times three is a render estimate.

    An explicit malformed height is not replaced by another tag. The caller can
    report that observation and apply a separately labelled visual fallback.
    """
    value = tags.get("height")
    if value is not None:
        if isinstance(value, bool):
            return None, None
        text = str(value).strip()
        metric = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*(m|metres?|meters?))?", text)
        feet = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:ft|feet|foot)", text)
        imperial = re.fullmatch(r"(\d+(?:\.\d+)?)'(?:(\d+(?:\.\d+)?)\")?", text)
        if metric:
            height = float(metric.group(1))
        elif feet:
            height = float(feet.group(1)) * 0.3048
        elif imperial:
            inches = float(imperial.group(2) or 0)
            if inches >= 12:
                return None, None
            height = float(imperial.group(1)) * 0.3048 + inches * 0.0254
        else:
            return None, None
        if 0 < height <= 1000 and math.isfinite(height):
            return round(height, 6), "osm_height"
        return None, None
    levels = tags.get("building:levels")
    if isinstance(levels, bool) or levels is None:
        return None, None
    if re.fullmatch(r"\d+(?:\.\d+)?", str(levels).strip()):
        height = float(levels) * 3
        if 0 < height <= 1000 and math.isfinite(height):
            return round(height, 6), "osm_levels"
    return None, None


def _query(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    if not all(math.isfinite(v) for v in bbox):
        raise OsmEvidenceError("OSM: границы участка содержат неконечные координаты")
    if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
        raise OsmEvidenceError("OSM: недопустимые границы участка WGS84")
    width = (
        EARTH_RADIUS_M
        * math.radians(east - west)
        * math.cos(math.radians((south + north) / 2))
    )
    height = EARTH_RADIUS_M * math.radians(north - south)
    if width * height > MAX_BBOX_AREA_M2 or max(width, height) > 5000:
        raise OsmEvidenceError("OSM: область высот зданий превышает 4 км²")
    # Full geometry is necessary; cropping out geom to bbox opens boundary ways.
    extent = ",".join(f"{value:.8f}" for value in (south, west, north, east))
    return (
        "[out:json][timeout:20][maxsize:16777216];"
        f'(way["building"]["building"!="no"]({extent});'
        f'relation["type"="multipolygon"]["building"]["building"!="no"]({extent}););'
        "out meta geom;"
    )


def _coordinates(geometry: Any) -> list[tuple[float, float]]:
    if not isinstance(geometry, list) or len(geometry) < 2:
        raise OsmEvidenceError("missing_member_geometry")
    result = []
    for point in geometry:
        if not isinstance(point, dict):
            raise OsmEvidenceError("incomplete_member_geometry")
        lon, lat = point.get("lon"), point.get("lat")
        if (
            not isinstance(lon, (int, float))
            or isinstance(lon, bool)
            or not isinstance(lat, (int, float))
            or isinstance(lat, bool)
            or not math.isfinite(lon)
            or not math.isfinite(lat)
            or not -180 <= lon <= 180
            or not -90 <= lat <= 90
        ):
            raise OsmEvidenceError("invalid_member_coordinate")
        result.append((float(lon), float(lat)))
    return result


def _rings(
    segments: list[list[tuple[float, float]]],
) -> list[list[tuple[float, float]]]:
    """Join only exactly connected, unbranched relation members."""
    rings = []
    pending = []
    endpoints: dict[tuple[float, float], int] = {}
    for segment in segments:
        if segment[0] == segment[-1]:
            if len(segment) < 4:
                raise OsmEvidenceError("invalid_closed_ring")
            rings.append(segment)
        else:
            pending.append(segment)
            for point in (segment[0], segment[-1]):
                endpoints[point] = endpoints.get(point, 0) + 1
    if any(count != 2 for count in endpoints.values()):
        raise OsmEvidenceError("incomplete_or_branched_ring")
    while pending:
        ring = list(pending.pop(0))
        while ring[0] != ring[-1]:
            connected = [
                (index, segment)
                for index, segment in enumerate(pending)
                if ring[-1] in (segment[0], segment[-1])
            ]
            if len(connected) != 1:
                raise OsmEvidenceError("incomplete_or_ambiguous_ring")
            index, segment = connected[0]
            pending.pop(index)
            if segment[-1] == ring[-1]:
                segment = list(reversed(segment))
            ring.extend(segment[1:])
        if len(ring) < 4:
            raise OsmEvidenceError("invalid_closed_ring")
        rings.append(ring)
    return rings


def _relation_polygon(element: dict[str, Any]) -> Polygon | MultiPolygon:
    roles: dict[str, list[list[tuple[float, float]]]] = {"outer": [], "inner": []}
    members = element.get("members")
    if not isinstance(members, list):
        raise OsmEvidenceError("missing_relation_members")
    for member in members:
        if not isinstance(member, dict):
            raise OsmEvidenceError("invalid_relation_member")
        role = member.get("role") or "outer"
        if role not in roles:
            continue
        if member.get("type") != "way":
            raise OsmEvidenceError("unsupported_area_member")
        roles[role].append(_coordinates(member.get("geometry")))
    outers = _rings(roles["outer"])
    inners = _rings(roles["inner"])
    shells = [Polygon(ring) for ring in outers]
    if not shells or any(not shell.is_valid or shell.is_empty for shell in shells):
        raise OsmEvidenceError("invalid_outer_ring")
    holes: list[list[list[tuple[float, float]]]] = [[] for _ in shells]
    for ring in inners:
        hole = Polygon(ring)
        if not hole.is_valid or hole.is_empty:
            raise OsmEvidenceError("invalid_inner_ring")
        parents = [i for i, shell in enumerate(shells) if shell.contains(hole)]
        if len(parents) != 1:
            raise OsmEvidenceError("unassigned_or_ambiguous_hole")
        holes[parents[0]].append(ring)
    polygons = [Polygon(ring, holes[i]) for i, ring in enumerate(outers)]
    polygon = polygons[0] if len(polygons) == 1 else MultiPolygon(polygons)
    if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
        raise OsmEvidenceError("invalid_relation_polygon")
    return polygon


def parse_buildings(payload: dict[str, Any]) -> dict[str, Any]:
    """Decode complete parent-building footprints, preserving relation holes."""
    if payload.get("remark") or payload.get("error"):
        raise OsmEvidenceError("OSM: Overpass вернул неполный ответ или ошибку")
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise OsmEvidenceError("OSM: ответ не содержит список объектов")
    if any(not isinstance(element, dict) for element in elements):
        raise OsmEvidenceError("OSM: недопустимая строка объекта")
    features = []
    rejected = []
    relation_members: set[int] = set()
    for element in sorted(elements, key=lambda row: row.get("type") != "relation"):
        tags = element.get("tags", {})
        if not isinstance(tags, dict) or tags.get("building") in (None, "no"):
            continue
        identifier = f"{element.get('type')}/{element.get('id')}"
        if tags.get("building:part") not in (None, "no"):
            rejected.append({"id": identifier, "reason": "building_part_not_parent"})
            continue
        if element.get("type") == "way" and element.get("id") in relation_members:
            continue
        polygon: Polygon | MultiPolygon
        try:
            if element.get("type") == "way":
                coordinates = _coordinates(element.get("geometry"))
                nodes = element.get("nodes")
                if (
                    len(coordinates) < 4
                    or coordinates[0] != coordinates[-1]
                    or not isinstance(nodes, list)
                    or len(nodes) != len(coordinates)
                    or nodes[0] != nodes[-1]
                ):
                    raise OsmEvidenceError("open_or_incomplete_way")
                polygon = Polygon(coordinates)
                if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
                    raise OsmEvidenceError("invalid_way_polygon")
            elif element.get("type") == "relation":
                if tags.get("type") != "multipolygon":
                    raise OsmEvidenceError("unsupported_relation_type")
                polygon = _relation_polygon(element)
                relation_members.update(
                    member["ref"]
                    for member in element["members"]
                    if member.get("type") == "way"
                    and isinstance(member.get("ref"), int)
                )
            else:
                continue
        except OsmEvidenceError as error:
            rejected.append({"id": identifier, "reason": str(error)})
            continue
        features.append(
            {
                "type": "Feature",
                "id": identifier,
                "geometry": mapping(polygon),
                "properties": {
                    "osm_id": element.get("id"),
                    "osm_type": element.get("type"),
                    "osm_version": element.get("version"),
                    "osm_timestamp": element.get("timestamp"),
                    "tags": tags,
                },
            }
        )
    return {"features": features, "rejected": rejected}


def _request(query: str) -> bytes:
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            with client.stream(
                "POST",
                OVERPASS_ENDPOINT,
                data={"data": query},
                headers={"User-Agent": "GreenAtlas-local-height-evidence/1"},
            ) as response:
                response.raise_for_status()
                chunks = []
                count = 0
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > MAX_RESPONSE_BYTES:
                        raise OsmEvidenceError("OSM: ответ превышает 16 МиБ")
                    chunks.append(chunk)
                return b"".join(chunks)
    except httpx.HTTPError as error:
        raise OsmEvidenceError(
            "OSM: данные высот недоступны; повторите загрузку позже"
        ) from error


def fetch_buildings(
    bbox: tuple[float, float, float, float], cache_directory: Path
) -> dict[str, Any]:
    """Fetch once per bounded area; only checksum-valid unexpired cache is used."""
    query = _query(bbox)
    key = sha256(f"{OVERPASS_ENDPOINT}\n{query}".encode()).hexdigest()
    metadata_path = cache_directory / f"{key}.json"
    raw_path = cache_directory / f"{key}.response.json"
    now = datetime.now(UTC)
    cached = False
    metadata: dict[str, Any] = {}
    raw: bytes | None = None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("invalid cache metadata")
        fetched_at = datetime.fromisoformat(metadata["fetched_at"])
        if (
            metadata.get("endpoint") == OVERPASS_ENDPOINT
            and metadata.get("query") == query
            and fetched_at.tzinfo is not None
            and timedelta() <= now - fetched_at < CACHE_TTL
            and raw_path.stat().st_size <= MAX_RESPONSE_BYTES
        ):
            candidate = raw_path.read_bytes()
            if sha256(candidate).hexdigest() == metadata.get("source_sha256"):
                raw, cached = candidate, True
    except (OSError, ValueError, KeyError, TypeError):
        pass
    if raw is None:
        raw = _request(query)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise OsmEvidenceError("OSM: ответ превышает 16 МиБ")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise OsmEvidenceError("OSM: недопустимый JSON ответа")
        parsed = parse_buildings(payload)
    except (ValueError, TypeError, KeyError) as error:
        if isinstance(error, OsmEvidenceError):
            raise
        raise OsmEvidenceError("OSM: повреждённый ответ источника") from error
    if not cached:
        source_metadata = payload.get("osm3s")
        metadata = {
            "endpoint": OVERPASS_ENDPOINT,
            "query": query,
            "source_sha256": sha256(raw).hexdigest(),
            "fetched_at": now.isoformat(),
            "osm_timestamp": source_metadata.get("timestamp_osm_base")
            if isinstance(source_metadata, dict)
            else None,
            "attribution": ATTRIBUTION,
        }
        cache_directory.mkdir(parents=True, exist_ok=True)
        # Independent temporary paths allow concurrent identical requests safely.
        nonce = uuid4().hex
        pending_raw = cache_directory / f".{key}.{nonce}.response.pending"
        pending_metadata = cache_directory / f".{key}.{nonce}.metadata.pending"
        try:
            pending_raw.write_bytes(raw)
            pending_metadata.write_text(
                json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
            )
            pending_raw.replace(raw_path)
            pending_metadata.replace(metadata_path)
        finally:
            pending_raw.unlink(missing_ok=True)
            pending_metadata.unlink(missing_ok=True)
    return {
        "type": "FeatureCollection",
        **parsed,
        "provenance": {**metadata, "cache_status": "hit" if cached else "fresh"},
    }


@dataclass(frozen=True)
class CandidateIndex:
    rows: list[dict[str, Any]]
    geometries: list[Polygon | MultiPolygon]
    tree: STRtree


def prepare_candidates(candidates: list[dict[str, Any]]) -> CandidateIndex:
    """Build an index only over complete parent polygons in a metric frame."""
    rows = []
    geometries = []
    for candidate in candidates:
        tags = candidate.get("tags", {})
        if not isinstance(tags, dict) or tags.get("building:part") not in (None, "no"):
            continue
        if tags.get("building") == "no":
            continue
        geometry = candidate.get("geometry")
        try:
            polygon = shape(geometry) if isinstance(geometry, dict) else geometry
            if (
                not isinstance(polygon, (Polygon, MultiPolygon))
                or polygon.is_empty
                or not polygon.is_valid
                or polygon.area <= 0
                or not all(math.isfinite(value) for value in polygon.bounds)
            ):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        rows.append({**candidate, "tags": dict(tags)})
        geometries.append(polygon)
    return CandidateIndex(rows, geometries, STRtree(geometries))


def match_height(
    footprint: Polygon | MultiPolygon,
    candidates: list[dict[str, Any]] | CandidateIndex,
) -> dict[str, Any]:
    """Transfer evidence only on strong, unambiguous polygon overlap.

    Matching is per CAD footprint, allowing duplicate CAD drawings to share an OSM
    object. Split/merged footprints stay ambiguous instead of inheriting one part's
    height. Holes participate in all coverage and area calculations.
    """
    result: dict[str, Any] = {
        "match_status": "unmatched",
        "reason": "no_overlap",
        "osm_id": None,
        "height_m": None,
        "height_source": None,
        "height_status": "missing",
        "building_class": None,
        "footprint_coverage": 0.0,
        "osm_coverage": 0.0,
        "intersection_over_union": 0.0,
        "candidates": [],
    }
    if (
        not isinstance(footprint, (Polygon, MultiPolygon))
        or footprint.is_empty
        or not footprint.is_valid
        or footprint.area <= 0
        or not all(math.isfinite(value) for value in footprint.bounds)
    ):
        return {**result, "reason": "invalid_footprint"}
    index = (
        candidates
        if isinstance(candidates, CandidateIndex)
        else prepare_candidates(candidates)
    )
    overlaps: list[dict[str, Any]] = []
    for value in index.tree.query(footprint):
        offset = int(value)
        polygon = index.geometries[offset]
        area = footprint.intersection(polygon).area
        if area <= 0:
            continue
        overlaps.append(
            {
                "osm_id": str(index.rows[offset]["id"]),
                "footprint_coverage": area / footprint.area,
                "osm_coverage": area / polygon.area,
                "intersection_over_union": area
                / (footprint.area + polygon.area - area),
                "offset": offset,
            }
        )
    overlaps.sort(key=lambda row: (-row["intersection_over_union"], row["osm_id"]))
    result["candidates"] = [
        {key: value for key, value in row.items() if key != "offset"}
        for row in overlaps
    ]
    strong = [
        row
        for row in overlaps
        if row["footprint_coverage"] >= 0.65
        and row["osm_coverage"] >= 0.65
        and row["intersection_over_union"] >= 0.5
    ]
    if not strong:
        if len([row for row in overlaps if row["footprint_coverage"] >= 0.2]) > 1:
            return {
                **result,
                "match_status": "ambiguous",
                "reason": "split_or_merged_footprint",
            }
        return {
            **result,
            "reason": "insufficient_overlap" if overlaps else "no_overlap",
        }
    best = strong[0]
    competing = [
        row
        for row in overlaps
        if row["offset"] != best["offset"]
        and row["footprint_coverage"] >= 0.2
        and row["osm_coverage"] >= 0.5
    ]
    if len(strong) > 1 or competing:
        return {**result, "match_status": "ambiguous", "reason": "competing_buildings"}
    row = index.rows[best["offset"]]
    height, source = parse_height(row["tags"])
    return {
        **result,
        **{key: value for key, value in best.items() if key != "offset"},
        "match_status": "matched",
        "reason": "polygon_overlap",
        "height_m": height,
        "height_source": source,
        "height_status": "estimated" if height is not None else "missing",
        "building_class": row["tags"].get("building"),
        "osm_tags": row["tags"],
    }
