"""Planting on an immutable AutoCAD-prepared snapshot, without a live CAD client."""

from collections import OrderedDict
from math import isfinite
from threading import RLock

from app.native_query.bounds_index import NativeBoundsIndex, nearby_measurements
from app.native_query.derived_faces import face_features
from app.native_query.display_snapshot import display_snapshot
from app.native_query.placement_policy import PlacementPolicy
from app.native_query.prepared_measurement import measure
from app.native_query.prepared_snapshot import PreparedProjection, preparation_key
from app.native_query.query_policy import BASE_QUERY_REACH_M, query_reach_m
from app.operations.progress import WorkProgress

MAX_CACHED_POINTS = 20_000  # Memory bound, not a planting distance or search budget.


class PreparedGeometryEngine(PlacementPolicy):
    final_check = "prepared_geometry"
    sends_cad_queries = False

    def __init__(self, snapshot, project, *, checkpoints=None, progress=None):
        self.snapshot = snapshot
        self.session = (
            snapshot.capture
        )  # Content identity only; contains no process or paths.
        self.factor = snapshot.factor
        self.linear_layers = snapshot.linear_layers
        self._basis_key = snapshot.key
        self._lock = RLock()
        self._layers = {
            layer.source_name: layer.model_copy(deep=True) for layer in project.layers
        }
        self._objects = tuple(record.item for record in snapshot.records)
        self._faces = snapshot.faces
        self.context_counts = snapshot.context_counts
        self.projection = PreparedProjection(snapshot, progress)
        if progress:
            progress(WorkProgress("Строим пространственный индекс"))
        self._bounds_index = NativeBoundsIndex(self._objects, self._layers)
        self._cache = OrderedDict()
        self._hybrid = None
        self._coverage = None
        self._domain_checkpoints = checkpoints
        self.assert_current(project)

    def assert_current(self, project):
        session = project.source_file.native_session if project.source_file else None
        if (
            preparation_key(project, session or self.session, self.linear_layers)
            != self._basis_key
        ):
            raise ValueError(
                "Подготовленная геометрия устарела: обновите подготовку проекта"
            )

    def prepare_positions(self, project, points, *, reach_m=BASE_QUERY_REACH_M):
        with self._lock:
            self.assert_current(project)
            if (
                not isfinite(reach_m)
                or reach_m < 0
                or len(set(points)) > MAX_CACHED_POINTS
            ):
                raise ValueError("Некорректный пакет проверки позиций")
            for x, y in points:
                if not isfinite(x) or not isfinite(y):
                    raise ValueError("Координаты должны быть конечными числами")
                point = (x, y)
                if point in self._cache and self._cache[point][0] >= reach_m:
                    self._cache.move_to_end(point)
                    continue
                items = self._bounds_index.candidates(
                    [(x / self.factor, y / self.factor, 0)], reach_m / self.factor
                )
                rows = []
                for item in items:
                    layer = self._layers.get(item.layer)
                    if (
                        layer
                        and layer.mapping_confirmed
                        and layer.mapped_kind in {"ignore", "lawn"}
                    ):
                        continue
                    rows.append(
                        measure(
                            item,
                            self.projection.get(item),
                            x,
                            y,
                            self.factor,
                            site=bool(layer and layer.mapped_kind == "site_border"),
                        )
                    )
                self._cache[point] = (reach_m, tuple(rows))
                while len(self._cache) > MAX_CACHED_POINTS:
                    self._cache.popitem(last=False)

    def _rows(self, project, x, y, radius):
        with self._lock:
            reach = query_reach_m(radius)
            self.prepare_positions(project, [(x, y)], reach_m=reach)
            return nearby_measurements(
                self._cache[(x, y)][1],
                self._layers,
                x / self.factor,
                y / self.factor,
                reach / self.factor,
            )

    def calculate(self, project, progress=None):
        self.assert_current(project)
        if progress:
            progress(WorkProgress("Готовим объекты для карты"))
        return display_snapshot(
            project, self._layers, face_features(self._faces, self.factor)
        )

    def source_coverage(self, project):
        from app.native_query.coverage import source_coverage

        return source_coverage(self, project)
