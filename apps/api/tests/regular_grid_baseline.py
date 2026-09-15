"""Frozen pre-extraction grid loop, used only for equivalence and CPU profiles."""

from math import floor, sqrt

from shapely.affinity import rotate
from shapely.geometry import Point

from app.planning.ports import PatternCandidate


class BaselineRegularGrid:
    def __init__(self, geometry, angle_deg, layout):
        self.geometry = geometry
        self.angle_deg = angle_deg
        self.layout = layout

    def _candidates(self, spacing, limit=None):
        candidates = []
        seen = set()
        rotated = rotate(
            self.geometry, -self.angle_deg, origin=(0, 0), use_radians=False
        )
        min_x, min_y, max_x, max_y = rotated.bounds
        first_x = floor(min_x / spacing) * spacing
        first_y = floor(min_y / spacing) * spacing
        row = 0
        row_step = spacing * sqrt(3) / 2 if self.layout == "staggered" else spacing
        y = first_y
        while y <= max_y + 1e-9:
            col = 0
            x_offset = spacing / 2 if self.layout == "staggered" and row % 2 else 0
            x = first_x + x_offset
            while x <= max_x + 1e-9:
                candidate_x, candidate_y = x, y
                point = Point(candidate_x, candidate_y)
                if rotated.covers(point):
                    restored = rotate(
                        point, self.angle_deg, origin=(0, 0), use_radians=False
                    )
                    key = (round(restored.x, 6), round(restored.y, 6))
                    if key not in seen:
                        seen.add(key)
                        candidates.append(PatternCandidate(*key))
                        if limit is not None and len(candidates) >= limit:
                            return candidates
                x += spacing
                col += 1
            y += row_step
            row += 1
        return candidates

    def count(self, spacing, limit):
        return len(self._candidates(spacing, limit))

    def candidates(self, spacing):
        return self._candidates(spacing)
