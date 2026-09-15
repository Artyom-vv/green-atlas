"""Exact source obstacles near a calculation, independent of viewport budgets."""

from collections import OrderedDict
from threading import RLock

from shapely import STRtree
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

FeatureGeometry = tuple[dict, BaseGeometry]
LOCAL_UNION_CACHE_SIZE = 32


class ConstraintIndex:
    """One kind in one checker revision; source order is evidence order.

    Unlike a map query this index never simplifies, truncates or prioritises
    visible objects. The query geometry is the full influence of a rule.
    """

    def __init__(self, features: list[FeatureGeometry]) -> None:
        self.features = features
        self._tree = STRtree([geometry for _, geometry in features])
        self._unions: OrderedDict[tuple[int, ...], BaseGeometry] = OrderedDict()
        self._lock = RLock()

    def _indices(self, window: BaseGeometry) -> tuple[int, ...]:
        # Exact intersection after bbox selection also excludes the empty
        # space between disjoint work areas and inside their holes.
        return tuple(
            sorted(
                int(index)
                for index in self._tree.query(
                    window,
                    predicate="intersects",
                )
            )
        )

    def nearby(self, window: BaseGeometry) -> list[FeatureGeometry]:
        return [self.features[index] for index in self._indices(window)]

    def nearest(self, geometry: BaseGeometry) -> list[tuple[int, FeatureGeometry]]:
        """Exact nearest source, including ties, without a rule-radius cutoff."""
        indices = sorted(int(index) for index in self._tree.query_nearest(geometry))
        return [(index, self.features[index]) for index in indices]

    def within_distance(
        self, geometry: BaseGeometry, distance: float
    ) -> list[tuple[int, FeatureGeometry]]:
        indices = sorted(
            int(index)
            for index in self._tree.query(
                geometry, predicate="dwithin", distance=distance
            )
        )
        return [(index, self.features[index]) for index in indices]

    def union(self, window: BaseGeometry) -> BaseGeometry | None:
        indices = self._indices(window)
        if not indices:
            return None
        with self._lock:
            cached = self._unions.get(indices)
            if cached is not None:
                self._unions.move_to_end(indices)
                return cached
            geometry = unary_union([self.features[index][1] for index in indices])
            self._unions[indices] = geometry
            while len(self._unions) > LOCAL_UNION_CACHE_SIZE:
                self._unions.popitem(last=False)
            return geometry
