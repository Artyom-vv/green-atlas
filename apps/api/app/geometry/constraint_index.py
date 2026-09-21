"""Exact source obstacles near a calculation, independent of viewport budgets."""

from collections import OrderedDict
from threading import RLock

from shapely import STRtree, disjoint_subset_union_all
from shapely import buffer as vector_buffer
from shapely.geometry.base import BaseGeometry

FeatureGeometry = tuple[dict, BaseGeometry]
LOCAL_UNION_CACHE_SIZE = 32
LOCAL_QUERY_EPSILON_M = 0.001


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
            # Municipal source drawings contain many independent symbols
            # (trees are the dominant real-world case).  ``unary_union``
            # nodes every input against one global graph and took minutes for
            # 10k+ disjoint contours.  Shapely's disjoint-subset union first
            # partitions independent components, then applies the same exact
            # overlay within each connected subset.  It does not simplify or
            # buffer the source geometry, so the placement contract remains
            # unchanged while sparse layers avoid quadratic overlay work.
            geometry = disjoint_subset_union_all(
                [self.features[index][1] for index in indices]
            )
            self._unions[indices] = geometry
            while len(self._unions) > LOCAL_UNION_CACHE_SIZE:
                self._unions.popitem(last=False)
            return geometry

    def buffered_union(
        self, window: BaseGeometry, distance: float
    ) -> BaseGeometry | None:
        """Buffer only local source fragments, then combine the result.

        Positive buffering distributes over geometric union.  Performing it
        per source fragment avoids constructing and buffering one enormous
        sparse GeometryCollection, while clipping to the influence window is
        equivalent for every point that can affect the target area.
        """

        # A work area is often already cut along the exact obstacle boundary.
        # Querying and clipping at precisely the rule distance then leaves
        # thousands of zero-width boundary fragments for GEOS to buffer.  A
        # 1 mm *query-only* halo keeps the local source polygons intact.  The
        # regulatory buffer below remains exactly ``distance``; extra objects
        # outside that distance cannot change the target-area difference.
        local_window = window.buffer(LOCAL_QUERY_EPSILON_M)
        indices = self._indices(local_window)
        if not indices:
            return None
        min_x, min_y, max_x, max_y = local_window.bounds
        fragments: list[BaseGeometry] = []
        for index in indices:
            geometry = self.features[index][1]
            source_min_x, source_min_y, source_max_x, source_max_y = geometry.bounds
            # Most municipal symbols are tiny and already wholly inside the
            # influence envelope. Avoid an expensive GEOS overlay for those;
            # only long/crossing source objects need exact clipping.
            if (
                source_min_x >= min_x
                and source_min_y >= min_y
                and source_max_x <= max_x
                and source_max_y <= max_y
            ):
                fragments.append(geometry)
            else:
                fragments.append(geometry.intersection(local_window))
        fragments = [fragment for fragment in fragments if not fragment.is_empty]
        if not fragments:
            return None
        # Match BaseGeometry.buffer's existing 16-segment quarter-circle
        # approximation; the vectorised function defaults to only eight.
        buffered = vector_buffer(fragments, distance, quad_segs=16)
        return disjoint_subset_union_all(buffered)
