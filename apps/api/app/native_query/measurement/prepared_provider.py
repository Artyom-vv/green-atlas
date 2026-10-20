"""Общий геометрический адаптер посадок по неизменяемому подготовленному снимку без
live-клиента CAD.
"""

from collections import OrderedDict
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from math import isfinite
from threading import RLock
from weakref import ref

from app.dxf_import.review.contracts import PhysicalObjectKind
from app.geometry.contracts import GeometrySnapshot
from app.geometry.coverage_contracts import GeometryCoverage
from app.native_query.display.display_snapshot import display_snapshot
from app.native_query.live.live_inventory import QueryObject
from app.native_query.measurement.prepared_measurement import measure
from app.native_query.policy.live_rules import MeasuredObstacle
from app.native_query.policy.placement_policy import PlacementPolicy, PointMeasurements
from app.native_query.policy.query_policy import (
    BASE_QUERY_REACH_M,
    MAX_CACHED_MEASUREMENT_POINTS,
    query_reach_m,
)
from app.native_query.policy.source_participation import (
    SourceParticipation,
    SourceUse,
    SourceUseProof,
    classify_source_use,
    materialize_source_roles,
    source_object_key,
)
from app.native_query.preparation.derived_faces import face_features
from app.native_query.preparation.preparation_identity import preparation_basis
from app.native_query.preparation.prepared_snapshot import (
    FileFingerprint,
    PreparedObject,
    PreparedProjection,
    PreparedSnapshot,
    PreparedState,
)
from app.native_query.preparation.prepared_storage import SqlitePreparedResource
from app.native_query.search.bounds_index import NativeBoundsIndex, nearby_measurements
from app.native_query.search.domain_checkpoint import DomainCheckpointStore
from app.native_query.search.hybrid_domain import HybridSearch
from app.operations.progress import ProgressReporter, WorkProgress
from app.projects.contracts import Project
from app.shared.performance import performance_span, performance_stage
from app.validation.contracts import CalculationInputBasis

MAX_CACHED_POINTS = MAX_CACHED_MEASUREMENT_POINTS


def _intersects_bounds(
    bounds: tuple[float, float, float, float],
    window: tuple[float, float, float, float],
) -> bool:
    x0, y0, x1, y1 = bounds
    left, bottom, right, top = window
    return (
        max(x0, x1) >= left
        and min(x0, x1) <= right
        and max(y0, y1) >= bottom
        and min(y0, y1) <= top
    )


class _SqliteSourceObjects:
    """Полный перечень читается только явным обходом покрытия, по одной записи."""

    def __init__(self, engine: "PreparedGeometryEngine", *, calculation: bool) -> None:
        self._engine = ref(engine)
        self._calculation = calculation

    def __iter__(self) -> Iterator[QueryObject]:
        engine = self._engine()
        if engine is None or engine._resource is None:
            return
        for record in engine._resource.iter_records():
            item, use = engine._materialize_record(record)
            if not self._calculation or use.use in {
                SourceUse.PHYSICAL,
                SourceUse.UNRESOLVED,
            }:
                yield item


