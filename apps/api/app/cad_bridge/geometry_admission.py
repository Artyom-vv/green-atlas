"""Допуск отдельных native-примитивов перед проверкой связности снимка; ошибочная
геометрия не исправляется молча.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeGuard, cast

from .contracts import (
    CadSnapshotAdmissionError,
    PathGeometry,
    PointGeometry,
    RegionGeometry,
)
from .integrity import UNSIGNED_CONTENT_SHA256
from .integrity import signed_geometry_payload as _signed_geometry_payload

# Запас сравнения чисел с плавающей точкой не является допуском точности геометрии.
SAMPLING_TOLERANCE_RELATIVE_SLACK = 1e-12
_MEASURE_EPSILON_UNITS = 1e-10
_AREA_ERROR_ENVELOPE_FACTOR = 2.0
_PERIMETER_RELATIVE_ERROR = 0.01


@dataclass(frozen=True)
class QuarantinedPath:
    reason: str


def signed_xy_area(coordinates: Sequence[Sequence[float]]) -> float:
    # Перед суммированием малых площадей убираем крупное смещение городских WCS-координат.
    origin_x, origin_y = coordinates[0][:2]
    return 0.5 * math.fsum(
        (left[0] - origin_x) * (right[1] - origin_y)
        - (right[0] - origin_x) * (left[1] - origin_y)
        for left, right in zip(coordinates, coordinates[1:], strict=False)
    )


def admit_native_path(
    path: Mapping[str, object],
    *,
    geometry_id: str,
    identity: Mapping[str, object],
    metres_per_unit: float,
    requested_tolerance_m: float,
) -> PathGeometry | QuarantinedPath:
    """Сохранять порядок отказов и изоляцию плохих данных независимо от учёта снимка."""
    coordinates = path.get("coordinates")
    closed = path.get("closed")
    if path.get("status") != "native" or path.get("error_status") is not None:
        raise CadSnapshotAdmissionError(f"path {geometry_id} is unresolved")
    if not isinstance(closed, bool):
        raise CadSnapshotAdmissionError(f"path {geometry_id} misses closure state")
    if not isinstance(coordinates, list) or len(coordinates) < (4 if closed else 2):
        raise CadSnapshotAdmissionError(f"path {geometry_id} has too few coordinates")
    if closed and coordinates[0] != coordinates[-1]:
        raise CadSnapshotAdmissionError(f"path {geometry_id} is not closed")
    if not closed and coordinates[0] == coordinates[-1]:
        raise CadSnapshotAdmissionError(f"path {geometry_id} has equal open endpoints")
    for point in coordinates:
        if (
            not isinstance(point, list)
            or len(point) != 3
            or not all(is_finite_number(value) for value in point)
        ):
            raise CadSnapshotAdmissionError(
                f"path {geometry_id} has invalid coordinates"
            )
    # Сырой JSON остаётся нетипизированным до проверки формы и конечности чисел.
    # Не создаём второй граф координат: нормализация принадлежит Pydantic.
    finite_coordinates = cast(list[list[float]], coordinates)
    projected_length = sum(
        math.dist(left[:2], right[:2])
        for left, right in zip(finite_coordinates, finite_coordinates[1:], strict=False)
    )
    if projected_length * metres_per_unit <= requested_tolerance_m:
        return QuarantinedPath(
            "AutoCAD path has no usable WCS XY length at the requested "
            "tolerance; excluded from calculation geometry"
        )
    if closed and abs(signed_xy_area(finite_coordinates)) * metres_per_unit**2 <= (
        requested_tolerance_m**2
    ):
        return QuarantinedPath(
            "closed AutoCAD path has no usable WCS XY area at the requested "
            "tolerance; excluded from calculation geometry"
        )
    sampled_deviation = path.get("sampled_max_deviation_units")
    if not is_finite_number(sampled_deviation) or sampled_deviation < 0:
        raise CadSnapshotAdmissionError(f"path {geometry_id} has invalid deviation")
    achieved_tolerance_m = sampled_deviation * metres_per_unit
    if achieved_tolerance_m > requested_tolerance_m * (
        1 + SAMPLING_TOLERANCE_RELATIVE_SLACK
    ):
        raise CadSnapshotAdmissionError(
            f"path {geometry_id} exceeds requested sampling tolerance"
        )
    geometry = PathGeometry.model_validate(
        {
            "id": geometry_id,
            "identity": identity,
            "kind": "path",
            "closed": closed,
            "coordinates": finite_coordinates,
            "achieved_tolerance_m": achieved_tolerance_m,
            "content_sha256": UNSIGNED_CONTENT_SHA256,
        }
    )
    geometry.content_sha256 = _signed_geometry_payload(geometry)["content_sha256"]
    return geometry


def is_finite_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and math.isfinite(value)


def native_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "handle": record["handle"],
        "instance_chain": list(record.get("instance_chain", [])),
    }


def native_identity_key(record: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    return record["handle"], tuple(record.get("instance_chain", []))


def native_geometry_id(record: dict[str, Any], kind: str = "region") -> str:
    handle, instance_chain = native_identity_key(record)
    return f"{kind}/{'/'.join([*instance_chain, handle])}"


def _length_3d(coordinates: list[list[float]]) -> float:
    return sum(
        math.dist(left, right)
        for left, right in zip(coordinates, coordinates[1:], strict=False)
    )


def admit_native_region(
    region: dict[str, Any],
    source_handles: list[str],
    *,
    is_proposal: bool,
    allow_multiple_outer: bool,
    metres_per_unit: float,
    requested_tolerance_m: float,
) -> tuple[dict[str, Any], float]:
    """Нормализовать native-область с проверкой колец по заявленным измерениям."""
    if region.get("status") != "native" or region.get("error_status") is not None:
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} is unresolved"
        )
    area = region.get("native_area_units2")
    perimeter = region.get("native_perimeter_units")
    if not is_finite_number(area) or area <= 0:
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} has invalid area"
        )
    if not is_finite_number(perimeter) or perimeter <= 0:
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} has invalid perimeter"
        )

    loops: list[dict[str, Any]] = []
    achieved_tolerance_m = 0.0
    reconstructed_outer_area = 0.0
    reconstructed_hole_area = 0.0
    reconstructed_perimeter = 0.0
    outer_count = 0
    for loop in region.get("loops", []):
        coordinates = loop.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 4:
            raise CadSnapshotAdmissionError(
                f"REGION {native_geometry_id(region)} has a short loop"
            )
        if coordinates[0] != coordinates[-1]:
            raise CadSnapshotAdmissionError(
                f"REGION {native_geometry_id(region)} has an open loop"
            )
        for point in coordinates:
            if len(point) != 3 or not all(is_finite_number(value) for value in point):
                raise CadSnapshotAdmissionError(
                    f"REGION {native_geometry_id(region)} has invalid coordinates"
                )
        sampled_deviation = loop.get("sampled_max_deviation_units")
        if not is_finite_number(sampled_deviation) or sampled_deviation < 0:
            raise CadSnapshotAdmissionError(
                f"REGION {native_geometry_id(region)} has invalid deviation"
            )
        loop_tolerance_m = sampled_deviation * metres_per_unit
        achieved_tolerance_m = max(achieved_tolerance_m, loop_tolerance_m)
        loop_area = abs(signed_xy_area(coordinates))
        if loop.get("role") == "outer":
            outer_count += 1
            reconstructed_outer_area += loop_area
        elif loop.get("role") == "hole":
            reconstructed_hole_area += loop_area
        else:
            raise CadSnapshotAdmissionError(
                f"REGION {native_geometry_id(region)} has an unknown loop role"
            )
        reconstructed_perimeter += _length_3d(coordinates)
        loops.append(
            {
                "role": loop["role"],
                "closed": True,
                "coordinates": coordinates,
            }
        )
    if not loops:
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} has no loops"
        )
    reconstructed_area = reconstructed_outer_area - reconstructed_hole_area
    tolerance_units = requested_tolerance_m / metres_per_unit
    if outer_count > 1 and not allow_multiple_outer:
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} has multiple outer loops "
            "without native producer 0.1.35"
        )
    if outer_count < 1:
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} has no outer loop"
        )
    area_error = abs(reconstructed_area - area)
    perimeter_error = abs(reconstructed_perimeter - perimeter)
    area_envelope = max(
        _MEASURE_EPSILON_UNITS,
        perimeter * tolerance_units * _AREA_ERROR_ENVELOPE_FACTOR,
    )
    perimeter_envelope = max(
        _MEASURE_EPSILON_UNITS, perimeter * _PERIMETER_RELATIVE_ERROR
    )
    if area_error > area_envelope:
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} area disagrees with emitted loops"
        )
    if perimeter_error > perimeter_envelope:
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} perimeter disagrees with emitted loops"
        )
    if achieved_tolerance_m > requested_tolerance_m * (
        1 + SAMPLING_TOLERANCE_RELATIVE_SLACK
    ):
        raise CadSnapshotAdmissionError(
            f"REGION {native_geometry_id(region)} exceeds requested sampling tolerance"
        )
    proposal_id = native_geometry_id(region, "area-proposal")
    geometry_payload = {
        "id": (
            f"{proposal_id}/region"
            if is_proposal
            else native_geometry_id(
                region, "derived-region" if source_handles else "region"
            )
        ),
        "identity": native_identity(region),
        "kind": "region",
        "loops": loops,
        "native_area_units2": area,
        "native_perimeter_units": perimeter,
        "achieved_tolerance_m": achieved_tolerance_m,
    }
    if source_handles:
        geometry_payload["derived_from"] = [
            {
                "handle": handle,
                "instance_chain": list(region.get("instance_chain", [])),
            }
            for handle in source_handles
        ]
    geometry_payload["content_sha256"] = UNSIGNED_CONTENT_SHA256
    normalized_geometry = _signed_geometry_payload(
        RegionGeometry.model_validate(geometry_payload), exclude_none=True
    )
    return normalized_geometry, achieved_tolerance_m


def admit_native_point(point: dict[str, Any]) -> dict[str, Any]:
    geometry_id = native_geometry_id(point, "point")
    coordinates = point.get("coordinates")
    if point.get("status") != "native" or point.get("error_status") is not None:
        raise CadSnapshotAdmissionError(f"point {geometry_id} is unresolved")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) != 3
        or not all(is_finite_number(value) for value in coordinates)
    ):
        raise CadSnapshotAdmissionError(f"point {geometry_id} has invalid coordinates")
    geometry_payload = {
        "id": geometry_id,
        "identity": native_identity(point),
        "kind": "point",
        "coordinates": coordinates,
        "content_sha256": UNSIGNED_CONTENT_SHA256,
    }
    normalized_geometry = _signed_geometry_payload(
        PointGeometry.model_validate(geometry_payload)
    )
    return normalized_geometry
