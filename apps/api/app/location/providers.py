"""Bounded address and map evidence. Nothing here establishes a CAD transform.

DaData Suggest supplies address identity, not survey accuracy. OSM supplies
connected named highway sections; disconnected sections remain explicit choices.
Only an explicitly selected map section may crop a street before buffering.
"""

from __future__ import annotations

import json
import math
import re
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from shapely.geometry import LineString, MultiLineString, box, mapping, shape
from shapely.ops import transform

from app.location.settings import (
    LocationProviderError as LocationProviderError,
)
from app.location.settings import (
    dadata_settings_path as dadata_settings_path,
)
from app.location.settings import (
    load_dadata_token as load_dadata_token,
)
from app.location.settings import (
    save_dadata_token as _save_dadata_token,
)

DADATA_ENDPOINT = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"
EARTH_RADIUS_M = 6_378_137
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_DADATA_BYTES = 512 * 1024
MAX_ELEMENTS = 2000
MAX_POINTS = 100_000
SEARCH_RADIUS_M = 5000
CACHE_TTL = timedelta(days=7)
MAX_CACHE_ENTRIES = 32
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_BBOX_AREA_M2 = 4_000_000
AddressKind = Literal["city", "street"]


def save_dadata_token(token: str) -> None:
    """Preserve the private desktop endpoint's validation and shared storage."""
    if (
        not isinstance(token, str)
        or not 8 <= len(token) <= 256
        or not token.isascii()
        or not token.isprintable()
        or token != token.strip()
    ):
        raise ValueError("Недопустимый API-ключ DaData")
    _save_dadata_token(token)


def _request_json(
    endpoint: str,
    *,
    client: httpx.Client | None,
    max_bytes: int,
    **kwargs: Any,
) -> tuple[dict[str, Any], bytes]:
    provider = "DaData" if endpoint == DADATA_ENDPOINT else "OSM"
    try:
        context = (
            nullcontext(client)
            if client is not None
            else httpx.Client(
                timeout=httpx.Timeout(25, connect=5), follow_redirects=False
            )
        )
        with context as transport:
            with transport.stream(
                "POST", endpoint, timeout=httpx.Timeout(25, connect=5), **kwargs
            ) as response:
                if response.status_code in (401, 403):
                    raise LocationProviderError(
                        "provider_auth",
                        f"{provider}: проверьте API-ключ и доступный лимит",
                    )
                if response.status_code == 429:
                    raise LocationProviderError(
                        "provider_rate_limit",
                        f"{provider}: лимит запросов, повторите позже",
                    )
                response.raise_for_status()
                chunks = []
                length = 0
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if length > max_bytes:
                        raise LocationProviderError(
                            "response_limit",
                            f"{provider}: ответ превышает допустимый размер",
                        )
                    chunks.append(chunk)
        raw = b"".join(chunks)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("object required")
        return payload, raw
    except httpx.HTTPError:
        raise LocationProviderError(
            "provider_unavailable", f"{provider}: источник недоступен, повторите позже"
        ) from None
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        if isinstance(error, LocationProviderError):
            raise
        raise LocationProviderError(
            "invalid_response", f"{provider}: некорректный ответ источника"
        ) from None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _point(lon: Any, lat: Any) -> tuple[float, float]:
    try:
        if isinstance(lon, bool) or isinstance(lat, bool):
            raise ValueError("boolean")
        x, y = float(lon), float(lat)
        if (
            not math.isfinite(x)
            or not math.isfinite(y)
            or not (-180 <= x <= 180 and -85 <= y <= 85)
        ):
            raise ValueError("range")
        return x, y
    except (TypeError, ValueError, OverflowError):
        raise LocationProviderError(
            "invalid_coordinates", "Недопустимые координаты WGS84"
        ) from None


