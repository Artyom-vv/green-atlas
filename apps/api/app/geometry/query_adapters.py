from collections import Counter, OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from math import isfinite
from threading import RLock

from shapely import STRtree
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

from app.dxf_import.contracts import ImportMode
from app.geometry.contracts import GeometrySnapshot
from app.geometry.source_overview import source_overview
from app.geometry.viewport_admission import (
    PreparedViewportFeature,
    admit_fair_features,
)
from app.geometry.viewport_budget import (
    DEFAULT_VIEWPORT_COORDINATES,
    DEFAULT_VIEWPORT_FEATURE_BYTES,
    ViewportBudget,
    feature_size,
)
from app.geometry.viewport_representation import viewport_representation
from app.geometry.viewport_selection import (
    VIEWPORT_CANDIDATE_WINDOW_FACTOR,
    Candidate,
    select_fair_candidates,
)
from app.geometry.viewport_simplification import (
    CALCULATED_SURFACE_KINDS,
    DisplayGeometry,
    ViewportSimplifier,
)
from app.projects.contracts import Project


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
    simplified_geometries: OrderedDict[tuple[float, int], DisplayGeometry] = field(
        default_factory=OrderedDict
    )
    display_sizes: OrderedDict[tuple[float, int], tuple[int, int]] = field(
        default_factory=OrderedDict
    )
    simplifier: ViewportSimplifier = field(default_factory=ViewportSimplifier)


