"""Bounded sampling windows for sparse or strongly elongated work areas."""

from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import accumulate
from random import Random

from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry


@dataclass(frozen=True)
class SamplingWindow:
    geometry: Polygon
    origin: tuple[float, float]
    across: tuple[float, float]
    along: tuple[float, float]
    area: float

    def draw(self, random: Random) -> tuple[float, float]:
        u, v = random.random(), random.random()
        return (
            self.origin[0] + u * self.across[0] + v * self.along[0],
            self.origin[1] + u * self.across[1] + v * self.along[1],
        )


def _polygons(geometry: BaseGeometry) -> Iterator[Polygon]:
    if isinstance(geometry, Polygon) and not geometry.is_empty:
        yield geometry
    elif isinstance(geometry, BaseMultipartGeometry):
        for part in geometry.geoms:
            yield from _polygons(part)


class SparseAreaSampler:
    def __init__(self, windows: list[SamplingWindow]) -> None:
        self.windows = windows
        self._weights = list(accumulate(window.area for window in windows))

    def draw(self, random: Random) -> tuple[BaseGeometry, float, float]:
        if len(self.windows) == 1:
            window = self.windows[0]
        else:
            index = bisect_right(self._weights, random.random() * self._weights[-1])
            window = self.windows[min(index, len(self.windows) - 1)]
        x, y = window.draw(random)
        # Test this component, not the combined work area: overlapping sample
        # rectangles must not make the overlap more likely to be accepted.
        return window.geometry, x, y


def sparse_area_sampler(
    geometry: BaseGeometry,
    target: int,
    attempt_budget: int,
) -> SparseAreaSampler | None:
    """Switch only when expected bbox hits cannot reach the requested count.

    Acceptance from a uniform bbox draw is area / bbox_area. If even all
    expected geometric hits are fewer than target before spacing rejection,
    the existing attempt budget is spent mainly on empty space. Compact
    inputs retain the original coordinate stream and seed interpretation.
    """
    bbox_area = geometry.envelope.area
    if bbox_area <= 0 or geometry.area * attempt_budget >= target * bbox_area:
        return None
    windows = []
    for polygon in _polygons(geometry):
        rectangle = polygon.minimum_rotated_rectangle
        if not isinstance(rectangle, Polygon) or rectangle.area <= 0:
            continue
        coordinates = list(rectangle.exterior.coords)
        origin = (coordinates[0][0], coordinates[0][1])
        windows.append(
            SamplingWindow(
                geometry=polygon,
                origin=origin,
                across=(coordinates[1][0] - origin[0], coordinates[1][1] - origin[1]),
                along=(coordinates[3][0] - origin[0], coordinates[3][1] - origin[1]),
                area=rectangle.area,
            )
        )
    # Selecting a rectangle proportional to its area and accepting only its
    # polygon gives the same constant density at every usable point. Holes
    # and the normal spacing check still reject candidates afterwards.
    return SparseAreaSampler(windows) if windows else None
