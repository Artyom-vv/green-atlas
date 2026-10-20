"""Совместимый геометрический адаптер native-канала: кандидатная маска отделена от
проверок измерениями AutoCAD.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict, defaultdict
from math import floor
from threading import RLock
from typing import Any

from app.dxf_import.layers.contracts import Layer
from app.dxf_import.review.contracts import (
    PhysicalObjectKind,
    SourceAreaGroupCheck,
    SourceAreaGroupRequest,
)
from app.dxf_import.units import meters_per_dxf_unit
from app.geometry.contracts import GeometrySnapshot
from app.geometry.coverage_contracts import GeometryCoverage
from app.native_query.capture.capture_client import CaptureClient
from app.native_query.display.display_snapshot import display_snapshot
from app.native_query.live.live_client import LiveSession
from app.native_query.live.live_inventory import (
    InventoryObject,
    LiveInventory,
    QueryObject,
    load_inventory,
    query_objects,
)
from app.native_query.measurement.measurement_reader import NativeMeasurementReader
from app.native_query.policy.live_rules import MeasuredObstacle
from app.native_query.policy.placement_policy import PlacementPolicy, PointMeasurements
from app.native_query.policy.query_policy import (
    BASE_QUERY_REACH_M,
    MAX_CACHED_MEASUREMENT_POINTS,
    query_reach_m,
)
from app.native_query.policy.source_participation import (
    SourceParticipation,
    SourceUseProof,
    classify_source_use,
    individual_object_targets,
    materialize_source_roles,
    source_object_key,
)
from app.native_query.preparation.derived_faces import (
    active_faces,
    area_layers,
    face_features,
    face_targets,
    linear_remainders,
)
from app.native_query.preparation.face_contracts import NativeFace
from app.native_query.preparation.face_preparation import NativeFacePreparation
from app.native_query.preparation.face_review import NativeFaceReview
from app.native_query.preparation.preparation_identity import preparation_key
from app.native_query.preparation.topology_recipe import validated_recipe
from app.native_query.search.bounds_index import NativeBoundsIndex, nearby_measurements
from app.native_query.search.domain_checkpoint import DomainCheckpointStore
from app.native_query.search.hybrid_domain import HybridSearch
from app.native_query.search.hybrid_geometry import HybridGeometry
from app.native_query.search.search_domain import DomainProgress
from app.operations.progress import ProgressReporter, WorkProgress, progress_items
from app.projects.contracts import Project
from app.regulations.placement_config import PLACEMENT_RULES_REVISION

MAX_CACHED_POINTS = MAX_CACHED_MEASUREMENT_POINTS
POINT_BATCH = 256
QUERY_WINDOW_M = 32.0  # Локальность транспорта, а не допуск геометрии.
SINGLE_WINDOW_POINT_LIMIT = 16
WINDOW_REACH_MULTIPLIER = 4


class LiveNativeGeometryEngine(PlacementPolicy):
    session: LiveSession

    def __init__(
        self,
        session: LiveSession,
        client: CaptureClient,
        *,
        inventory: LiveInventory | None = None,
        linear_layers: frozenset[str] = frozenset(),
    ):
        self.session, self.client = session, client
        self.inventory = inventory if inventory is not None else load_inventory(session)
        # Прежняя семантика линий задаётся явно. Новые захваты также сохраняют
        # читаемые остатки сборки областей как ограничения расстояния.
        self.linear_layers = linear_layers
        factor = meters_per_dxf_unit(session.units_code)
        if factor is None:
            raise ValueError("Единицы чертежа не подтверждены")
        self.factor: float = factor
        self._lock = RLock()
        self._basis_key: tuple[object, ...] | None = None
        self._objects: tuple[QueryObject, ...] = ()
        self._source_objects: tuple[QueryObject, ...] = ()
        self._bounds_index_cache: NativeBoundsIndex | None = None
        self._source_participations: dict[
            tuple[tuple[str, ...], int], SourceParticipation
        ] = {}
        self._layers: dict[str, Layer] = {}
        self._faces: tuple[NativeFace, ...] = ()
        self._face_preparation = NativeFacePreparation()
        self._cache: OrderedDict[
            tuple[float, float], tuple[float, tuple[MeasuredObstacle, ...]]
        ] = OrderedDict()
        self._domain_cache: OrderedDict[
            tuple[object, ...], tuple[dict[str, Any], DomainProgress]
        ] = OrderedDict()
        self._hybrid: HybridSearch | None = None
        self._coverage: GeometryCoverage | None = None
        queue = getattr(client, "queue", None)
        self._domain_checkpoints = (
            DomainCheckpointStore(queue / "search-domains") if queue else None
        )

    def _basis(
        self, project: Project, *, progress: ProgressReporter | None = None
    ) -> None:
        if (
            project.source_file is None
            or project.source_file.content_sha256 != self.session.snapshot_sha256
        ):
            raise ValueError("Карта проекта и native-сеанс относятся к разным захватам")
        layers = "\n".join(layer.model_dump_json() for layer in project.layers)
        reviewed_closures = frozenset(
            "/".join((*item.source.instance_chain, item.source.handle))
            for item in project.source_file.native_area_proposals
            if item.decision == "accepted"
        )
        object_decisions = project.source_file.object_decisions
        recipe = validated_recipe(project)
        area_groups = project.source_file.area_groups
        if any(
            group.source_sha256 != project.source_file.content_sha256
            for group in area_groups
        ):
            raise ValueError("Подтверждённая область относится к другому захвату")
        linear_routes = frozenset(
            "/".join((*item.source.instance_chain, item.source.handle))
            for item in object_decisions
            if item.interpretation == "linear"
        )
        ignored_routes = frozenset(
            "/".join((*item.source.instance_chain, item.source.handle))
            for item in object_decisions
            if item.interpretation == "reference"
        )
        key = (
            project.id,
            project.geometry_version,
            PLACEMENT_RULES_REVISION,
            hashlib.sha256(layers.encode()).hexdigest(),
            tuple(sorted(reviewed_closures)),
            tuple(item.model_dump_json() for item in object_decisions),
            tuple(item.model_dump_json() for item in area_groups),
            tuple(sorted(project.source_file.rejected_native_face_keys)),
            recipe.model_dump_json() if recipe is not None else None,
        )
        if key != self._basis_key:
            indexed = {item.route: item for item in self.inventory.objects}
            if any(
                route not in indexed
                or (route in linear_routes and not indexed[route].curve)
                or indexed[route].context
                or indexed[route].error
                or route in reviewed_closures
                for route in linear_routes | ignored_routes
            ):
                raise ValueError(
                    "Решение об объекте не соответствует текущему захвату AutoCAD"
                )
            self._layers = {layer.source_name: layer for layer in project.layers}
            configure_recipe = getattr(self.client, "set_preparation_recipe", None)
            if callable(configure_recipe):
                configure_recipe(recipe)
            requested_layers = tuple(
                sorted(area_layers(self._layers, self.linear_layers))
            )
            if progress:
                progress(WorkProgress("Восстанавливаем области чертежа"))
            self.inventory = self._face_preparation.prepare(
                self.inventory, self.session, self.client, requested_layers
            )
            if progress:
                progress(WorkProgress("Собираем объекты чертежа"))
            from app.native_query.preparation.reviewed_groups import group_targets

            groups, group_routes = group_targets(
                area_groups, self.inventory, self._layers
            )
            if group_routes & (reviewed_closures | linear_routes | ignored_routes):
                raise ValueError("Для объекта сохранены противоречащие решения")
            rejected_closures = frozenset(
                "/".join((*item.source.instance_chain, item.source.handle))
                for item in project.source_file.native_area_proposals
                if item.decision == "rejected"
            )
            self._faces = tuple(
                active_faces(
                    self.inventory,
                    self._layers,
                    linear_routes
                    | ignored_routes
                    | group_routes
                    | rejected_closures
                    | {
                        "/".join((*item.source.instance_chain, item.source.handle))
                        for item in object_decisions
                        if item.kind is not None
                    },
                    self.linear_layers,
                    frozenset(project.source_file.rejected_native_face_keys),
                )
            )
            native_linear = linear_remainders(
                self.inventory,
                self._layers,
                reviewed_closures | linear_routes | ignored_routes | group_routes,
            )
            for name in self.linear_layers:
                layer = self._layers.get(name)
                if (
                    not layer
                    or not layer.mapping_confirmed
                    or layer.mapped_kind not in {"road", "restricted"}
                ):
                    raise ValueError("Назначение линейного препятствия изменилось")
                if any(
                    not item.curve
                    for item in self.inventory.objects
                    if item.layer == name and not item.context
                ):
                    raise ValueError(
                        "Линейный слой содержит площадные или неподтверждённые объекты"
                    )
            addressed = {
                "/".join(
                    (*decision.source.instance_chain, decision.source.handle)
                ): decision
                for decision in object_decisions
                if decision.kind is not None or decision.interpretation == "reference"
            }
            if group_routes.intersection(addressed):
                raise ValueError("Для объекта сохранены противоречащие решения")
            source_objects = (
                query_objects(
                    self.inventory,
                    # Используем общий допуск областей подготовки AutoCAD. Символы и линейные
                    # объекты сохраняют исходную геометрию: цикл концов не доказывает заполненный габарит.
                    set(requested_layers)
                    | {
                        name
                        for name, layer in self._layers.items()
                        if layer.mapped_kind == "site_border"
                        and layer.mapping_confirmed
                        and name not in self.linear_layers
                    },
                    reviewed_closures=reviewed_closures,
                    linear_routes=linear_routes,
                    ignored_routes=ignored_routes | group_routes | frozenset(addressed),
                    native_linear_routes=native_linear,
                    progress=progress,
                )
                + individual_object_targets(indexed, addressed, reviewed_closures)
                + groups
                + face_targets(self._faces)
            )
            self._bind_source_roles(project, source_objects, indexed, progress=progress)
            self._cache.clear()
            self._domain_cache.clear()
            self._hybrid = None
            self._coverage = None
            # Подготовке снимка не нужен пространственный индекс исходного каталога.
            # Строим его при первом измерении, когда известны точки запроса.
            self._bounds_index_cache = None
            self._basis_key = key

    @property
    def _bounds_index(self) -> NativeBoundsIndex:
        """Один ленивый индекс для точечных измерений и поиска областей."""
        with self._lock:
            if self._bounds_index_cache is None:
                self._bounds_index_cache = NativeBoundsIndex(
                    self._objects, self._layers
                )
            return self._bounds_index_cache

    def _bind_source_roles(
        self,
        project: Project,
        objects: tuple[QueryObject, ...],
        inventory: dict[str, InventoryObject],
        *,
        progress: ProgressReporter | None = None,
    ) -> None:
        """Один набор адресных ролей используется индексом, снимком и покрытием."""
        source = project.source_file
        assert source is not None and source.content_sha256 is not None
        key = preparation_key(project, self.session, self.linear_layers)
        recipe = validated_recipe(project)
        companions = (
            frozenset(recipe.companion_layers) if recipe is not None else frozenset()
        )
        used_routes = frozenset(
            route
            for face in self._faces
            for route in set(face.routes) - set(face.physical_routes)
            if inventory[route].layer in companions
        )
        decisions = {
            "/".join(
                (*decision.source.instance_chain, decision.source.handle)
            ): decision
            for decision in source.object_decisions
        }
        group_kinds: dict[frozenset[str], PhysicalObjectKind] = {
            frozenset(
                "/".join((*member.source.instance_chain, member.source.handle))
                for member in group.members
            ): group.kind
            for group in source.area_groups
        }
        projection = (
            HybridGeometry(
                self, project, require_accuracy=True, cache_projections=False
            )
            if companions
            else None
        )
        participation = {}
        # Валидируем каждое решение, но одинаковые неизменяемые свидетельства
        # храним один раз. Адрес каждого объекта остаётся отдельным ключом.
        participation_pool: dict[SourceParticipation, SourceParticipation] = {}
        items = (
            progress_items(objects, progress, "Проверяем роли объектов", len(objects))
            if progress is not None
            else objects
        )
        for item in items:
            original = (
                inventory[item.routes[0]].layer if not item.face_id else item.layer
            )
            decision = decisions.get(item.routes[0]) if len(item.routes) == 1 else None
            topology = not item.face_id and set(item.routes) <= used_routes
            boundary = False
            if (
                not topology
                and original in companions
                and not item.face_id
                and projection is not None
            ):
                value = projection.get(item, linear=item.curve)
                boundary = (
                    not value.reason and bool(value.geometries) and not item.error
                )
            role_value = classify_source_use(
                self._layers.get(original),
                source_sha256=source.content_sha256,
                prepared_key=key,
                source_layer=original,
                decision=decision,
                prepared_topology=topology,
                prepared_boundary=boundary,
                area_group_kind=group_kinds.get(frozenset(item.routes)),
            )
            participation[source_object_key(item)] = participation_pool.setdefault(
                role_value, role_value
            )
        self._source_participations = participation
        self._source_objects, self._objects = materialize_source_roles(
            progress_items(objects, progress, "Применяем роли объектов", len(objects))
            if progress is not None
            else objects,
            self._layers,
            participation,
        )

    def source_participation(
        self,
        item: QueryObject,
        prepared_key: str,
        *,
        prepared_projection_success: bool | None = None,
    ) -> SourceParticipation:
        """Возвратить адресное свидетельство только того основания подготовки."""
        value = self._source_participations[source_object_key(item)]
        if not value.current(self.session.snapshot_sha256, prepared_key):
            raise ValueError("Участие объекта относится к другой подготовке CAD")
        if (
            prepared_projection_success is False
            and value.proof == SourceUseProof.PREPARED_BOUNDARY
        ):
            return classify_source_use(
                self._layers.get(value.source_layer),
                source_sha256=value.source_sha256,
                prepared_key=prepared_key,
                source_layer=value.source_layer,
            )
        return value

    def review_area_group(
        self, project: Project, request: SourceAreaGroupRequest
    ) -> SourceAreaGroupCheck:
        from app.native_query.preparation.reviewed_groups import verify_group

        return verify_group(self, project, request)

    def source_coverage(self, project: Project) -> GeometryCoverage:
        from app.native_query.search.coverage import source_coverage

        return source_coverage(self, project)

    def native_face_review(self, project: Project) -> NativeFaceReview:
        from app.native_query.preparation.face_review import review_faces

        with self._lock:
            self.assert_current(project)
            return review_faces(project, self.inventory.faces, self._faces, self.factor)

    def assert_current(
        self, project: Project, *, progress: ProgressReporter | None = None
    ) -> None:
        """Обязательная проверка перед применением кешированного предпросмотра
        изменяемого CAD.
        """
        with self._lock:
            self._basis(project, progress=progress)
            try:
                self.client.inspect(self.session)
            except Exception:
                self._cache.clear()
                self._domain_cache.clear()
                self._hybrid = None
                self._coverage = None
                raise

    def _point_measurements(
        self,
        project: Project,
        points: list[tuple[float, float]],
        *,
        reach_m: float = BASE_QUERY_REACH_M,
    ) -> PointMeasurements:
        with self._lock:
            self.assert_current(project)
            if len(set(points)) > MAX_CACHED_POINTS:
                raise ValueError("Разделите размещение на меньшие участки")
            for point in points:
                if point in self._cache:
                    self._cache.move_to_end(point)
            answers = {
                point: self._cache[point]
                for point in points
                if point in self._cache and self._cache[point][0] >= reach_m
            }
            missing = list(
                dict.fromkeys(
                    point
                    for point in points
                    if point not in self._cache or self._cache[point][0] < reach_m
                )
            )
            windows = defaultdict(list)
            window_m = max(QUERY_WINDOW_M, reach_m * WINDOW_REACH_MULTIPLIER)
            for point in missing:
                key = (
                    (floor(point[0] / window_m), floor(point[1] / window_m))
                    if len(missing) > SINGLE_WINDOW_POINT_LIMIT
                    else (0, 0)
                )
                windows[key].append(point)
            measured = {}
            sites = {}
            reader = NativeMeasurementReader(
                self.session,
                self.client,
                self.factor,
                self._bounds_index,
                self._layers,
                self.linear_layers,
            )
            if len(windows) > 1:
                # Протяжённые границы территории измеряются один раз на порцию точек,
                # а не повторно для каждого локального окна препятствий.
                for start in range(0, len(missing), POINT_BATCH):
                    sites.update(
                        reader.measure(
                            missing[start : start + POINT_BATCH],
                            reach_m,
                            sites_only=True,
                        )
                    )
            for window in windows.values():
                for start in range(0, len(window), POINT_BATCH):
                    batch = window[start : start + POINT_BATCH]
                    measured.update(
                        reader.measure(
                            batch, reach_m, sites_only=False if sites else None
                        )
                    )
            if sites:
                measured = {
                    point: (reach_m, rows + sites[point][1])
                    for point, (_, rows) in measured.items()
                }
            if measured:
                # Публикуем ответ только при общем live-основании всех пространственных порций.
                self.client.inspect(self.session)
                self._remember_measurements(measured)
            answers.update(measured)
            return answers

    def _rows(
        self, project: Project, x: float, y: float, radius: float
    ) -> tuple[MeasuredObstacle, ...]:
        with self._lock:
            self._basis(project)
            point = (x, y)
            reach = query_reach_m(radius)
            cached = self._cache.get(point)
            if cached is None or cached[0] < reach:
                cached = self._point_measurements(project, [point], reach_m=reach)[point]
            # Свидетельство области могло кешировать более широкое соседство. Его повторное
            # использование не превращает далёкие неизвестные объекты в локальные.
            return nearby_measurements(
                cached[1],
                self._layers,
                x / self.factor,
                y / self.factor,
                reach / self.factor,
            )

    def calculate(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometrySnapshot:
        self.assert_current(project)
        return display_snapshot(
            project, self._layers, face_features(self._faces, self.factor)
        )