class IndexedGeometryQuery:
    """Queries visible map features by extent without sending the full drawing."""

    def __init__(
        self,
        max_features: int = 12_000,
        max_cached_projects: int = 32,
        *,
        max_coordinates: int = DEFAULT_VIEWPORT_COORDINATES,
        max_feature_bytes: int = DEFAULT_VIEWPORT_FEATURE_BYTES,
    ) -> None:
        if max_features < 1:
            raise ValueError("Geometry response budget must be positive")
        if max_cached_projects < 1:
            raise ValueError("Geometry index cache budget must be positive")
        self.max_features = max_features
        self.max_cached_projects = max_cached_projects
        ViewportBudget(max_coordinates, max_feature_bytes)
        self.max_coordinates = max_coordinates
        self.max_feature_bytes = max_feature_bytes
        self._indexes: OrderedDict[str, _ProjectIndex] = OrderedDict()
        self._lock = RLock()

    @property
    def cached_project_count(self) -> int:
        with self._lock:
            return len(self._indexes)

    def discard(self, project_id: str) -> None:
        """Release a deleted project's spatial index immediately.

        The LRU cap protects a long-lived API from an unlimited sequence of
        drawings, but a full-source index can still be material until another
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
            indexed = _ProjectIndex(
                snapshot_key=snapshot_key,
                features=features,
                geometries=geometries,
                tree=STRtree(geometries),
            )
            self._indexes[project.id] = indexed
            self._indexes.move_to_end(project.id)
            while len(self._indexes) > self.max_cached_projects:
                self._indexes.popitem(last=False)
            return indexed

    @staticmethod
    def _validate_query(
        extent: tuple[float, float, float, float], resolution: float
    ) -> None:
        if (
            not all(isfinite(value) for value in (*extent, resolution))
            or resolution <= 0
        ):
            raise ValueError(
                "Координаты и масштаб карты должны быть конечными положительными числами"
            )

    def query_cached(
        self,
        project: Project,
        extent: tuple[float, float, float, float],
        resolution: float,
    ) -> GeometrySnapshot | None:
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
        representation = viewport_representation(resolution)
        viewport = box(*extent)
        matched_indexes = indexed.tree.query(viewport)

        def visible_candidates() -> Iterator[tuple[int, dict]]:
            """Yield only the detail that this zoom level is allowed to show.

            Forbidden areas deliberately disappear at an overview zoom. They
            were excluded before any response accounting in the original
            implementation; keeping that rule here means the metadata, the
            truncation flag and the resulting feature list stay consistent.
            """
            for raw_index in matched_indexes:
                index = int(raw_index)
                feature = indexed.features[index]
                kind = str(feature.get("properties", {}).get("kind", ""))
                if not representation.show_forbidden and kind == "forbidden":
                    continue
                yield index, feature

        selection = select_fair_candidates(
            visible_candidates, self.max_features * VIEWPORT_CANDIDATE_WINDOW_FACTOR
        )
        total_matches = selection.total_matches
        total_by_kind = selection.total_by_kind
        truncated = total_matches > self.max_features

        # The cache key and the actual computation use the same quantization.
        simplify_tolerance = representation.simplify_tolerance
        returned_by_kind: dict[str, int] = {}
        budget = ViewportBudget(self.max_coordinates, self.max_feature_bytes)
        truncation_reasons = {"features"} if truncated else set()
        simplification_fallbacks: Counter[str] = Counter()

        def prepare(candidate: Candidate) -> PreparedViewportFeature:
            feature_index, feature = candidate
            prepared = dict(feature)
            cache_key = (simplify_tolerance, feature_index)
            if simplify_tolerance:
                try:
                    simplified_geometry = indexed.simplified_geometries.get(cache_key)
                    if simplified_geometry is None:
                        simplified, fallback = indexed.simplifier.simplify(
                            feature_index,
                            indexed.geometries[feature_index],
                            simplify_tolerance,
                            calculated_surface=(
                                project.geometry is not None
                                and feature.get("properties", {}).get("kind")
                                in CALCULATED_SURFACE_KINDS
                            ),
                        )
                        simplified_geometry = DisplayGeometry(
                            mapping(simplified)
                            if not fallback and not simplified.is_empty
                            else feature["geometry"],
                            fallback,
                        )
                        indexed.simplified_geometries[cache_key] = simplified_geometry
                        while len(indexed.simplified_geometries) > self.max_features:
                            indexed.simplified_geometries.popitem(last=False)
                    else:
                        indexed.simplified_geometries.move_to_end(cache_key)
                    prepared["geometry"] = simplified_geometry.geometry
                    if simplified_geometry.fallback:
                        simplification_fallbacks[simplified_geometry.fallback] += 1
                except Exception:
                    # A failed simplification falls back to exact geometry.
                    # Do not cache its size under a future simplified result.
                    cache_key = (0.0, feature_index)
            size = indexed.display_sizes.get(cache_key)
            if size is None:
                size = feature_size(prepared)
                indexed.display_sizes[cache_key] = size
                while len(indexed.display_sizes) > self.max_features:
                    indexed.display_sizes.popitem(last=False)
            else:
                indexed.display_sizes.move_to_end(cache_key)
            return PreparedViewportFeature(feature_index, prepared, size)

        selected_matches, admission_reasons = admit_fair_features(
            selection.candidates, budget, prepare, self.max_features
        )
        truncation_reasons.update(admission_reasons)
        for item in selected_matches:
            kind = str(item.feature.get("properties", {}).get("kind", "source"))
            returned_by_kind[kind] = returned_by_kind.get(kind, 0) + 1

        if not truncated:
            selected_matches.sort(key=lambda item: item.index)
        selected = [item.feature for item in selected_matches]
        truncated = bool(truncation_reasons)

        lod = (
            "budgeted"
            if truncated
            else "overview"
            if resolution > 2.5
            else "mid"
            if resolution > 2.2
            else "detail"
        )
        omitted_by_kind = {
            kind: count - returned_by_kind.get(kind, 0)
            for kind, count in total_by_kind.items()
            if count > returned_by_kind.get(kind, 0)
        }
        overview = None
        if truncated and project.import_status.mode == ImportMode.AUTOCAD_LIVE:
            overview = source_overview(
                ((indexed.features[int(index)], indexed.geometries[int(index)])
                 for index in matched_indexes), extent, resolution,
            )
        return GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": selected,
                **({"source_overview": overview} if overview else {}),
                "metadata": {
                    "returned_features": len(selected),
                    "total_matches": total_matches,
                    "truncated": truncated,
                    "feature_budget": self.max_features,
                    "coordinate_budget": self.max_coordinates,
                    "returned_coordinates": budget.coordinates,
                    "byte_budget": self.max_feature_bytes,
                    "returned_bytes": budget.feature_bytes,
                    "byte_budget_scope": "feature_array",
                    "truncation_reasons": sorted(truncation_reasons),
                    "returned_by_kind": returned_by_kind,
                    "omitted_by_kind": omitted_by_kind,
                    "resolution": resolution,
                    "simplification_fallbacks": dict(simplification_fallbacks),
                    **representation.metadata(),
                    "lod": lod,
                    "geometry_version": project.geometry_version,
                },
            },
            site_area_m2=project.site_area_m2,
            planning_area_m2=project.planning_area_m2,
            allowed_area_m2=project.allowed_area_m2,
        )
