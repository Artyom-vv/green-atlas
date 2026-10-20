"""Индексированные запросы и ограниченный GeoJSON карты с кешем версий; исходная
расчётная геометрия не заменяется.
"""

from collections import Counter, OrderedDict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from math import isfinite
from threading import RLock
from typing import Any, NamedTuple

from shapely import STRtree, get_num_coordinates
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

from app.dxf_import.contracts import ImportMode
from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.cancellation import check_viewport_cancelled
from app.geometry.viewport.feature_storage import ViewportFeatures
from app.geometry.viewport.persistent_index import (
    DisplayResourceAccess,
    PersistentSelectionCache,
    PersistentViewportFeatures,
    PersistentViewportGeometries,
    PersistentViewportTree,
)
from app.geometry.viewport.source_overview import SourceOverviewCache
from app.geometry.viewport.viewport_admission import (
    PreparedViewportFeature,
    admit_fair_features,
)
from app.geometry.viewport.viewport_budget import (
    DEFAULT_VIEWPORT_CACHE_COORDINATES,
    DEFAULT_VIEWPORT_CACHE_FEATURES,
    DEFAULT_VIEWPORT_CACHE_PROJECTS,
    DEFAULT_VIEWPORT_CACHE_SIMPLIFIED_BYTES,
    DEFAULT_VIEWPORT_COORDINATES,
    DEFAULT_VIEWPORT_FEATURE_BYTES,
    ViewportBudget,
    feature_size,
)
from app.geometry.viewport.viewport_representation import viewport_representation
from app.geometry.viewport.viewport_selection import (
    VIEWPORT_CANDIDATE_WINDOW_FACTOR,
    Candidate,
    select_fair_candidates,
)
from app.geometry.viewport.viewport_simplification import (
    CALCULATED_SURFACE_KINDS,
    DisplayGeometry,
    ViewportSimplifier,
)
from app.operations.progress import (
    OperationCancelled,
    cancellable_lock,
    check_operation_cancelled,
)
from app.projects.contracts import Project
from app.projects.ports import (
    DisplayGeometryResource,
    ProjectDisplayGeometryReader,
    ProjectGeometryStreamReader,
    ProjectGeometryView,
)


class _DisplaySize(NamedTuple):
    coordinates: int
    feature_bytes: int
    fallback: str | None


class _DisplaySizeChanged(Exception):
    """Повторная подготовка требует нового допуска с фактическим размером."""


@dataclass
class _ProjectIndex:
    snapshot_key: str
    features: ViewportFeatures | PersistentViewportFeatures
    geometries: list[BaseGeometry] | PersistentViewportGeometries
    tree: STRtree | PersistentViewportTree
    coordinates: int
    resource_access: DisplayResourceAccess | None = None
    # Долгая навигация охватывает весь городской чертёж. Кеш GeoJSON ограничен,
    # чтобы не хранить упрощённую копию каждого объекта на каждом масштабе.
    # Ключ включает округлённый допуск: подписи mid и overview не задают одинаковую геометрию.
    simplified_geometries: OrderedDict[tuple[float, int], DisplayGeometry] = field(
        default_factory=OrderedDict
    )
    simplified_sizes: dict[tuple[float, int], int] = field(default_factory=dict)
    simplified_bytes: int = 0
    cache_lock: RLock = field(default_factory=RLock)
    display_sizes: OrderedDict[tuple[float, int], _DisplaySize] = field(
        default_factory=OrderedDict
    )
    simplifier: ViewportSimplifier = field(default_factory=ViewportSimplifier)
    source_overviews: SourceOverviewCache = field(default_factory=SourceOverviewCache)
    selection_cache: PersistentSelectionCache = field(
        default_factory=PersistentSelectionCache
    )


