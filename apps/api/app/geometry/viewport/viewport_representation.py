"""Идентичность и точность представления карты по масштабу; параметры упрощения не
становятся нормативными допусками.
"""

from dataclasses import asdict, dataclass
from math import inf, nextafter

EXACT_RESOLUTION_MAX = 0.75
FORBIDDEN_RESOLUTION_MAX = 2.2
SIMPLIFY_RESOLUTION_FACTOR = 0.35
TOLERANCE_DECIMALS = 3
TOLERANCE_STEP = 10**-TOLERANCE_DECIMALS


@dataclass(frozen=True)
class ResolutionRange:
    min: float
    max: float
    min_inclusive: bool = False
    max_inclusive: bool = False


def _components(resolution: float) -> tuple[float, bool]:
    tolerance = (
        round(resolution * SIMPLIFY_RESOLUTION_FACTOR, TOLERANCE_DECIMALS)
        if resolution > EXACT_RESOLUTION_MAX
        else 0.0
    )
    return tolerance, resolution <= FORBIDDEN_RESOLUTION_MAX


def _resolution_range(tolerance: float, show_forbidden: bool) -> ResolutionRange | None:
    if tolerance == 0:
        return ResolutionRange(0.0, EXACT_RESOLUTION_MAX, max_inclusive=True)
    # Открытые границы ячеек округлены внутрь без зависимости от правил равенства float/round.
    lower = nextafter(
        (tolerance - TOLERANCE_STEP / 2) / SIMPLIFY_RESOLUTION_FACTOR, inf
    )
    upper = nextafter(
        (tolerance + TOLERANCE_STEP / 2) / SIMPLIFY_RESOLUTION_FACTOR, -inf
    )
    lower = max(lower, EXACT_RESOLUTION_MAX)
    upper_inclusive = show_forbidden and upper >= FORBIDDEN_RESOLUTION_MAX
    if show_forbidden:
        upper = min(upper, FORBIDDEN_RESOLUTION_MAX)
    else:
        lower = max(lower, FORBIDDEN_RESOLUTION_MAX)
    # Концы проверяются настоящим quantizer, а не только десятичной алгеброй.
    # Внутри диапазона округление монотонно.
    first = nextafter(lower, inf)
    last = upper if upper_inclusive else nextafter(upper, -inf)
    expected = (tolerance, show_forbidden)
    if first > last or _components(first) != expected or _components(last) != expected:
        return None
    return ResolutionRange(lower, upper, max_inclusive=upper_inclusive)


@dataclass(frozen=True)
class ViewportRepresentation:
    simplify_tolerance: float
    show_forbidden: bool
    resolution_range: ResolutionRange | None

    @property
    def id(self) -> str:
        # v2 использует native-упрощение покрытия расчётных MultiPolygon.
        return f"viewport-v2:tol={self.simplify_tolerance:.3f}:forbidden={int(self.show_forbidden)}"

    def metadata(self) -> dict:
        return {
            "representation_id": self.id,
            "simplify_tolerance": self.simplify_tolerance,
            "simplification_policy": {
                "calculated_multipolygon": "coverage_visvalingam_whyatt",
                "coverage_tolerance_unit": "sqrt_triangle_area_m",
                "other_geometry": "topology_preserving_douglas_peucker",
                "other_tolerance_unit": "max_displacement_m",
            },
            "resolution_range": asdict(self.resolution_range)
            if self.resolution_range
            else None,
        }


def viewport_representation(resolution: float) -> ViewportRepresentation:
    tolerance, show_forbidden = _components(resolution)
    return ViewportRepresentation(
        tolerance, show_forbidden, _resolution_range(tolerance, show_forbidden)
    )