def _suggestion(row: Any, kind: AddressKind) -> dict[str, Any]:
    if not isinstance(row, dict) or not isinstance(row.get("data"), dict):
        raise LocationProviderError(
            "invalid_response", "DaData: некорректная подсказка"
        )
    data = row["data"]
    identifier = _text(data.get(f"{kind}_fias_id"))
    label = _text(row.get("unrestricted_value"))
    if not identifier or not label:
        raise LocationProviderError(
            "invalid_response", "DaData: подсказка без адресного идентификатора"
        )
    longitude = latitude = None
    try:
        longitude, latitude = _point(data.get("geo_lon"), data.get("geo_lat"))
    except LocationProviderError:
        pass
    quality = data.get("qc_geo")
    qc_geo = int(quality) if str(quality) in {"0", "1", "2", "3", "4", "5"} else None
    fields = (
        "city",
        "city_fias_id",
        "city_with_type",
        "settlement",
        "settlement_fias_id",
        "region",
        "region_fias_id",
        "street",
        "street_fias_id",
        "street_with_type",
        "street_type",
        "street_type_full",
        "fias_id",
    )
    return {
        **{key: _text(data.get(key)) for key in fields},
        "id": identifier,
        "label": label,
        "kind": kind,
        "value": _text(row.get("value")) or label,
        "unrestricted_value": label,
        "longitude": longitude,
        "latitude": latitude,
        "qc_geo": qc_geo,
        "provenance": {"source": "dadata_suggest", "endpoint": DADATA_ENDPOINT},
    }


def _address_request(
    query: str, kind: AddressKind, city_fias_id: str | None, count: int
) -> dict[str, Any]:
    if (
        kind not in ("city", "street")
        or not isinstance(query, str)
        or not 1 <= len(query.strip()) <= 300
    ):
        raise LocationProviderError(
            "invalid_query", "Введите адрес длиной от 1 до 300 символов"
        )
    body: dict[str, Any] = {
        "query": query.strip(),
        "count": count,
        "from_bound": {"value": kind},
        "to_bound": {"value": kind},
        "locations": [{"country_iso_code": "RU"}],
    }
    if city_fias_id:
        body["locations"][0]["city_fias_id"] = city_fias_id
        body["restrict_value"] = True
    return body


def _dadata(
    body: dict[str, Any], token: str | None, client: httpx.Client | None
) -> list[Any]:
    key = token if token is not None else load_dadata_token()
    if not isinstance(key, str) or not key.strip():
        raise LocationProviderError(
            "not_configured", "Добавьте API-ключ DaData для поиска адреса"
        )
    payload, _ = _request_json(
        DADATA_ENDPOINT,
        client=client,
        max_bytes=MAX_DADATA_BYTES,
        json=body,
        headers={"Authorization": f"Token {key.strip()}", "Accept": "application/json"},
    )
    if payload.get("error") or payload.get("message"):
        raise LocationProviderError("invalid_response", "DaData: ошибка источника")
    rows = payload.get("suggestions")
    if not isinstance(rows, list) or len(rows) > body["count"]:
        raise LocationProviderError(
            "invalid_response", "DaData: некорректный список подсказок"
        )
    return rows


