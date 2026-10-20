"""Pure, bounded similarity fits; cartographic controls never become survey data."""

from __future__ import annotations

import math
import re
from itertools import combinations
from typing import Any

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import transform as transform_geometry

from app.location.contracts import LocationControlPair, LocationTransform

EARTH_RADIUS_M = 6_378_137.0
_ADDRESS = re.compile(
    r"^(\d+[а-яa-z]?)(?:\s*(?:к|корпус|корп\.?)\s*(\d+[а-яa-z]?))?"
    r"(?:\s*(?:с|стр\.?|строение)\s*(\d+[а-яa-z]?))?$",
    re.IGNORECASE,
)


def normalize_address(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    text = re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip()
    match = _ADDRESS.fullmatch(text)
    if not match:
        return None
    return (
        match[1]
        + (f" к{match[2]}" if match[2] else "")
        + (f" с{match[3]}" if match[3] else "")
    )


def tangent(
    point: tuple[float, float],
    reference: tuple[float, float],
    radius: float = EARTH_RADIUS_M,
) -> tuple[float, float]:
    lon, lat = point
    lon0, lat0 = reference
    return (
        radius * math.radians(lon - lon0) * math.cos(math.radians(lat0)),
        radius * math.radians(lat - lat0),
    )


def apply_point(
    point: tuple[float, float], scale: float, rotation: Any, translation: Any
) -> tuple[float, float]:
    x, y = point
    return (
        scale * (x * rotation[0][0] + y * rotation[1][0]) + translation[0],
        scale * (x * rotation[0][1] + y * rotation[1][1]) + translation[1],
    )


def _fit(
    source: list[tuple[float, float]], target: list[tuple[float, float]]
) -> tuple[float, Any, Any]:
    sx, sy = (sum(p[i] for p in source) / len(source) for i in (0, 1))
    tx, ty = (sum(p[i] for p in target) / len(target) for i in (0, 1))
    left = [(x - sx, y - sy) for x, y in source]
    right = [(x - tx, y - ty) for x, y in target]
    denominator = sum(x * x + y * y for x, y in left)
    if denominator < 1:
        raise ValueError("Опорные точки расположены слишком близко")
    a = sum(x * u + y * v for (x, y), (u, v) in zip(left, right)) / denominator
    b = sum(x * v - y * u for (x, y), (u, v) in zip(left, right)) / denominator
    scale = math.hypot(a, b)
    if not 0.95 <= scale <= 1.05:
        raise ValueError(
            "Масштаб привязки не соответствует метрическим координатам CAD"
        )
    rotation = ((a / scale, b / scale), (-b / scale, a / scale))
    translation = (tx - a * sx + b * sy, ty - b * sx - a * sy)
    return scale, rotation, translation


def _spatial(points: list[tuple[float, float]]) -> bool:
    if len(points) < 3:
        return False
    extent = max(max(p[i] for p in points) - min(p[i] for p in points) for i in (0, 1))
    if extent < 15:
        return False
    return any(
        abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
        > extent * extent * 0.001
        for a, b, c in combinations(points, 3)
    )


def fit_controls(
    pairs: list[LocationControlPair],
    *,
    robust: bool,
    cad_bounds: tuple[float, float, float, float],
) -> tuple[LocationTransform, dict[str, float | int]]:
    fit = [p for p in pairs if p.usage == "fit"]
    holdout = [p for p in pairs if p.usage == "holdout"]
    if len(fit) < 3 or not holdout:
        raise ValueError(
            "Нужны минимум три опорные пары и одна отдельная проверочная пара"
        )
    if len({p.cad_xy for p in pairs}) != len(pairs) or len(
        {p.lon_lat for p in pairs}
    ) != len(pairs):
        raise ValueError("Опорные и проверочные точки должны быть разными")
    if any(math.dist(a.cad_xy, b.cad_xy) < 1 for a, b in combinations(pairs, 2)):
        raise ValueError("Разнесите контрольные точки минимум на метр")
    reference = (
        sum(p.lon_lat[0] for p in fit) / len(fit),
        sum(p.lon_lat[1] for p in fit) / len(fit),
    )
    source = [tangent(p.lon_lat, reference) for p in fit]
    target = [p.cad_xy for p in fit]
    if not _spatial(source) or not _spatial(target):
        raise ValueError(
            "Опорные точки должны охватывать участок и не лежать на одной прямой"
        )
    inliers = list(range(len(fit)))
    if robust:
        candidates = []
        for a, b in combinations(range(len(fit)), 2):
            if math.dist(source[a], source[b]) < 15:
                continue
            try:
                model = _fit([source[a], source[b]], [target[a], target[b]])
            except ValueError:
                continue
            residuals = [
                math.dist(apply_point(p, *model), q) for p, q in zip(source, target)
            ]
            selected = [i for i, residual in enumerate(residuals) if residual <= 3]
            if len(selected) >= 3 and _spatial([target[i] for i in selected]):
                candidates.append(
                    (len(selected), -sum(residuals[i] ** 2 for i in selected), selected)
                )
        if not candidates:
            raise ValueError("Адресные соответствия не дали согласованную привязку")
        inliers = max(candidates, key=lambda row: row[:2])[2]
    model = _fit([source[i] for i in inliers], [target[i] for i in inliers])
    residuals = [math.dist(apply_point(source[i], *model), target[i]) for i in inliers]
    rmse = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    if rmse > 2 or max(residuals) > 3:
        raise ValueError(
            "Точки расходятся: допустимое среднее отклонение 2 м, максимальное 3 м"
        )
    checks = [
        math.dist(apply_point(tangent(p.lon_lat, reference), *model), p.cad_xy)
        for p in holdout
    ]
    if max(checks) > 5:
        raise ValueError("Независимая проверочная точка отклоняется более чем на 5 м")
    # A reviewed local fit has finite support; never extrapolate across a whole city.
    accepted = [target[i] for i in inliers] + [p.cad_xy for p in holdout]
    # A small, explicit visual context margin also admits neighbouring houses.
    # The renderer still clips every external polygon to this transform's scope.
    bounds = (
        max(cad_bounds[0] - 150, min(p[0] for p in accepted) - 150),
        max(cad_bounds[1] - 150, min(p[1] for p in accepted) - 150),
        min(cad_bounds[2] + 150, max(p[0] for p in accepted) + 150),
        min(cad_bounds[3] + 150, max(p[1] for p in accepted) + 150),
    )
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError("Контрольные точки находятся вне участка CAD")
    scale, rotation, translation = model
    return LocationTransform(
        reference_lon_lat=reference,
        scale=scale,
        rotation=rotation,
        translation=translation,
        valid_extent_cad_xy=bounds,
    ), {
        "rmse_m": rmse,
        "max_residual_m": max(residuals),
        "inlier_count": len(inliers),
        "holdout_rmse_m": math.sqrt(sum(r * r for r in checks) / len(checks)),
    }


def automatic_pairs(
    cad: dict[str, Any], mapped: dict[str, Any]
) -> list[LocationControlPair]:
    def indexed(collection: dict[str, Any], is_map: bool) -> dict[str, list[Any]]:
        rows: dict[str, list[Any]] = {}
        for feature in collection.get("features", []):
            props = feature.get("properties", {})
            tags = props.get("tags", props)
            address = normalize_address(
                tags.get("addr:housenumber") if is_map else props.get("address")
            )
            if not address or (not is_map and props.get("kind") != "building"):
                continue
            geometry = shape(feature["geometry"])
            if (
                isinstance(geometry, (Polygon, MultiPolygon))
                and geometry.is_valid
                and geometry.area > 0
            ):
                rows.setdefault(address, []).append(
                    (geometry.centroid.coords[0], str(feature.get("id", "")))
                )
        return rows

    cad_rows, map_rows = indexed(cad, False), indexed(mapped, True)
    pairs = []
    for address in sorted(cad_rows.keys() & map_rows.keys()):
        left, right = cad_rows[address], map_rows[address]
        if len(left) == len(right) == 1:
            pairs.append(
                LocationControlPair(
                    cad_xy=left[0][0],
                    lon_lat=right[0][0],
                    source=f"address:{address};CAD:{left[0][1]};OSM:{right[0][1]}",
                )
            )
    # Holdouts never take part in the fit. Keep the split stable and reviewable.
    if len(pairs) >= 4:
        for index in range(3, len(pairs), 4):
            pairs[index] = pairs[index].model_copy(update={"usage": "holdout"})
    return pairs[:64]


def transformed_buildings(
    collection: dict[str, Any], frame: LocationTransform
) -> dict[str, Any]:
    def project(x: Any, y: Any, *_: Any) -> tuple[float, float]:
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise TypeError("Scalar coordinate projection")
        return apply_point(
            tangent((x, y), frame.reference_lon_lat, frame.earth_radius_m),
            frame.scale,
            frame.rotation,
            frame.translation,
        )

    result = []
    omitted = 0
    left, bottom, right, top = frame.valid_extent_cad_xy
    for feature in collection.get("features", []):
        geometry = transform_geometry(project, shape(feature["geometry"]))
        west, south, east, north = geometry.bounds
        # RenderFrame rejects an external polygon when any vertex leaves the
        # checked extent. Use the same whole-polygon policy in the review overlay.
        if not (left <= west <= east <= right and bottom <= south <= north <= top):
            omitted += 1
            continue
        result.append({**feature, "geometry": mapping(geometry)})
    return {
        "type": "FeatureCollection",
        "features": result,
        "omitted_outside_extent_count": omitted,
    }
