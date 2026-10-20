"""Упрощение отображаемых контуров средствами GEOS; результат не подменяет расчётную
геометрию.
"""

from dataclasses import dataclass, field
from typing import Literal

import shapely
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry

CALCULATED_SURFACE_KINDS = frozenset({"allowed", "forbidden"})
FallbackReason = Literal["invalid_polygon_coverage", "coverage_simplification_failed"]


@dataclass(frozen=True)
class DisplayGeometry:
    geometry: dict
    fallback: FallbackReason | None = None


@dataclass
class ViewportSimplifier:
    # Кеш принадлежит одному _ProjectIndex и удаляется вместе с ревизией геометрии.
    coverage_validity: dict[int, bool] = field(default_factory=dict)

    def simplify(
        self,
        feature_index: int,
        geometry: BaseGeometry,
        tolerance: float,
        *,
        calculated_surface: bool,
    ) -> tuple[BaseGeometry, FallbackReason | None]:
        if not calculated_surface or not isinstance(geometry, MultiPolygon):
            return geometry.simplify(tolerance, preserve_topology=True), None

        try:
            parts = shapely.get_parts(geometry)
            valid = self.coverage_validity.get(feature_index)
            if valid is None:
                valid = bool(shapely.coverage_is_valid(parts))
                self.coverage_validity[feature_index] = valid
            if not valid:
                return geometry, "invalid_polygon_coverage"
            # Допуск Visvalingam равен корню площади треугольника, а не предельному
            # смещению контура. Представление viewport v2 объявляет это явно.
            simplified = shapely.multipolygons(
                shapely.coverage_simplify(parts, tolerance)
            )
            if (
                not isinstance(simplified, MultiPolygon)
                or simplified.is_empty
                or not simplified.is_valid
            ):
                return geometry, "coverage_simplification_failed"
            return simplified, None
        except (GEOSException, ValueError):
            # Сохраняем полный точный объект. Допуск отмечает пропуски байтов или координат,
            # а ответ явно называет возврат к исходной геометрии.
            return geometry, "coverage_simplification_failed"
