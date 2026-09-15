"""One immutable rotated grid basis for the existing spacing search."""

from collections.abc import Iterator
from math import floor, sqrt
from typing import Literal

from shapely.affinity import rotate
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

from app.planning.ports import PatternCandidate

GRID_BOUNDARY_EPSILON = 1e-9
GRID_COORDINATE_DECIMALS = 6


class RegularFillGrid:
    def __init__(
        self,
        geometry: BaseGeometry,
        angle_deg: float,
        layout: Literal["regular", "staggered"],
    ) -> None:
        self.angle_deg = angle_deg
        self.staggered = layout == "staggered"
        self.rotated = rotate(geometry, -angle_deg, origin=(0, 0), use_radians=False)
        self.bounds = self.rotated.bounds
        self.coverage = prep(self.rotated)

    def _keys(self, spacing: float) -> Iterator[tuple[float, float]]:
        min_x, min_y, max_x, max_y = self.bounds
        first_x = floor(min_x / spacing) * spacing
        first_y = floor(min_y / spacing) * spacing
        row = 0
        row_step = spacing * sqrt(3) / 2 if self.staggered else spacing
        seen: set[tuple[float, float]] = set()
        y = first_y
        # Preserve cumulative arithmetic, global phase, row parity and order.
        # Component skipping and a different count-search strategy are separate.
        while y <= max_y + GRID_BOUNDARY_EPSILON:
            x_offset = spacing / 2 if self.staggered and row % 2 else 0
            x = first_x + x_offset
            while x <= max_x + GRID_BOUNDARY_EPSILON:
                point = Point(x, y)
                if self.coverage.covers(point):
                    restored = rotate(
                        point, self.angle_deg, origin=(0, 0), use_radians=False
                    )
                    key = (
                        round(restored.x, GRID_COORDINATE_DECIMALS),
                        round(restored.y, GRID_COORDINATE_DECIMALS),
                    )
                    if key not in seen:
                        seen.add(key)
                        yield key
                x += spacing
            y += row_step
            row += 1

    def count(self, spacing: float, limit: int) -> int:
        count = 0
        for _ in self._keys(spacing):
            count += 1
            if count >= limit:
                break
        return count

    def candidates(self, spacing: float) -> list[PatternCandidate]:
        return [PatternCandidate(*key) for key in self._keys(spacing)]