def suggest_addresses(
    query: str,
    *,
    kind: AddressKind = "street",
    city_fias_id: str | None = None,
    token: str | None = None,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    body = _address_request(query, kind, city_fias_id, 10)
    rows = []
    for row in _dadata(body, token, client):
        # A street query can also return a metro station with the same name.
        # It is not a selectable street; keep strict validation for actual rows.
        if (
            kind == "street"
            and isinstance(row, dict)
            and isinstance(row.get("data"), dict)
            and not row["data"].get("street_fias_id")
        ):
            continue
        rows.append(_suggestion(row, kind))
    if city_fias_id and any(row["city_fias_id"] != city_fias_id for row in rows):
        raise LocationProviderError(
            "address_mismatch", "DaData: адрес не относится к выбранному городу"
        )
    return rows


def select_address(
    unrestricted_value: str,
    *,
    kind: AddressKind,
    expected_fias_id: str,
    city_fias_id: str | None = None,
    token: str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Re-query the chosen suggestion with count=1, then check its FIAS identity."""
    body = _address_request(unrestricted_value, kind, city_fias_id, 1)
    rows = _dadata(body, token, client)
    if len(rows) != 1:
        raise LocationProviderError(
            "address_not_found", "Выбранный адрес больше не найден в DaData"
        )
    selected = _suggestion(rows[0], kind)
    if selected["id"] != expected_fias_id or (
        city_fias_id and selected["city_fias_id"] != city_fias_id
    ):
        raise LocationProviderError(
            "address_mismatch", "DaData вернула другой адрес, выберите подсказку заново"
        )
    selected["provenance"]["selected_at"] = datetime.now(UTC).isoformat()
    return selected


_STREET_TYPES = {
    "ул": "улица",
    "улица": "улица",
    "просп": "проспект",
    "пркт": "проспект",
    "проспект": "проспект",
    "пер": "переулок",
    "переулок": "переулок",
    "прд": "проезд",
    "проезд": "проезд",
    "бульвар": "бульвар",
    "бр": "бульвар",
    "ш": "шоссе",
    "шоссе": "шоссе",
    "наб": "набережная",
    "набережная": "набережная",
    "пл": "площадь",
    "площадь": "площадь",
    "аллея": "аллея",
    "туп": "тупик",
    "тупик": "тупик",
}


def normalize_street_name(name: str) -> tuple[str, str | None]:
    """Keep the type distinct: улица Мира and проспект Мира are not synonyms."""
    value = name.casefold().replace("ё", "е")
    value = re.sub(r"\b(пр-т|пр-кт)\b", "пркт", value)
    value = re.sub(r"\b(б-р|пр-д)\b", lambda match: match[0].replace("-", ""), value)
    words = re.findall(r"[\w-]+", value)
    street_type = None
    if words and words[0] in _STREET_TYPES:
        street_type = _STREET_TYPES[words.pop(0)]
    elif words and words[-1] in _STREET_TYPES:
        street_type = _STREET_TYPES[words.pop()]
    return " ".join(words), street_type


def _street_identity(address: dict[str, Any]) -> tuple[str, str | None]:
    name = _text(address.get("street_with_type")) or " ".join(
        value
        for field in ("street_type_full", "street")
        if (value := _text(address.get(field)))
    )
    identity = normalize_street_name(name)
    if not identity[0]:
        raise LocationProviderError(
            "street_required", "Сначала выберите улицу из подсказок"
        )
    return identity


def _street_query(address: dict[str, Any], point: tuple[float, float]) -> str:
    # A literal name token narrows dense cities without depending on type order.
    # Full identity, including type, is checked locally after the bounded query.
    name, _ = _street_identity(address)
    token = max(name.split(), key=len)
    escaped = re.sub(r"([\\.^$|()\[\]{}*+?])", r"\\\1", token).replace("е", "[её]")
    pattern = json.dumps(escaped, ensure_ascii=False)
    lon, lat = point
    return (
        "[out:json][timeout:20][maxsize:8388608];"
        f'way["highway"]["name"~{pattern},i]'
        f"(around:{SEARCH_RADIUS_M},{lat:.8f},{lon:.8f});out meta geom;"
    )


def _cache_read(
    path: Path, query: str, now: datetime
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    try:
        if path.stat().st_size > MAX_RESPONSE_BYTES * 2:
            return None
        entry = json.loads(path.read_text(encoding="utf-8"))
        meta, raw = entry["provenance"], entry["response"]
        age = now - datetime.fromisoformat(meta["fetched_at"])
        if (
            not (timedelta() <= age < CACHE_TTL)
            or meta["query"] != query
            or meta["endpoint"] != OVERPASS_ENDPOINT
        ):
            return None
        if (
            not isinstance(raw, str)
            or len(raw.encode()) > MAX_RESPONSE_BYTES
            or sha256(raw.encode()).hexdigest() != meta["source_sha256"]
        ):
            return None
        payload = json.loads(raw)
        return (payload, meta) if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _cache_write(path: Path, payload: dict[str, Any], now: datetime) -> None:
    """One atomic envelope; retain at most 32 entries / 64 MiB for seven days."""
    pending = path.with_name(f".{path.stem}.{uuid4().hex}.pending")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        pending.replace(path)
        files = []
        for candidate in path.parent.glob("streets-*.json"):
            stat = candidate.stat()
            if now.timestamp() - stat.st_mtime >= CACHE_TTL.total_seconds():
                candidate.unlink(missing_ok=True)
            else:
                files.append((stat.st_mtime, stat.st_size, candidate))
        total = 0
        for index, (_, size, candidate) in enumerate(sorted(files, reverse=True)):
            total += size
            if index >= MAX_CACHE_ENTRIES or total > MAX_CACHE_BYTES:
                candidate.unlink(missing_ok=True)
    except OSError:
        # A cache failure does not change the validity of a fetched response.
        pass
    finally:
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            pass


def _street_candidates(
    payload: dict[str, Any], address: dict[str, Any]
) -> list[dict[str, Any]]:
    if payload.get("remark") or payload.get("error"):
        raise LocationProviderError(
            "incomplete_response", "OSM: неполный ответ, повторите запрос"
        )
    elements = payload.get("elements")
    if not isinstance(elements, list) or len(elements) > MAX_ELEMENTS:
        raise LocationProviderError(
            "response_limit",
            "OSM: недопустимое количество объектов, уточните точку на карте",
        )
    wanted_name, wanted_type = _street_identity(address)
    ways: list[dict[str, Any]] = []
    total_points = 0
    seen: set[int] = set()
    for element in elements:
        if not isinstance(element, dict):
            raise LocationProviderError(
                "invalid_response", "OSM: некорректная строка объекта"
            )
        tags = element.get("tags")
        if (
            element.get("type") != "way"
            or not isinstance(tags, dict)
            or not tags.get("highway")
        ):
            raise LocationProviderError(
                "invalid_response", "OSM: ответ не соответствует запросу улиц"
            )
        name = _text(tags.get("name"))
        if not name:
            raise LocationProviderError(
                "invalid_response", "OSM: в ответе отсутствует название улицы"
            )
        actual_name, actual_type = normalize_street_name(name)
        if actual_name != wanted_name or (wanted_type and actual_type != wanted_type):
            continue
        identifier, nodes, coords = (
            element.get("id"),
            element.get("nodes"),
            element.get("geometry"),
        )
        if (
            not isinstance(identifier, int)
            or isinstance(identifier, bool)
            or identifier in seen
            or not isinstance(nodes, list)
            or not isinstance(coords, list)
            or len(nodes) != len(coords)
            or len(coords) < 2
            or any(
                not isinstance(node, int) or isinstance(node, bool) for node in nodes
            )
        ):
            raise LocationProviderError(
                "incomplete_response", "OSM: неполная геометрия выбранной улицы"
            )
        total_points += len(coords)
        if total_points > MAX_POINTS:
            raise LocationProviderError(
                "response_limit", "OSM: слишком сложная геометрия улицы"
            )
        if any(not isinstance(point, dict) for point in coords):
            raise LocationProviderError(
                "incomplete_response", "OSM: неполная геометрия выбранной улицы"
            )
        points = [_point(point.get("lon"), point.get("lat")) for point in coords]
        line = LineString(points)
        if line.length <= 0 or not line.is_valid:
            raise LocationProviderError(
                "invalid_response", "OSM: недопустимая геометрия улицы"
            )
        seen.add(identifier)
        ways.append(
            {
                "id": identifier,
                "nodes": nodes,
                "points": points,
                "name": name,
                "version": element.get("version"),
                "timestamp": element.get("timestamp"),
            }
        )
    # Merge by actual shared OSM node, never by apparent crossing or proximity.
    groups: list[list[dict[str, Any]]] = []
    node_groups: dict[tuple[int, str | None], int] = {}
    node_coordinates: dict[int, tuple[float, float]] = {}
    parents = list(range(len(ways)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for index, way in enumerate(ways):
        for node, point in zip(way["nodes"], way["points"], strict=True):
            if node in node_coordinates and node_coordinates[node] != point:
                raise LocationProviderError(
                    "incomplete_response", "OSM: координаты общего узла не совпадают"
                )
            node_coordinates[node] = point
            node_key = (node, normalize_street_name(way["name"])[1])
            if node_key in node_groups:
                parents[root(index)] = root(node_groups[node_key])
            node_groups[node_key] = index
    by_root: dict[int, list[dict[str, Any]]] = {}
    for index, way in enumerate(ways):
        by_root.setdefault(root(index), []).append(way)
    groups = list(by_root.values())
    candidates = []
    for group in groups:
        group.sort(key=lambda way: way["id"])
        geometry = MultiLineString([way["points"] for way in group])
        ids = [f"way/{way['id']}" for way in group]
        candidates.append(
            {
                "id": "osm-street-" + sha256(",".join(ids).encode()).hexdigest()[:24],
                "label": group[0]["name"],
                "name": group[0]["name"],
                "osm_ids": ids,
                "geometry": mapping(geometry),
                "bbox": list(geometry.bounds),
                "warnings": ["bounded_street_section"]
                + (["disconnected_candidates"] if len(groups) > 1 else []),
                "provenance": {
                    "source": "openstreetmap",
                    "attribution": ATTRIBUTION,
                    "scope": "connected_named_highways_in_search_radius",
                    "ways": [
                        {key: way[key] for key in ("id", "version", "timestamp")}
                        for way in group
                    ],
                },
            }
        )
    return sorted(candidates, key=lambda row: row["id"])


def search_streets(
    address: dict[str, Any],
    cache_root: Path,
    *,
    near_point: tuple[float, float] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if near_point is None:
        if isinstance(address.get("qc_geo"), bool) or address.get("qc_geo") not in (
            0,
            1,
            2,
        ):
            raise LocationProviderError(
                "manual_location_required",
                "Укажите точку улицы на карте: DaData не вернула координаты "
                "с точностью до улицы",
            )
        try:
            point = _point(address.get("longitude"), address.get("latitude"))
        except LocationProviderError:
            raise LocationProviderError(
                "manual_location_required",
                "Укажите точку улицы на карте: координаты адреса отсутствуют",
            ) from None
    else:
        if len(near_point) != 2:
            raise LocationProviderError(
                "invalid_coordinates",
                "Точка на карте должна содержать долготу и широту",
            )
        point = _point(*near_point)
    query = _street_query(address, point)
    key = sha256(f"{OVERPASS_ENDPOINT}\n{query}".encode()).hexdigest()
    path = cache_root / f"streets-{key}.json"
    now = datetime.now(UTC)
    cached = _cache_read(path, query, now)
    if cached:
        payload, provenance = cached
    else:
        payload, raw = _request_json(
            OVERPASS_ENDPOINT,
            client=client,
            max_bytes=MAX_RESPONSE_BYTES,
            data={"data": query},
            headers={"User-Agent": "GreenAtlas-local-location-evidence/1"},
        )
        provenance = {
            "endpoint": OVERPASS_ENDPOINT,
            "query": query,
            "fetched_at": now.isoformat(),
            "source_sha256": sha256(raw).hexdigest(),
            "attribution": ATTRIBUTION,
            "osm_timestamp": (payload.get("osm3s") or {}).get("timestamp_osm_base")
            if isinstance(payload.get("osm3s"), dict)
            else None,
        }
    candidates = _street_candidates(payload, address)
    if not cached:
        _cache_write(
            path, {"provenance": provenance, "response": raw.decode("utf-8")}, now
        )
    provenance = {
        **provenance,
        "cache_status": "hit" if cached else "fresh",
        "center_lon_lat": list(point),
        "radius_m": SEARCH_RADIUS_M,
        "center_source": "manual_map" if near_point is not None else "dadata_street",
    }
    for candidate in candidates:
        candidate["provenance"].update(provenance)
    return {
        "candidates": candidates,
        "attribution": ATTRIBUTION,
        "fetched_at": provenance["fetched_at"],
        "cache_hit": bool(cached),
        "provenance": provenance,
    }


def _bbox_bounds(bounds: Any) -> tuple[float, float, float, float]:
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise LocationProviderError(
            "invalid_bbox", "Укажите прямоугольную область на карте"
        )
    west, south = _point(bounds[0], bounds[1])
    east, north = _point(bounds[2], bounds[3])
    if west >= east or south >= north:
        raise LocationProviderError(
            "invalid_bbox", "Недопустимые границы области на карте"
        )
    return west, south, east, north


def build_street_corridor(
    candidate: dict[str, Any],
    *,
    buffer_m: float = 120,
    section_bbox: tuple[float, float, float, float] | list[float] | None = None,
) -> dict[str, Any]:
    """Buffer in local metres, checking the resulting building-request bbox.

    Local equirectangular WGS84 is adequate for a <=5 km display-evidence area.
    It does not claim cadastral/survey accuracy or establish the CAD frame.
    """
    if (
        isinstance(buffer_m, bool)
        or not isinstance(buffer_m, (int, float))
        or not math.isfinite(buffer_m)
        or not 20 <= buffer_m <= 500
    ):
        raise LocationProviderError(
            "invalid_buffer", "Ширина окружения должна быть от 20 до 500 метров"
        )
    try:
        geometry = shape(candidate["geometry"])
    except (TypeError, KeyError, ValueError):
        raise LocationProviderError(
            "invalid_street", "Недопустимая геометрия выбранной улицы"
        ) from None
    if (
        not isinstance(geometry, (LineString, MultiLineString))
        or geometry.is_empty
        or not geometry.is_valid
    ):
        raise LocationProviderError(
            "invalid_street", "Недопустимая геометрия выбранной улицы"
        )
    if section_bbox is not None:
        selection = _bbox_bounds(section_bbox)
        geometry = geometry.intersection(box(*selection))
        if (
            not isinstance(geometry, (LineString, MultiLineString))
            or geometry.is_empty
            or geometry.length == 0
        ):
            raise LocationProviderError(
                "empty_section", "Выбранная область не пересекает улицу"
            )
    west, south, east, north = geometry.bounds
    _point(west, south)
    _point(east, north)
    lon0, lat0 = (west + east) / 2, (south + north) / 2
    x_factor = EARTH_RADIUS_M * math.pi / 180 * math.cos(math.radians(lat0))
    y_factor = EARTH_RADIUS_M * math.pi / 180
    def to_metric(
        x: float, y: float, z: float | None = None
    ) -> tuple[float, float]:
        # Прежний двумерный callback тоже отвергал третий аргумент transform.
        if z is not None:
            raise TypeError("Координаты улицы должны быть двумерными")
        return (x - lon0) * x_factor, (y - lat0) * y_factor

    def to_geographic(
        x: float, y: float, z: float | None = None
    ) -> tuple[float, float]:
        if z is not None:
            raise TypeError("Координаты улицы должны быть двумерными")
        return x / x_factor + lon0, y / y_factor + lat0

    metric = transform(to_metric, geometry)
    buffered = metric.buffer(buffer_m)
    corridor = transform(to_geographic, buffered)
    bounds = _bbox_bounds(corridor.bounds)
    width = (
        EARTH_RADIUS_M
        * math.radians(bounds[2] - bounds[0])
        * math.cos(math.radians((bounds[1] + bounds[3]) / 2))
    )
    height = EARTH_RADIUS_M * math.radians(bounds[3] - bounds[1])
    if width * height > MAX_BBOX_AREA_M2 or max(width, height) > 5000:
        raise LocationProviderError(
            "area_too_large",
            "Область окружения превышает 4 км² или 5 км. "
            "Выберите меньший участок улицы на карте",
        )
    return {
        "bbox": list(bounds),
        "corridor": mapping(corridor),
        "area_m2": buffered.area,
        "bbox_area_m2": width * height,
        "buffer_m": buffer_m,
        "provenance": {
            **candidate.get("provenance", {}),
            "selected_candidate_id": candidate.get("id"),
            "osm_ids": candidate.get("osm_ids", []),
            "scope": "explicit_map_section"
            if section_bbox is not None
            else "selected_street_component",
            "section_bbox": list(section_bbox) if section_bbox is not None else None,
            "projection": "local_equirectangular_wgs84",
            "reference_lon_lat": [lon0, lat0],
        },
    }