class _SqliteBoundsIndex:
    """Тот же отбор кандидатов с дисковым RTree вместо полного STRtree."""

    def __init__(self, engine: "PreparedGeometryEngine") -> None:
        self._engine = ref(engine)

    def candidates(
        self, points: Sequence[tuple[float, float, float]], reach: float
    ) -> Iterator[QueryObject]:
        if not points:
            return
        selected = self.window(
            (
                min(point[0] for point in points),
                min(point[1] for point in points),
                max(point[0] for point in points),
                max(point[1] for point in points),
            ),
            reach,
        )
        engine = self._engine()
        if engine is None:
            return
        for item in selected:
            layer = engine._layers.get(item.layer)
            if (
                item.bounds is None
                or bool(layer and layer.mapped_kind == "site_border")
                or any(
                    _intersects_bounds(
                        item.bounds, (x - reach, y - reach, x + reach, y + reach)
                    )
                    for x, y, _ in points
                )
            ):
                yield item

    def window(
        self, bounds: tuple[float, float, float, float], reach: float
    ) -> Iterator[QueryObject]:
        engine = self._engine()
        if engine is None or engine._resource is None:
            return
        x0, y0, x1, y1 = bounds
        for record in engine._resource.candidates(
            (x0 - reach, y0 - reach, x1 + reach, y1 + reach),
            always_layers=engine._site_layers,
        ):
            item, use = engine._materialize_record(record)
            if use.use not in {SourceUse.PHYSICAL, SourceUse.UNRESOLVED}:
                continue
            layer = engine._layers.get(item.layer)
            if (
                item.bounds is None
                or bool(layer and layer.mapped_kind == "site_border")
                or _intersects_bounds(
                    item.bounds, (x0 - reach, y0 - reach, x1 + reach, y1 + reach)
                )
            ):
                # SQLite RTree округляет габариты наружу до float32. Точный
                # metadata-фильтр сохраняет прежний состав и порядок кандидатов.
                yield item