class _IndexBuilder:
    """Временный индекс: точные записи и GEOS без исходного дерева координат."""

    def __init__(self, snapshot_key: str, *, compact: bool) -> None:
        self.snapshot_key = snapshot_key
        self.features = ViewportFeatures(compact=compact)
        self.geometries: list[BaseGeometry] = []

    def accept(self, feature: Any) -> None:
        check_operation_cancelled()
        try:
            geometry = shape(feature["geometry"])
        except Exception:
            return
        if not geometry.is_empty:
            self.features.append(feature)
            self.geometries.append(geometry)

    def finish(self) -> _ProjectIndex:
        check_operation_cancelled()
        return _ProjectIndex(
            snapshot_key=self.snapshot_key,
            features=self.features,
            geometries=self.geometries,
            tree=STRtree(self.geometries),
            coordinates=int(get_num_coordinates(self.geometries).sum()),
        )


class IndexedGeometryQuery:
    """Читать окно дискового индекса; старые inline-снимки индексировать в памяти."""

    def __init__(
        self,
        max_features: int = 12_000,
        max_cached_projects: int = DEFAULT_VIEWPORT_CACHE_PROJECTS,
        *,
        max_cached_features: int = DEFAULT_VIEWPORT_CACHE_FEATURES,
        max_cached_coordinates: int = DEFAULT_VIEWPORT_CACHE_COORDINATES,
        max_cached_simplified_bytes: int = DEFAULT_VIEWPORT_CACHE_SIMPLIFIED_BYTES,
        max_coordinates: int = DEFAULT_VIEWPORT_COORDINATES,
        max_feature_bytes: int = DEFAULT_VIEWPORT_FEATURE_BYTES,
        display_repository: ProjectDisplayGeometryReader | None = None,
    ) -> None:
        if max_features < 1:
            raise ValueError("Geometry response budget must be positive")
        if max_cached_projects < 1:
            raise ValueError("Geometry index cache budget must be positive")
        if (
            max_cached_features < 1
            or max_cached_coordinates < 1
            or max_cached_simplified_bytes < 1
        ):
            raise ValueError(
                "Размер кеша объектов и координат должен быть положительным"
            )
        self.max_features = max_features
        self.max_cached_projects = max_cached_projects
        self.max_cached_features = max_cached_features
        self.max_cached_coordinates = max_cached_coordinates
        self.max_cached_simplified_bytes = max_cached_simplified_bytes
        ViewportBudget(max_coordinates, max_feature_bytes)
        self.max_coordinates = max_coordinates
        self.max_feature_bytes = max_feature_bytes
        self.display_repository = display_repository
        self._indexes: OrderedDict[str, _ProjectIndex] = OrderedDict()
        self._lock = RLock()

    def cached_scene_features(self, project: Project) -> list[dict] | None:
        """Reuse admitted 2D polygons, tree symbols and confirmed curb paths.

        Never publishes a truncated viewport as a complete calculation snapshot.
        None asks the display reader to use its normal validated geometry port.
        """
        expected = f"{'calculated' if project.geometry is not None else 'source'}:{project.geometry_version}"
        tree_layers = {
            row.source_name
            for row in project.layers
            if row.mapping_confirmed
            and row.mapped_kind == "existing_green"
            and row.category == "tree"
        }
        curb_layers = {
            row.source_name
            for row in project.layers
            if row.mapping_confirmed
            and row.mapped_kind in {"road", "lawn"}
            and row.category == "curb"
        }
        # Явное открытие 3D может предшествовать карте. Дисковый ресурс отдаёт
        # только нужные классы, не требуя готового viewport или полного GeoJSON.
        if self.display_repository is not None:
            with self.display_repository.open_display_geometry(project.id) as opened:
                if opened is not None:
                    view, resource = opened
                    if self._snapshot_key(view) != expected:
                        return None
                    features = []
                    for feature in resource.iter_scene_candidates(
                        tree_layers, curb_layers
                    ):
                        check_operation_cancelled()
                        features.append(feature)
                    return features
        with self._lock:
            index = self._indexes.get(project.id)
            if index is None or index.snapshot_key != expected:
                return None
            if index.resource_access is not None:
                with index.resource_access.repository.open_display_geometry(
                    project.id
                ) as opened:
                    if opened is None:
                        return None
                    view, resource = opened
                    if (
                        self._snapshot_key(view) != expected
                        or resource.identity != index.resource_access.identity
                    ):
                        return None
                    return list(
                        resource.iter_scene_candidates(tree_layers, curb_layers)
                    )
            assert isinstance(index.geometries, list)
            features = []
            for i, polygon in enumerate(index.geometries):
                summary = index.features.metadata(i)
                properties = summary.get("properties", {})
                if (
                    properties.get("source_layer") in tree_layers
                    or (
                        properties.get("source_layer") in curb_layers
                        and properties.get("kind") in {"road", "lawn"}
                        and polygon.geom_type in {"LineString", "MultiLineString"}
                    )
                    or (
                        properties.get("kind") in {"building", "road", "lawn", "water"}
                        and polygon.geom_type in {"Polygon", "MultiPolygon"}
                    )
                ):
                    features.append(index.features.feature(i))
            return features

    @property
    def cached_project_count(self) -> int:
        with self._lock:
            return len(self._indexes)

    def discard(self, project_id: str) -> None:
        """Сразу освобождать пространственный индекс устойчиво удалённого проекта.

        LRU ограничивает последовательность чертежей, но крупный индекс мог бы ждать
        следующего проекта. После удаления исходника и плана его хранение расходует
        память и размывает владение кешем.
        """
        with self._lock:
            self._indexes.pop(project_id, None)

    def _index(self, project: Project | ProjectGeometryView) -> _ProjectIndex:
        snapshot_kind = "calculated" if project.geometry is not None else "source"
        snapshot_model = project.geometry or project.source_geometry
        if snapshot_model is None:
            raise ValueError("Сначала импортируйте DXF")
        snapshot = snapshot_model.feature_collection
        with self._lock:
            cached = self._indexes.get(project.id)
            # Индекс зависит от снимка геометрии. Ревизия состояния меняется и при правках
            # посадок и метаданных; её включение перестраивало всю геометрию перед запросом карты.
            snapshot_key = f"{snapshot_kind}:{project.geometry_version}"
            if cached is not None and cached.snapshot_key == snapshot_key:
                self._indexes.move_to_end(project.id)
                return cached
            self._indexes.pop(project.id, None)
            del cached
            # Освобождаем прошлые крупные источники до создания нового GEOS-индекса,
            # чтобы переключение проекта не удваивало долгоживущую геометрию.
            self._evict_for_budget(len(snapshot.get("features", [])), 0)
            builder = _IndexBuilder(
                snapshot_key,
                compact=len(snapshot.get("features", [])) > self.max_cached_features,
            )
            for feature in snapshot.get("features", []):
                builder.accept(feature)
            indexed = builder.finish()
            self._publish(project.id, indexed)
            return indexed

    def _publish(self, project_id: str, indexed: _ProjectIndex) -> None:
        check_operation_cancelled()
        with self._lock:
            self._evict_for_budget(len(indexed.features), indexed.coordinates)
            self._indexes[project_id] = indexed
            self._indexes.move_to_end(project_id)
            while len(self._indexes) > self.max_cached_projects:
                self._indexes.popitem(last=False)

    def query_streamed(
        self,
        reader: ProjectGeometryStreamReader,
        project_id: str,
        extent: tuple[float, float, float, float],
        resolution: float,
    ) -> GeometrySnapshot:
        """Опубликовать индекс только после полного проверенного чтения хранилища."""
        self._validate_query(extent, resolution)
        if isinstance(reader, ProjectDisplayGeometryReader):
            with self._open_display_viewport(reader, project_id) as opened:
                if opened is not None:
                    view, resource, indexed = opened
                    assert indexed.resource_access is not None
                    with indexed.resource_access.activate(resource):
                        return self.query(
                            view, extent, resolution, cached_index=indexed
                        )

        def create(view: ProjectGeometryView, count: int) -> _IndexBuilder:
            if view.geometry is None and view.source_geometry is None:
                raise ValueError("Сначала импортируйте DXF")
            kind = "calculated" if view.geometry is not None else "source"
            with self._lock:
                self._indexes.pop(project_id, None)
                self._evict_for_budget(count, 0)
            return _IndexBuilder(
                f"{kind}:{view.geometry_version}",
                compact=count > self.max_cached_features,
            )

        view, indexed = reader.consume_geometry_view(project_id, create)
        self._publish(project_id, indexed)
        return self.query(view, extent, resolution, cached_index=indexed)

    @contextmanager
    def _open_display_viewport(
        self, reader: ProjectDisplayGeometryReader, project_id: str
    ) -> Iterator[
        tuple[ProjectGeometryView, DisplayGeometryResource, _ProjectIndex] | None
    ]:
        indexed, protected = None, False
        # Этот lock живёт дольше context ресурса: окончательные SHA/ревизия
        # проверяются до публикации изменённых кешей другому запросу окна.
        with ExitStack() as locks:
            try:
                with reader.open_display_geometry(
                    project_id, wait_check=check_viewport_cancelled
                ) as opened:
                    if opened is None:
                        yield None
                        return
                    view, resource = opened
                    indexed = self._persistent_index(reader, view, resource)
                    locks.enter_context(
                        cancellable_lock(
                            indexed.cache_lock, check=check_viewport_cancelled
                        )
                    )
                    protected = True
                    yield view, resource, indexed
            except Exception:
                if protected and indexed is not None:
                    # Запрос мог уже держать ссылку на прежний index и ждать lock.
                    # Поэтому очищаем сам объект, а не только запись LRU.
                    indexed.simplified_geometries.clear()
                    indexed.simplified_sizes.clear()
                    indexed.simplified_bytes = 0
                    indexed.display_sizes.clear()
                    indexed.simplifier = ViewportSimplifier()
                    indexed.selection_cache.clear()
                    indexed.source_overviews = SourceOverviewCache(
                        cache_paths=False, stable_paths=True
                    )
                    with self._lock:
                        if self._indexes.get(project_id) is indexed:
                            self._indexes.pop(project_id)
                raise

    @staticmethod
    def _snapshot_key(project: Project | ProjectGeometryView) -> str:
        kind = "calculated" if project.geometry is not None else "source"
        return f"{kind}:{project.geometry_version}"

    def _persistent_index(
        self,
        reader: ProjectDisplayGeometryReader,
        view: ProjectGeometryView,
        resource: DisplayGeometryResource,
    ) -> _ProjectIndex:
        snapshot_key = self._snapshot_key(view)
        with cancellable_lock(self._lock, check=check_viewport_cancelled):
            cached = self._indexes.get(view.id)
            if (
                cached is not None
                and cached.snapshot_key == snapshot_key
                and cached.resource_access is not None
                and cached.resource_access.identity == resource.identity
            ):
                self._indexes.move_to_end(view.id)
                return cached
            self._indexes.pop(view.id, None)
            access = DisplayResourceAccess(reader, resource.identity, resource.count)
            indexed = _ProjectIndex(
                snapshot_key=snapshot_key,
                features=PersistentViewportFeatures(access),
                geometries=PersistentViewportGeometries(access),
                tree=PersistentViewportTree(access),
                coordinates=resource.coordinates,
                resource_access=access,
                # Кеш хранит строки SVG по ordinal неизменяемого ресурса.
                # GEOS декодируется только при промахе и не удерживается кешем.
                source_overviews=SourceOverviewCache(
                    cache_paths=False, stable_paths=True
                ),
            )
            self._publish(view.id, indexed)
            return indexed

    def _evict_for_budget(self, features: int, coordinates: int) -> None:
        """Сохранять целый активный индекс; его размер не урезает исходные объекты."""
        while self._indexes and (
            len(self._indexes) >= self.max_cached_projects
            or features + sum(len(index.features) for index in self._indexes.values())
            > self.max_cached_features
            or coordinates + sum(index.coordinates for index in self._indexes.values())
            > self.max_cached_coordinates
        ):
            self._indexes.popitem(last=False)

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
        project: Project | ProjectGeometryView,
        extent: tuple[float, float, float, float],
        resolution: float,
    ) -> GeometrySnapshot | None:
        """Выдавать viewport по готовому индексу без загрузки DXF JSON.

        Компактная проекция определяет снимок по проекту, виду и версии геометрии.
        При промахе возвращаем None, чтобы приложение однократно загрузило полное
        действующее состояние.
        """
        self._validate_query(extent, resolution)
        snapshot_kind = "calculated" if project.geometry is not None else "source"
        snapshot_key = f"{snapshot_kind}:{project.geometry_version}"
        with cancellable_lock(self._lock, check=check_viewport_cancelled):
            cached = self._indexes.get(project.id)
            if cached is None or cached.snapshot_key != snapshot_key:
                self._indexes.pop(project.id, None)
                # Холодный читатель ещё не распаковал полный GeoJSON. Число
                # native-проекций уже известно из метаданных: освобождаем прежний
                # крупный индекс до загрузки нового, сохраняя полный активный снимок.
                source = project.source_file if isinstance(project, Project) else None
                if source is not None and source.native_session is not None:
                    if source.cad_snapshot_provenance is not None:
                        self._evict_for_budget(
                            source.cad_snapshot_provenance.native_geometry, 0
                        )
                return None
            self._indexes.move_to_end(project.id)
        if cached.resource_access is not None:
            access = cached.resource_access
            with self._open_display_viewport(access.repository, project.id) as opened:
                if opened is None:
                    self.discard(project.id)
                    return None
                view, resource, current = opened
                if self._snapshot_key(view) != snapshot_key:
                    self.discard(project.id)
                    return None
                assert current.resource_access is not None
                with current.resource_access.activate(resource):
                    return self.query(view, extent, resolution, cached_index=current)
        return self.query(project, extent, resolution, cached_index=cached)

    def query(
        self,
        project: Project | ProjectGeometryView,
        extent: tuple[float, float, float, float],
        resolution: float,
        *,
        cached_index: _ProjectIndex | None = None,
    ) -> GeometrySnapshot:
        self._validate_query(extent, resolution)
        if cached_index is None:
            with self._lock:
                cached = self._indexes.get(project.id)
            if cached is not None and cached.resource_access is not None:
                # Прямой вызов порта после оконного чтения тоже открывает
                # проверенный ресурс, а не обращается к закрытому ContextVar.
                result = self.query_cached(project, extent, resolution)
                if result is not None:
                    return result
        indexed = cached_index or self._index(project)
        check_viewport_cancelled()
        representation = viewport_representation(resolution)
        viewport = box(*extent)
        matched_indexes = indexed.tree.query(viewport)

        def visible_candidates() -> Iterator[tuple[int, dict]]:
            """Выдавать только подробности, разрешённые этим масштабом.

            Запретные области исчезают на обзорном масштабе до учёта ответа;
            метаданные, признак усечения и список объектов сохраняют одинаковую
            семантику.
            """
            candidates = (
                indexed.resource_access.resource.iter_metadata(matched_indexes)
                if indexed.resource_access is not None
                else (
                    (int(index), indexed.features.metadata(int(index)))
                    for index in matched_indexes
                )
            )
            for index, feature in candidates:
                check_viewport_cancelled()
                kind = str(feature.get("properties", {}).get("kind", ""))
                if not representation.show_forbidden and kind == "forbidden":
                    continue
                yield index, feature

        if indexed.resource_access is not None:
            selection = indexed.selection_cache.select(
                visible_candidates,
                self.max_features * VIEWPORT_CANDIDATE_WINDOW_FACTOR,
                matched_indexes.tobytes(),
                show_forbidden=representation.show_forbidden,
            )
        else:
            selection = select_fair_candidates(
                visible_candidates,
                self.max_features * VIEWPORT_CANDIDATE_WINDOW_FACTOR,
            )
        total_matches = selection.total_matches
        total_by_kind = selection.total_by_kind
        truncated = total_matches > self.max_features

        # Ключ кеша и расчёт используют одинаковое квантование допуска.
        simplify_tolerance = representation.simplify_tolerance
        returned_by_kind: dict[str, int] = {}
        budget = ViewportBudget(self.max_coordinates, self.max_feature_bytes)
        truncation_reasons = {"features"} if truncated else set()
        simplification_fallbacks: Counter[str] = Counter()

        def retain_simplified_locked(
            cache_key: tuple[float, int],
            geometry: DisplayGeometry,
            size: tuple[int, int],
        ) -> None:
            if not cache_key[0] or size[1] > self.max_cached_simplified_bytes:
                return
            if cache_key in indexed.simplified_geometries:
                indexed.simplified_geometries.move_to_end(cache_key)
                return
            # Размер полного Feature консервативно ограничивает удерживаемые
            # проекции. Повторный допуск сохраняет тот же предел, включая масштабы.
            while indexed.simplified_geometries and (
                len(indexed.simplified_geometries) >= self.max_features
                or indexed.simplified_bytes + size[1] > self.max_cached_simplified_bytes
            ):
                expired, _ = indexed.simplified_geometries.popitem(last=False)
                indexed.simplified_bytes -= indexed.simplified_sizes.pop(expired)
            indexed.simplified_geometries[cache_key] = geometry
            indexed.simplified_sizes[cache_key] = size[1]
            indexed.simplified_bytes += size[1]

        def prepare_full_locked(
            candidate: Candidate, *, reuse_sizes: bool, record_fallback: bool = True
        ) -> PreparedViewportFeature:
            feature_index, _ = candidate
            cache_key = (simplify_tolerance, feature_index)
            envelope_only = False
            if simplify_tolerance and isinstance(
                indexed.features, PersistentViewportFeatures
            ):
                feature = indexed.features.envelope(feature_index)
                envelope_only = True
            else:
                feature = indexed.features.feature(feature_index)
            prepared = dict(feature)
            simplified_geometry = None
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
                            else (
                                indexed.features.feature(feature_index)["geometry"]
                                if envelope_only
                                else feature["geometry"]
                            ),
                            fallback,
                        )
                    else:
                        indexed.simplified_geometries.move_to_end(cache_key)
                    prepared["geometry"] = simplified_geometry.geometry
                    if record_fallback and simplified_geometry.fallback:
                        simplification_fallbacks[simplified_geometry.fallback] += 1
                except OperationCancelled:
                    raise
                except Exception:
                    # При сбое упрощения отдаём точную геометрию. Её размер не кешируется
                    # как размер будущего упрощённого результата.
                    if envelope_only:
                        prepared = dict(indexed.features.feature(feature_index))
                    cache_key = (0.0, feature_index)
            size_entry = indexed.display_sizes.get(cache_key)
            fallback = simplified_geometry.fallback if simplified_geometry else None
            if size_entry is None or not reuse_sizes:
                size = feature_size(prepared)
                indexed.display_sizes[cache_key] = _DisplaySize(*size, fallback)
                # Окно дополнения рассматривает больше кандидатов, чем помещается
                # в ответ. Их числовые размеры должны пережить следующий запрос.
                while (
                    len(indexed.display_sizes)
                    > self.max_features * VIEWPORT_CANDIDATE_WINDOW_FACTOR
                ):
                    indexed.display_sizes.popitem(last=False)
            else:
                indexed.display_sizes.move_to_end(cache_key)
                size = size_entry[:2]
            if simplified_geometry is not None:
                retain_simplified_locked(cache_key, simplified_geometry, size)
            return PreparedViewportFeature(
                feature_index, prepared, size, fallback=fallback
            )

        def prepare_locked(
            candidate: Candidate, *, reuse_sizes: bool, record_fallback: bool = True
        ) -> PreparedViewportFeature:
            feature_index, metadata = candidate
            cache_key = (simplify_tolerance, feature_index)
            cached_size = indexed.display_sizes.get(cache_key)
            cached_geometry = (
                indexed.simplified_geometries.get(cache_key)
                if simplify_tolerance
                else None
            )
            if reuse_sizes and cached_size is not None:
                # Порядок и допуск определяются краткими признаками и уже
                # измеренными байтами. Отклонённым кандидатам не нужен полный JSON.
                indexed.display_sizes.move_to_end(cache_key)
                if cached_geometry is not None:
                    indexed.simplified_geometries.move_to_end(cache_key)
                if record_fallback and cached_size.fallback:
                    simplification_fallbacks[cached_size.fallback] += 1

                def materialize() -> dict:
                    if simplify_tolerance and cached_geometry is None:
                        # Размер допуска не заменяет координаты. Вытеснённую
                        # геометрию восстанавливаем только у выбранного объекта
                        # и проверяем, что временный сбой упрощения не изменил бюджет.
                        with cancellable_lock(
                            indexed.cache_lock, check=check_viewport_cancelled
                        ):
                            actual = prepare_full_locked(
                                candidate, reuse_sizes=False, record_fallback=False
                            )
                        if (
                            actual.size != cached_size[:2]
                            or actual.fallback != cached_size.fallback
                        ):
                            raise _DisplaySizeChanged
                        return actual.feature
                    prepared = dict(
                        indexed.features.envelope(feature_index)
                        if cached_geometry is not None
                        and isinstance(indexed.features, PersistentViewportFeatures)
                        else indexed.features.feature(feature_index)
                    )
                    if cached_geometry is not None:
                        prepared["geometry"] = cached_geometry.geometry
                        if cached_geometry.fallback is None:
                            # Контур мог быть вытеснен после создания closure
                            # другими кандидатами. Окончательно выбранный ответ
                            # должен сохранить приоритет перед отклонёнными.
                            with cancellable_lock(
                                indexed.cache_lock, check=check_viewport_cancelled
                            ):
                                retain_simplified_locked(
                                    cache_key, cached_geometry, cached_size[:2]
                                )
                    return prepared

                return PreparedViewportFeature(
                    feature_index,
                    metadata,
                    cached_size[:2],
                    materialize,
                    cached_size.fallback,
                )
            return prepare_full_locked(
                candidate, reuse_sizes=reuse_sizes, record_fallback=record_fallback
            )

        def prepare(
            candidate: Candidate, *, reuse_sizes: bool
        ) -> PreparedViewportFeature:
            with cancellable_lock(indexed.cache_lock, check=check_viewport_cancelled):
                return prepare_locked(candidate, reuse_sizes=reuse_sizes)

        def admit(
            *, reuse_sizes: bool
        ) -> tuple[list[PreparedViewportFeature], set[str], list[dict]]:
            nonlocal budget, simplification_fallbacks
            check_viewport_cancelled()
            budget = ViewportBudget(self.max_coordinates, self.max_feature_bytes)
            simplification_fallbacks = Counter()
            matches, reasons = admit_fair_features(
                selection.candidates,
                budget,
                lambda candidate: prepare(candidate, reuse_sizes=reuse_sizes),
                self.max_features,
            )
            if not truncated:
                matches.sort(key=lambda item: item.index)
            features = []
            for item in matches:
                check_viewport_cancelled()
                features.append(item.materialize())
            return matches, reasons, features

        try:
            selected_matches, admission_reasons, selected = admit(reuse_sizes=True)
        except _DisplaySizeChanged:
            # Единственный повтор прежнего отбора с полной подготовкой: свежие размеры,
            # диагностика и полный бюджет. Во втором проходе нет отложенных
            # объектов и повторных попыток, даже если simplify снова отказал.
            selected_matches, admission_reasons, selected = admit(reuse_sizes=False)
        truncation_reasons.update(admission_reasons)
        for item in selected_matches:
            kind = str(item.feature.get("properties", {}).get("kind", "source"))
            returned_by_kind[kind] = returned_by_kind.get(kind, 0) + 1

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
            if indexed.resource_access is not None:
                resource = indexed.resource_access.resource
                overview = indexed.source_overviews.render_lazy(
                    resource.iter_metadata(matched_indexes),
                    resource.geometry,
                    extent,
                    resolution,
                    matched_indexes.tobytes(),
                )
            else:

                def overview_candidates() -> Iterator[tuple[dict, BaseGeometry]]:
                    for index in matched_indexes:
                        yield (
                            indexed.features.metadata(int(index)),
                            indexed.geometries[int(index)],
                        )

                overview = indexed.source_overviews.render(
                    overview_candidates(),
                    extent,
                    resolution,
                    matched_indexes.tobytes(),
                )
        check_viewport_cancelled()
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
