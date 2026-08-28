from collections import OrderedDict
from dataclasses import dataclass, field
from heapq import nsmallest
from math import isfinite
from threading import RLock

from shapely import STRtree
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

from app.contracts import GeometrySnapshot, Project


@dataclass
class _ProjectIndex:
    snapshot_key: str
    features: list[dict]
    geometries: list[BaseGeometry]
    tree: STRtree
    # A long navigation session can visit every part of a city-scale drawing.
    # Keep the derived GeoJSON bounded just like the viewport response instead
    # of retaining one simplified copy of every source entity at every zoom.
    # The key includes the rounded tolerance: ``mid`` / ``overview`` are UI
    # labels, not geometrically equivalent simplification levels.
    simplified_geometries: OrderedDict[tuple[float, int], dict] = field(default_factory=OrderedDict)


class IndexedGeometryQuery:
    """Queries visible map features by extent without sending the full drawing."""

    # These layers explain the terrain and the planting decision. They must
    # survive a bounded viewport response before decorative/unknown CAD layers
    # when a large drawing exceeds the feature budget.
    _IMPORTANT_KINDS = {"site_border", "allowed", "planting_area", "forbidden", "utility", "existing_green", "building", "road"}
    _CONTEXT_COVERAGE_ORDER = ("site_border", "allowed", "forbidden", "building", "road", "utility", "existing_green")

    def __init__(self, max_features: int = 12_000, max_cached_projects: int = 32) -> None:
        if max_features < 1:
            raise ValueError("Geometry response budget must be positive")
        if max_cached_projects < 1:
            raise ValueError("Geometry index cache budget must be positive")
        self.max_features = max_features
        self.max_cached_projects = max_cached_projects
        self._indexes: OrderedDict[str, _ProjectIndex] = OrderedDict()
        self._lock = RLock()

    @property
    def cached_project_count(self) -> int:
        with self._lock:
            return len(self._indexes)

    def discard(self, project_id: str) -> None:
        """Release a deleted project's spatial index immediately.

        The LRU cap protects a long-lived API from an unlimited sequence of
        drawings, but a 12k-feature index can still be material until another
        project arrives. Project deletion is a durable lifecycle boundary, so
        retaining that index after the source and plan are gone only wastes
        memory and makes the cache's ownership ambiguous.
        """
        with self._lock:
            self._indexes.pop(project_id, None)

    def _index(self, project: Project) -> _ProjectIndex:
        snapshot_kind = "calculated" if project.geometry is not None else "source"
        snapshot_model = project.geometry or project.source_geometry
        if snapshot_model is None:
            raise ValueError("Сначала импортируйте DXF")
        snapshot = snapshot_model.feature_collection
        with self._lock:
            cached = self._indexes.get(project.id)
            # The map index depends only on the geometry snapshot. The project
            # state version also changes for plan edits, panel metadata and
            # evidence updates; including it here made every such action
            # rebuild all Shapely geometries before the next map request.
            snapshot_key = f"{snapshot_kind}:{project.geometry_version}"
            if cached is not None and cached.snapshot_key == snapshot_key:
                self._indexes.move_to_end(project.id)
                return cached
            features: list[dict] = []
            geometries: list[BaseGeometry] = []
            for feature in snapshot.get("features", []):
                try:
                    geometry = shape(feature["geometry"])
                except Exception:
                    continue
                if geometry.is_empty:
                    continue
                features.append(feature)
                geometries.append(geometry)
            indexed = _ProjectIndex(snapshot_key=snapshot_key, features=features, geometries=geometries, tree=STRtree(geometries))
            self._indexes[project.id] = indexed
            self._indexes.move_to_end(project.id)
            while len(self._indexes) > self.max_cached_projects:
                self._indexes.popitem(last=False)
            return indexed

    @staticmethod
    def _validate_query(extent: tuple[float, float, float, float], resolution: float) -> None:
        if not all(isfinite(value) for value in (*extent, resolution)) or resolution <= 0:
            raise ValueError("Координаты и масштаб карты должны быть конечными положительными числами")

    def query_cached(self, project: Project, extent: tuple[float, float, float, float], resolution: float) -> GeometrySnapshot | None:
        """Serve a viewport from an existing index without loading DXF JSON.

        Project projections deliberately omit source geometry and replace a
        calculated feature collection with an empty shell. That is sufficient
        to identify a cached snapshot by project id, source/calculated kind
        and geometry version. A miss deliberately returns ``None`` so the
        application can load the authoritative full project exactly once.
        """
        self._validate_query(extent, resolution)
        snapshot_kind = "calculated" if project.geometry is not None else "source"
        snapshot_key = f"{snapshot_kind}:{project.geometry_version}"
        with self._lock:
            cached = self._indexes.get(project.id)
            if cached is None or cached.snapshot_key != snapshot_key:
                return None
            self._indexes.move_to_end(project.id)
        return self.query(project, extent, resolution, cached_index=cached)

    def query(
        self,
        project: Project,
        extent: tuple[float, float, float, float],
        resolution: float,
        *,
        cached_index: _ProjectIndex | None = None,
    ) -> GeometrySnapshot:
        self._validate_query(extent, resolution)
        indexed = cached_index or self._index(project)
        viewport = box(*extent)
        matched_indexes = indexed.tree.query(viewport)
        total_by_kind: dict[str, int] = {}

        def item(index: int) -> tuple[int, int, dict]:
            feature = indexed.features[index]
            properties = feature.get("properties", {})
            kind = str(properties.get("kind", ""))
            priority = 0 if kind in self._IMPORTANT_KINDS else 1
            return (priority, index, feature)

        def visible_candidates():
            """Yield only the detail that this zoom level is allowed to show.

            Forbidden areas deliberately disappear at an overview zoom. They
            were excluded before any response accounting in the original
            implementation; keeping that rule here means the metadata, the
            truncation flag and the resulting feature list stay consistent.
            """
            for raw_index in matched_indexes:
                candidate = item(int(raw_index))
                kind = str(candidate[2].get("properties", {}).get("kind", ""))
                if resolution > 2.2 and kind == "forbidden":
                    continue
                yield candidate

        def sort_key(candidate: tuple[int, int, dict]) -> tuple[int, str, str, int]:
            priority, index, feature = candidate
            return (
                priority,
                str(feature.get("properties", {}).get("source_layer", "")),
                str(feature.get("id", feature.get("properties", {}).get("source_handle", ""))),
                index,
            )

        # Count once and retain no more than the response budget while doing
        # so. A full-city DXF may contain hundreds of thousands of matching
        # objects: retaining every hit just to discard most of them creates a
        # noticeable pause after a pan. The second pass happens only for a
        # genuinely budgeted response and is bounded by ``max_features``.
        total_matches = 0
        small_matches: list[tuple[int, int, dict]] = []
        coverage_by_kind: dict[str, tuple[int, int, dict]] = {}
        for candidate in visible_candidates():
            total_matches += 1
            kind = str(candidate[2].get("properties", {}).get("kind", "source"))
            total_by_kind[kind] = total_by_kind.get(kind, 0) + 1
            if len(small_matches) < self.max_features:
                small_matches.append(candidate)
            if kind not in self._CONTEXT_COVERAGE_ORDER:
                continue
            current = coverage_by_kind.get(kind)
            if current is None or sort_key(candidate) < sort_key(current):
                coverage_by_kind[kind] = candidate

        truncated = total_matches > self.max_features
        if not truncated:
            # Keep the old stable order for ordinary viewports.
            matches = sorted(small_matches, key=lambda candidate: candidate[1])
        else:
            coverage = [coverage_by_kind[kind] for kind in self._CONTEXT_COVERAGE_ORDER if kind in coverage_by_kind][:self.max_features]
            covered_indexes = {candidate[1] for candidate in coverage}
            remaining = nsmallest(
                self.max_features - len(coverage),
                (candidate for candidate in visible_candidates() if candidate[1] not in covered_indexes),
                key=sort_key,
            )
            matches = [*coverage, *remaining]

        simplify_tolerance = max(0.0, resolution * 0.35) if resolution > 0.75 else 0.0
        # Quantisation avoids making a new cache namespace for imperceptibly
        # different wheel-zoom resolutions, while keeping a close zoom from
        # incorrectly reusing a much coarser overview geometry.
        tolerance_key = round(simplify_tolerance, 3)
        selected: list[dict] = []
        returned_by_kind: dict[str, int] = {}
        for _, feature_index, feature in matches:
            prepared = dict(feature)
            kind = str(prepared.get("properties", {}).get("kind", "source"))
            returned_by_kind[kind] = returned_by_kind.get(kind, 0) + 1
            if simplify_tolerance:
                try:
                    cache_key = (tolerance_key, feature_index)
                    simplified_geometry = indexed.simplified_geometries.get(cache_key)
                    if simplified_geometry is None:
                        simplified = indexed.geometries[feature_index].simplify(simplify_tolerance, preserve_topology=True)
                        simplified_geometry = mapping(simplified) if not simplified.is_empty else feature["geometry"]
                        indexed.simplified_geometries[cache_key] = simplified_geometry
                        while len(indexed.simplified_geometries) > self.max_features:
                            indexed.simplified_geometries.popitem(last=False)
                    else:
                        indexed.simplified_geometries.move_to_end(cache_key)
                    prepared["geometry"] = simplified_geometry
                except Exception:
                    pass
            selected.append(prepared)

        lod = "budgeted" if truncated else "overview" if resolution > 2.5 else "mid" if resolution > 2.2 else "detail"
        omitted_by_kind = {
            kind: count - returned_by_kind.get(kind, 0)
            for kind, count in total_by_kind.items()
            if count > returned_by_kind.get(kind, 0)
        }
        return GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": selected,
                "metadata": {
                    "returned_features": len(selected),
                    "total_matches": total_matches,
                    "truncated": truncated,
                    "feature_budget": self.max_features,
                    "returned_by_kind": returned_by_kind,
                    "omitted_by_kind": omitted_by_kind,
                    "resolution": resolution,
                    "simplify_tolerance": round(simplify_tolerance, 4),
                    "lod": lod,
                    "geometry_version": project.geometry_version,
                },
            },
            site_area_m2=project.site_area_m2,
            planning_area_m2=project.planning_area_m2,
            allowed_area_m2=project.allowed_area_m2,
        )