class PreparedGeometryEngine(PlacementPolicy):
    final_check = "prepared_geometry"
    sends_cad_queries = False

    @performance_span("native.engine.init")
    def __init__(
        self,
        snapshot: PreparedSnapshot | SqlitePreparedResource,
        project: Project,
        *,
        checkpoints: DomainCheckpointStore | None = None,
        progress: ProgressReporter | None = None,
    ) -> None:
        self.snapshot = snapshot
        self._resource = (
            snapshot if isinstance(snapshot, SqlitePreparedResource) else None
        )
        self._storage_fingerprint: FileFingerprint | None = None
        self.session = (
            snapshot.capture
        )  # Только идентичность содержимого, без процесса и файловых путей.
        self.factor = snapshot.factor
        self.linear_layers = snapshot.linear_layers
        self._geometry_key = snapshot.key
        self._binding = preparation_basis(
            project, snapshot.capture, snapshot.linear_layers
        )
        self._basis_key = self._binding.calculation
        self._lock = RLock()
        self._source_objects: Iterable[QueryObject]
        self._objects: Iterable[QueryObject]
        self._bind_source_roles(project)
        self._faces = snapshot.faces
        self.context_counts = snapshot.context_counts
        self.projection: SqlitePreparedResource | PreparedProjection
        if isinstance(snapshot, SqlitePreparedResource):
            self.projection = snapshot
        else:
            self.projection = PreparedProjection(snapshot, progress)
        self._bounds_index_cache: NativeBoundsIndex | _SqliteBoundsIndex | None = None
        self._cache: OrderedDict[
            tuple[float, float], tuple[float, tuple[MeasuredObstacle, ...]]
        ] = OrderedDict()
        self._hybrid: HybridSearch | None = None
        self._coverage: GeometryCoverage | None = None
        self._domain_checkpoints = checkpoints
        self.assert_current(project)

    @contextmanager
    def calculation_scope(self, project: Project) -> Iterator[None]:
        """Проверять ресурс на границах порции, а не открывать его на каждый объект."""
        scope = (
            self._resource.read_scope() if self._resource is not None else nullcontext()
        )
        # Между вызовами роутера не удерживаем блокировку движка: load_local
        # берёт их в порядке роутер → движок. Измерения и маски блокируются сами.
        with scope:
            self.assert_current(project)
            yield
            self.assert_current(project)

    def assert_current(self, project: Project) -> None:
        if self._resource is not None:
            self._resource.assert_unchanged()
        session = project.source_file.native_session if project.source_file else None
        basis = preparation_basis(project, session or self.session, self.linear_layers)
        if basis.geometry != self._geometry_key:
            raise ValueError(
                "Подготовленная геометрия устарела: обновите подготовку проекта"
            )
        with self._lock:
            if basis != self._binding:
                # Сохраняем проверенную физическую проекцию, заменяя интерпретацию и производные
                # кеши. Иначе старое более узкое правило сети пережило бы правку метаданных.
                self._bind_source_roles(project)
                self._bounds_index_cache = None
                self._cache.clear()
                self._hybrid = None
                self._coverage = None
                self._basis_key = basis.calculation
                self._binding = basis

    @property
    def _bounds_index(self) -> NativeBoundsIndex | _SqliteBoundsIndex:
        """Строить индекс проверенных проекций при первом пространственном запросе."""
        with self._lock:
            if self._bounds_index_cache is None:
                with performance_stage("native.spatial_index.init"):
                    self._bounds_index_cache = (
                        _SqliteBoundsIndex(self)
                        if self._resource is not None
                        else NativeBoundsIndex(self._objects, self._layers)
                    )
            return self._bounds_index_cache

    def _bind_source_roles(self, project: Project) -> None:
        source = project.source_file
        if source is None or source.content_sha256 is None:
            raise ValueError("Подготовленная геометрия не содержит CAD-захват")
        self._source_sha256 = source.content_sha256
        self._last_materialized: (
            tuple[tuple[tuple[str, ...], int], SourceParticipation] | None
        ) = None
        self._layers = {
            layer.source_name: layer.model_copy(deep=True) for layer in project.layers
        }
        self._decisions = {
            "/".join(
                (*decision.source.instance_chain, decision.source.handle)
            ): decision
            for decision in source.object_decisions
        }
        self._group_kinds: dict[frozenset[str], PhysicalObjectKind] = {
            frozenset(
                "/".join((*member.source.instance_chain, member.source.handle))
                for member in group.members
            ): group.kind
            for group in source.area_groups
        }
        self._site_layers = {
            name
            for name, layer in self._layers.items()
            if layer.mapped_kind == "site_border"
        }
        if self._resource is not None:
            # Назначения слоёв и адресные решения малы. Сам перечень и координаты
            # читаются только в окне запроса либо явным потоковым обходом покрытия.
            self._source_participations = {}
            self._source_objects = _SqliteSourceObjects(self, calculation=False)
            self._objects = _SqliteSourceObjects(self, calculation=True)
            return
        participation = {}
        shared: dict[SourceParticipation, SourceParticipation] = {}
        objects = []
        for record in self.snapshot.records:
            item, value = self._classify_record(record)
            participation[source_object_key(item)] = shared.setdefault(value, value)
            objects.append(item)
        self._source_participations = participation
        self._source_objects, self._objects = materialize_source_roles(
            objects, self._layers, participation
        )

    def _classify_record(
        self, record: PreparedObject
    ) -> tuple[QueryObject, SourceParticipation]:
        stored = record.participation
        if stored is not None and not stored.current(
            self._source_sha256, self._geometry_key
        ):
            raise ValueError("Участие объекта относится к другой подготовке CAD")
        original = stored.source_layer if stored is not None else record.item.layer
        item = (
            record.item
            if record.item.layer == original
            else record.item.model_copy(update={"layer": original})
        )
        decision = (
            self._decisions.get(item.routes[0]) if len(item.routes) == 1 else None
        )
        value = classify_source_use(
            self._layers.get(original),
            source_sha256=self._source_sha256,
            prepared_key=self._geometry_key,
            source_layer=original,
            decision=decision,
            area_group_kind=self._group_kinds.get(frozenset(item.routes)),
            prepared_topology=stored is not None
            and stored.proof == SourceUseProof.PREPARED_TOPOLOGY,
            prepared_boundary=stored is not None
            and stored.proof == SourceUseProof.PREPARED_BOUNDARY,
            capture_context=stored is not None
            and stored.proof == SourceUseProof.CAPTURE_CONTEXT,
        )
        return item, stored if stored == value else value

    def _materialize_record(
        self, record: PreparedObject
    ) -> tuple[QueryObject, SourceParticipation]:
        item, participation = self._classify_record(record)
        sources, _ = materialize_source_roles(
            (item,), self._layers, {source_object_key(item): participation}
        )
        self._last_materialized = source_object_key(item), participation
        return sources[0], participation

    def source_participation(
        self, item: QueryObject, prepared_key: str
    ) -> SourceParticipation:
        key = source_object_key(item)
        if self._resource is None:
            value = self._source_participations[key]
        elif self._last_materialized is not None and self._last_materialized[0] == key:
            # Покрытие запрашивает роль сразу после очередной записи потока.
            # Один свидетель исключает повторный SQL SELECT и не удерживает перечень.
            value = self._last_materialized[1]
        else:
            value = self._classify_record(self._resource.record(item))[1]
        if not value.current(self.session.snapshot_sha256, prepared_key):
            raise ValueError("Участие объекта относится к другой подготовке CAD")
        return value

    def validation_input_basis(self, project: Project) -> CalculationInputBasis:
        self.assert_current(project)
        return CalculationInputBasis(
            geometry_digest=self._binding.geometry,
            calculation_digest=self._binding.calculation,
            evidence_digest=self._binding.evidence,
        )

    def validation_independent_of_planting_zones(self, project: Project) -> bool:
        """Проверенный CAD задаёт ограничения; рабочие зоны ограничивают поиск."""
        self.assert_current(project)
        return type(self) is PreparedGeometryEngine

    def supports_placement_projection(self, project: Project) -> bool:
        """Проверка использует собственный снимок, а не объекты отображения."""
        return bool(project.source_file and project.source_file.native_session)

    def prepared_state(self, project: Project) -> PreparedState:
        try:
            self.assert_current(project)
            return "available"
        except ValueError:
            return "stale"

    def _point_measurements(
        self,
        project: Project,
        points: list[tuple[float, float]],
        *,
        reach_m: float = BASE_QUERY_REACH_M,
    ) -> PointMeasurements:
        with self._lock, self.calculation_scope(project):
            self.assert_current(project)
            if (
                not isfinite(reach_m)
                or reach_m < 0
                or len(set(points)) > MAX_CACHED_POINTS
            ):
                raise ValueError("Некорректный пакет проверки позиций")
            measured: PointMeasurements = {}
            answers: PointMeasurements = {}
            for x, y in points:
                if not isfinite(x) or not isfinite(y):
                    raise ValueError("Координаты должны быть конечными числами")
                point = (x, y)
                if point in self._cache and self._cache[point][0] >= reach_m:
                    self._cache.move_to_end(point)
                    answers[point] = self._cache[point]
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
                measured[point] = (reach_m, tuple(rows))
            if measured:
                self._remember_measurements(measured)
            answers.update(measured)
            self.assert_current(project)
            return answers

    def _rows(
        self, project: Project, x: float, y: float, radius: float
    ) -> list[MeasuredObstacle]:
        with self._lock:
            reach = query_reach_m(radius)
            measured = self._point_measurements(project, [(x, y)], reach_m=reach)
            return nearby_measurements(
                measured[(x, y)][1],
                self._layers,
                x / self.factor,
                y / self.factor,
                reach / self.factor,
            )

    def calculate(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometrySnapshot:
        self.assert_current(project)
        if progress:
            progress(WorkProgress("Готовим объекты для карты"))
        result = display_snapshot(
            project, self._layers, face_features(self._faces, self.factor)
        )
        self.assert_current(project)
        return result

    def face_physical_routes(self) -> dict[int, tuple[str, ...]]:
        """Паспорт использует адреса областей, не их отображаемые координаты."""
        if self._resource is not None:
            return dict(self._resource.iter_face_routes())
        return {face.id: face.physical_routes for face in self._faces}

    def source_coverage(self, project: Project) -> GeometryCoverage:
        from app.native_query.search.coverage import source_coverage

        self.assert_current(project)
        return source_coverage(self, project)
