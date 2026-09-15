"""Native display simplification; exact calculation geometry is never replaced."""

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
    # Owned by one _ProjectIndex: discarded with its geometry revision/source.
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
            # Visvalingam's tolerance is sqrt(triangle area), not a guaranteed
            # maximum displacement. Viewport representation v2 declares this.
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
            # Preserve the complete exact feature. Admission still reports any
            # bytes/coordinate omission, and the response names this fallback.
            return geometry, "coverage_simplification_failed"
