"""Ограниченный кеш завершённых порций до обрезания рабочим участком.

WKB не удерживает графы GEOS. Точный порядок объектов и частей сохраняется;
другая порция не получает ограничения соседней области.
"""

import json
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from sys import getsizeof
from typing import Any

from shapely import from_wkb, get_num_coordinates
from shapely.geometry.base import BaseGeometry

from app.native_query.policy.query_policy import (
    MAX_CACHED_CONSTRAINT_BATCHES,
    MAX_CACHED_CONSTRAINT_BYTES,
    MAX_CACHED_CONSTRAINT_COORDINATES,
    MAX_CACHED_CONSTRAINT_ISSUES,
    MAX_CACHED_CONSTRAINT_ROUTES,
    MAX_CACHED_CONSTRAINT_TOTAL_COORDINATES,
)
from app.operations.progress import check_operation_cancelled


@dataclass(frozen=True, slots=True)
class SourceIssue:
    routes: tuple[str, ...]
    layer: str
    reason: str
    detail: str
    localized: bool

    def restore(self) -> dict[str, Any]:
        return {
            "routes": list(self.routes),
            "layer": self.layer,
            "reason": self.reason,
            "detail": self.detail,
            "localized": self.localized,
        }


@dataclass(slots=True)
class BatchConstraints:
    blocked: BaseGeometry
    sites: list[BaseGeometry]
    uncertain_sites: list[BaseGeometry]
    reviews: dict[str, BaseGeometry]
    issues: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _StoredBatch:
    blocked: bytes
    sites: tuple[bytes, ...]
    uncertain_sites: tuple[bytes, ...]
    reviews: tuple[tuple[str, bytes], ...]
    issues: tuple[SourceIssue, ...]
    weight: int
    coordinates: int
    routes: int
    key_witness: bytes

    def restore(self) -> BatchConstraints:
        return BatchConstraints(
            from_wkb(self.blocked),
            [from_wkb(value) for value in self.sites],
            [from_wkb(value) for value in self.uncertain_sites],
            {reason: from_wkb(value) for reason, value in self.reviews},
            [issue.restore() for issue in self.issues],
        )


def _weight(value: object) -> int:
    """Консервативно учитывать также ключи, контейнеры и адресные замечания."""
    if isinstance(value, (tuple, list)):
        return getsizeof(value) + sum(_weight(item) for item in value)
    if isinstance(value, SourceIssue):
        return getsizeof(value) + _weight(
            (value.routes, value.layer, value.reason, value.detail, value.localized)
        )
    return getsizeof(value)


def _key_witness(key: tuple) -> bytes:
    """Сохранять полный упорядоченный ключ без округления чисел и графа кортежей."""
    return json.dumps(
        key, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


class ConstraintUnionCache:
    def __init__(self) -> None:
        self.entries: OrderedDict[tuple[bytes, float | None], _StoredBatch] = (
            OrderedDict()
        )
        self.bytes = self.coordinates = self.issues = self.routes = 0

    def get(self, key: tuple, review_reach: float) -> BatchConstraints | None:
        witness = _key_witness(key)
        token = sha256(witness).digest()
        # Порция без замечаний не зависит от горизонта неизвестных объектов.
        # Для остальных порций он обязан совпасть даже при тех же отступах.
        for candidate in ((token, None), (token, review_reach)):
            value = self.entries.get(candidate)
            # Даже коллизия отпечатка не разрешает использовать чужую порцию.
            if value is not None and value.key_witness == witness:
                check_operation_cancelled()
                result = value.restore()
                check_operation_cancelled()
                self.entries.move_to_end(candidate)
                return result
        return None

    def remember(
        self, key: tuple, review_reach: float, value: BatchConstraints
    ) -> None:
        check_operation_cancelled()
        geometries = (
            value.blocked,
            *value.sites,
            *value.uncertain_sites,
            *value.reviews.values(),
        )
        coordinates = sum(int(get_num_coordinates(item)) for item in geometries)
        routes = sum(len(issue["routes"]) for issue in value.issues)
        if (
            coordinates > MAX_CACHED_CONSTRAINT_COORDINATES
            or coordinates > MAX_CACHED_CONSTRAINT_TOTAL_COORDINATES
            or len(value.issues) > MAX_CACHED_CONSTRAINT_ISSUES
            or routes > MAX_CACHED_CONSTRAINT_ROUTES
        ):
            return
        issues = tuple(
            SourceIssue(
                tuple(issue["routes"]),
                issue["layer"],
                issue["reason"],
                issue["detail"],
                issue["localized"],
            )
            for issue in value.issues
        )
        witness = _key_witness(key)
        stored_key = (sha256(witness).digest(), review_reach if issues else None)
        encoded = (
            value.blocked.wkb,
            tuple(item.wkb for item in value.sites),
            tuple(item.wkb for item in value.uncertain_sites),
            tuple((reason, item.wkb) for reason, item in value.reviews.items()),
            issues,
        )
        weight = 256 + _weight(stored_key) + _weight(encoded) + _weight(witness)
        if weight > MAX_CACHED_CONSTRAINT_BYTES:
            return
        check_operation_cancelled()
        previous = self.entries.pop(stored_key, None)
        if previous is not None:
            self._subtract(previous)
        while self.entries and (
            len(self.entries) >= MAX_CACHED_CONSTRAINT_BATCHES
            or self.bytes + weight > MAX_CACHED_CONSTRAINT_BYTES
            or self.coordinates + coordinates > MAX_CACHED_CONSTRAINT_TOTAL_COORDINATES
            or self.issues + len(issues) > MAX_CACHED_CONSTRAINT_ISSUES
            or self.routes + routes > MAX_CACHED_CONSTRAINT_ROUTES
        ):
            self._subtract(self.entries.popitem(last=False)[1])
        self.entries[stored_key] = _StoredBatch(
            *encoded, weight, coordinates, routes, witness
        )
        self.bytes += weight
        self.coordinates += coordinates
        self.issues += len(issues)
        self.routes += routes

    def _subtract(self, value: _StoredBatch) -> None:
        self.bytes -= value.weight
        self.coordinates -= value.coordinates
        self.issues -= len(value.issues)
        self.routes -= value.routes
