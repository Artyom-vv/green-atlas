"""Загрузка локального расчётного основания проекта; подготовка CAD запускается только
явной операцией.
"""

from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from threading import RLock
from typing import Any

from shapely.errors import GEOSException

from app.dxf_import.source.admission import (
    pending_native_layer_mapping,
    require_calculation_source,
    require_confirmed_site_boundary,
)
from app.dxf_import.units import meters_per_dxf_unit
from app.geometry.contracts import GeometrySnapshot
from app.geometry.coverage_contracts import GeometryCoverage
from app.geometry.evidence_contracts import GeometryEvidence
from app.geometry.ports import GeometryEnginePort
from app.native_query.archive.archive_client import ArchiveGeometryClient
from app.native_query.capture.capture_client import CaptureClient
from app.native_query.capture.capture_contracts import ARCHIVED_PLUGIN_VERSIONS
from app.native_query.capture.capture_store import NativeCaptureStore
from app.native_query.live.live_client import LiveQueryClient, LiveQueryError
from app.native_query.live.live_runtime import BoundLiveGeometry
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.coverage_cache import (
    PreparedCoverageCache,
    coverage_identity,
)
from app.native_query.preparation.preparation_identity import (
    CaptureIdentity,
    preparation_basis,
)
from app.native_query.preparation.prepared_snapshot import (
    FileFingerprint,
    PreparedCapture,
    PreparedGeometryUnavailable,
    PreparedSnapshot,
    PreparedState,
    PreparedStore,
    preparation_key,
    prepare_snapshot,
)
from app.native_query.preparation.prepared_storage import (
    PreparedSqliteStore,
    SqlitePreparedResource,
)
from app.native_query.preparation.topology_recipe import select_topology_recipe
from app.native_query.search.domain_checkpoint import DomainCheckpointStore
from app.native_query.transport.process_contracts import NativeQueryProcessConfig
from app.operations.progress import (
    ProgressReporter,
    WorkProgress,
    cancellable_lock,
    check_operation_cancelled,
)
from app.planting_zones.contracts import PlantingZoneAssignment
from app.planting_zones.domain import (
    validate_changed_planting_zones,
    validate_planting_zones,
)
from app.planting_zones.ports import ZoneValidator
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import PreparedAvailability, Project
from app.projects.resource_scope import project_resource_scope
from app.shared.failures import DomainFailure, FailureReason
from app.shared.performance import performance_span
from app.validation.contracts import CalculationInputBasis

# Крупное расчётное основание принадлежит активному проекту. Уже выполняемый
# запрос удерживает свой движок до завершения независимо от вытеснения кеша.
MAX_CACHED_PREPARED_PROJECTS = 1
MAX_CACHED_LIVE_PROJECTS = 1
MAX_CACHED_COVERAGE_PROJECTS = 2


def _same_coverage_counts(engine: PreparedGeometryEngine, project: Project) -> bool:
    """Число объектов участвует в полноте покрытия, но не в физическом основании."""
    return {name: layer.object_count for name, layer in engine._layers.items()} == {
        layer.source_name: layer.object_count for layer in project.layers
    }


@dataclass(frozen=True, slots=True)
class _CoverageRead:
    """Сводка хранит основание и отпечаток, не ссылаясь на большой движок."""

    identity: str
    fingerprint: FileFingerprint
    linear_layers: frozenset[str]
    coverage: GeometryCoverage


class ProjectLiveGeometry:
    def __init__(
        self,
        compatibility: GeometryEnginePort,
        compatibility_zones: ZoneValidator | None = None,
        *,
        prepared_store: PreparedStore | None = None,
        archive_store: NativeCaptureStore | None = None,
        archive_config_factory: Callable[[], NativeQueryProcessConfig] | None = None,
    ) -> None:
        self.compatibility = compatibility
        self.compatibility_zones = compatibility_zones
        self._engines: OrderedDict[str, tuple[str, LiveNativeGeometryEngine]] = (
            OrderedDict()
        )
        self._lock = RLock()
        self.prepared_store = prepared_store
        self._binary_prepared = (
            PreparedSqliteStore(
                prepared_store.directory,
                database_path=prepared_store.database_path,
                resource_lifecycle=prepared_store.resource_lifecycle,
            )
            if prepared_store is not None
            else None
        )
        self.archive_store = archive_store
        self.archive_config_factory = archive_config_factory
        self._prepared: OrderedDict[str, PreparedGeometryEngine] = OrderedDict()
        self._coverage_reads: OrderedDict[str, _CoverageRead] = OrderedDict()
        self._coverage_cache = (
            PreparedCoverageCache(prepared_store.directory) if prepared_store else None
        )

    def live_engine(self, project: Project) -> GeometryEnginePort:
        source = project.source_file
        if source is None or source.native_session is None:
            return self.compatibility
        session = source.native_session
        if source.content_sha256 != session.snapshot_sha256:
            raise ValueError("Расчётный сеанс не соответствует исходным данным проекта")
        provenance = source.cad_snapshot_provenance
        face_inputs_sha256 = (
            provenance.face_inputs_sha256
            if provenance is not None
            and provenance.source_sha256 == source.content_sha256
            else None
        )
        key = (
            session.model_dump_json()
            + (
                source.preparation_recipe.model_dump_json()
                if source.preparation_recipe is not None
                else ""
            )
            + (face_inputs_sha256 or "")
        )
        with cancellable_lock(self._lock):
            current = self._engines.get(project.id)
            if current is None or current[0] != key:
                self._engines.pop(project.id, None)
                del current
                while len(self._engines) >= MAX_CACHED_LIVE_PROJECTS:
                    self._engines.popitem(last=False)
                try:
                    client: CaptureClient
                    inventory = None
                    if session.plugin_version in ARCHIVED_PLUGIN_VERSIONS:
                        if (
                            self.archive_store is None
                            or self.archive_config_factory is None
                        ):
                            raise ValueError("Сохранённый CAD-архив проекта недоступен")
                        # Разрешаем пути восстановленного профиля и передаём ту же
                        # проверенную модель клиенту и движку, без повторного чтения.
                        session, inventory = self.archive_store.retained_inputs(
                            project.id, session
                        )
                        client = ArchiveGeometryClient(
                            self.archive_store,
                            project.id,
                            session,
                            self.archive_config_factory,
                            inventory=inventory,
                            face_inputs_sha256=face_inputs_sha256,
                        )
                    else:
                        client = LiveQueryClient(pid=session.pid)
                    engine = LiveNativeGeometryEngine(
                        session, client, inventory=inventory
                    )
                except FileNotFoundError as error:
                    raise LiveQueryError("live_session_expired") from error
                self._engines[project.id] = (key, engine)
            elif session.plugin_version in ARCHIVED_PLUGIN_VERSIONS:
                # Попадание в кеш движка не отменяет проверки сохранённых файлов.
                current[1].client.inspect(current[1].session)
            self._engines.move_to_end(project.id)
            return self._engines[project.id][1]

    @performance_span("native.load_local")
    def load_local(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometryEnginePort:
        """Загружать проверенное локальное основание; промах кеша не является
        CAD-запросом.
        """
        source = project.source_file
        session = source.native_session if source else None
        if session is None:
            if isinstance(self.compatibility, BoundLiveGeometry):
                if self.compatibility.binding.project_id == project.id:
                    raise PreparedGeometryUnavailable("unsupported")
                return self.compatibility.legacy
            return self.compatibility
        if self.prepared_store is None:
            raise PreparedGeometryUnavailable("missing")
        key = preparation_key(project, session)
        with cancellable_lock(self._lock):
            cached = self._prepared.get(project.id)
            if (
                cached is not None
                and cached._geometry_key == key
                and self.prepared_store.unchanged(key)
                and cached._storage_fingerprint
                == self.prepared_store.verified_fingerprint(key)
            ):
                try:
                    cached.assert_current(project)
                except PreparedGeometryUnavailable as error:
                    self._prepared.pop(project.id, None)
                    self.prepared_store.record_failure(key, error.state)
                    raise
                self._prepared.move_to_end(project.id)
                return cached
            self._prepared.clear()
            del cached
            if progress:
                progress(WorkProgress("Загружаем сохранённую геометрию"))
            snapshot = self._load_snapshot(project, session)
            if progress and isinstance(snapshot, SqlitePreparedResource):
                count = len(snapshot.records)
                progress(
                    WorkProgress(
                        "Проверили подготовленные объекты",
                        processed=count,
                        total=count,
                        unit="объектов",
                    )
                )
            return self._cache_snapshot(project, snapshot, progress)

    @performance_span("native.snapshot.open")
    def _load_snapshot(
        self, project: Project, session: CaptureIdentity
    ) -> PreparedSnapshot | SqlitePreparedResource:
        """Новые основания читаются из WKB; старые схемы сохраняют свой адаптер."""
        assert self.prepared_store is not None
        key = preparation_key(project, session)
        source_path = self.prepared_store.directory / f"{key}.json"
        if source_path.is_file() and self._binary_prepared is not None:
            try:
                resource = self._binary_prepared.open(
                    source_path, key, project_id=project.id
                )
                if (
                    resource.capture.model_dump()
                    != {
                        name: getattr(session, name)
                        for name in PreparedCapture.model_fields
                    }
                    or resource.factor != meters_per_dxf_unit(session.units_code)
                    or resource.linear_layers
                ):
                    raise ValueError(
                        "Сохранённая геометрия относится к другому захвату"
                    )
                resource.assert_unchanged()
                self.prepared_store.accept_verified(
                    key,
                    resource.source_fingerprint,
                    source_sha256=resource.registration.source_sha256,
                )
                return resource
            except PreparedGeometryUnavailable as error:
                self.prepared_store.record_failure(key, error.state)
                raise
            except (ValueError, OSError, KeyError, TypeError, GEOSException) as error:
                self.prepared_store.record_failure(key, "corrupt")
                raise PreparedGeometryUnavailable("corrupt") from error
        return self.prepared_store.load_project(project, session)

    def _cache_snapshot(
        self,
        project: Project,
        snapshot: PreparedSnapshot | SqlitePreparedResource,
        progress: ProgressReporter | None,
    ) -> PreparedGeometryEngine:
        """Публиковать движок только для проверенных и неизменённых байтов снимка."""
        if self.prepared_store is None:
            raise PreparedGeometryUnavailable("missing")
        with cancellable_lock(self._lock):
            self._prepared.clear()
            engine = self._validated_snapshot_engine(project, snapshot, progress)
            self._prepared[project.id] = engine
            while len(self._prepared) > MAX_CACHED_PREPARED_PROJECTS:
                self._prepared.popitem(last=False)
            return engine

    @performance_span("native.engine.bind")
    def _validated_snapshot_engine(
        self,
        project: Project,
        snapshot: PreparedSnapshot | SqlitePreparedResource,
        progress: ProgressReporter | None = None,
    ) -> PreparedGeometryEngine:
        """Одинаково проверять расчётный движок и временное чтение паспорта."""
        if self.prepared_store is None:
            raise PreparedGeometryUnavailable("missing")
        try:
            fingerprint = self.prepared_store.verified_fingerprint(snapshot.key)
            if fingerprint is None:
                raise ValueError("Сохранённая геометрия изменилась")
            engine = PreparedGeometryEngine(
                snapshot,
                project,
                checkpoints=DomainCheckpointStore(
                    self.prepared_store.directory / "domains",
                    resource_lifecycle=self.prepared_store.resource_lifecycle,
                    project_id=project.id,
                    resource_kind="domain",
                ),
                progress=progress,
            )
            check_operation_cancelled()
            if self.prepared_store.verified_fingerprint(snapshot.key) != fingerprint:
                raise ValueError("Сохранённая геометрия изменилась")
        except (ValueError, KeyError, TypeError, GEOSException) as error:
            self.prepared_store.record_failure(snapshot.key, "corrupt")
            raise PreparedGeometryUnavailable("corrupt") from error
        engine._storage_fingerprint = fingerprint
        return engine

    def engine(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometryEnginePort:
        return self.load_local(project, progress)

    def prepare_current_capture(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometrySnapshot:
        """Явная операция геометрии; чтение и размещение посадок её не вызывают."""
        source = project.source_file
        session = source.native_session if source else None
        if session is None:
            return self.live_engine(project).calculate(project, progress)
        return self._load_or_prepare_capture(project, progress).calculate(
            project, progress
        )

    def prepare_capture(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> None:
        """Явная физическая подготовка без подтверждения ролей или посадок."""
        session = project.source_file.native_session if project.source_file else None
        if session is None or session.plugin_version not in ARCHIVED_PLUGIN_VERSIONS:
            raise ValueError("В проекте не сохранён CAD-архив")
        self._load_or_prepare_capture(project, progress)

    def prepare_capture_from_repository(
        self,
        load_source: Callable[[str], Project],
        project: Project,
        progress: ProgressReporter | None = None,
    ) -> None:
        """Владеть полным чтением только во время явной физической подготовки.

        Операция передаёт метаданные, чтобы не удерживать вторую ссылку на исходный
        граф до записи снимка. Прежние методы с полным Project сохраняют его
        геометрию для вызывающего кода.
        """
        if project.geometry is not None or project.source_geometry is not None:
            raise ValueError("Подготовка из хранилища принимает только метаданные")
        session = project.source_file.native_session if project.source_file else None
        if session is None or session.plugin_version not in ARCHIVED_PLUGIN_VERSIONS:
            raise ValueError("В проекте не сохранён CAD-архив")
        self._load_or_prepare_capture(project, progress, load_source=load_source)

    @staticmethod
    def _load_preparation_source(
        candidate: Project, load_source: Callable[[str], Project]
    ) -> Project:
        """Получить отдельную оболочку исходника без изменения внешнего Project."""
        check_operation_cancelled()
        source = load_source(candidate.id)
        check_operation_cancelled()
        if source.id != candidate.id or source.state_version != candidate.state_version:
            raise ProjectVersionConflict(
                candidate.id, candidate.state_version, source.state_version
            )
        if (
            source.source_file is None
            or candidate.source_file is None
            or source.source_file.content_sha256 != candidate.source_file.content_sha256
            or source.source_file.native_session != candidate.source_file.native_session
        ):
            raise ValueError("Исходная геометрия относится к другому захвату")
        return candidate.model_copy(
            update={
                "geometry": source.geometry,
                "source_geometry": source.source_geometry,
            }
        )

    @staticmethod
    def _capture_candidate(project: Project) -> Project:
        # Рецепт меняется отдельно от сохранённого проекта только после проверки.
        candidate = project.model_copy(deep=False)
        if project.source_file is not None:
            candidate.source_file = project.source_file.model_copy(
                update={"preparation_recipe": select_topology_recipe(project)}
            )
        return candidate

    def reuse_prepared_capture(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> bool:
        """Проверять готовый снимок по метаданным без загрузки отображения и Core."""
        session = project.source_file.native_session if project.source_file else None
        if session is None or session.plugin_version not in ARCHIVED_PLUGIN_VERSIONS:
            raise ValueError("В проекте не сохранён CAD-архив")
        candidate = self._capture_candidate(project)
        try:
            self.load_local(candidate, progress)
        except PreparedGeometryUnavailable:
            return False
        check_operation_cancelled()
        project.source_file = candidate.source_file
        return True

    def _load_or_prepare_capture(
        self,
        project: Project,
        progress: ProgressReporter | None,
        *,
        load_source: Callable[[str], Project] | None = None,
        recoverable_states: frozenset[PreparedState] | None = None,
    ) -> GeometryEnginePort:
        with project_resource_scope(self.prepared_store, project.id):
            return self._prepare_owned_capture(
                project,
                progress,
                load_source=load_source,
                recoverable_states=recoverable_states,
            )

    def _prepare_owned_capture(
        self,
        project: Project,
        progress: ProgressReporter | None,
        *,
        load_source: Callable[[str], Project] | None = None,
        recoverable_states: frozenset[PreparedState] | None = None,
    ) -> GeometryEnginePort:
        # Отделяем новый рецепт от сохранённого проекта до успешной подготовки.
        # Обычное чтение никогда не выбирает новые границы и не запускает Core.
        candidate = self._capture_candidate(project)
        try:
            engine = self.load_local(candidate, progress)
        except PreparedGeometryUnavailable as error:
            if recoverable_states is not None and error.state not in recoverable_states:
                raise
            if self.prepared_store is None:
                raise
            if progress:
                progress(WorkProgress("Проверяем сохранённые файлы чертежа"))
            preparation_project = (
                self._load_preparation_source(candidate, load_source)
                if load_source is not None
                else candidate
            )
            live = self.live_engine(preparation_project)
            if not isinstance(live, LiveNativeGeometryEngine):
                raise ValueError("Для подготовки требуется текущий захват AutoCAD")
            try:
                snapshot = prepare_snapshot(live, preparation_project, progress)
            finally:
                # Успех, ошибка и отмена одинаково завершают владение временным
                # каталогом захвата. Очистка не должна прерываться проверкой отмены.
                with self._lock:
                    current = self._engines.get(project.id)
                    if current is not None and current[1] is live:
                        self._engines.pop(project.id)
                del current, live, preparation_project
            if progress:
                progress(WorkProgress("Сохраняем расчётную геометрию"))
            self.prepared_store.save(snapshot)
            # Освобождаем полный временный снимок после устойчивой записи. Дальше
            # локальные проверки используют тот же проверенный WKB-ресурс, что и
            # при повторном открытии, без долгоживущего графа координат.
            del snapshot
            source = candidate.source_file
            if source is None or source.native_session is None:
                raise PreparedGeometryUnavailable("unsupported")
            retained = self._load_snapshot(candidate, source.native_session)
            engine = self._cache_snapshot(candidate, retained, progress)
        project.source_file = candidate.source_file
        return engine

    def prepared_state(self, project: Project) -> PreparedAvailability:
        source = project.source_file
        session = source.native_session if source else None
        if session is None:
            return (
                "unsupported"
                if isinstance(self.compatibility, BoundLiveGeometry)
                and self.compatibility.binding.project_id == project.id
                else "not_required"
            )
        if self.prepared_store is None:
            return "missing"
        try:
            return self.prepared_store.state(project, session)
        except ValueError:
            return "stale"

    def workspace_state(self, project: Project) -> PreparedAvailability:
        """Проверять локальную целостность при открытии, сохраняя дешёвый обход
        каталога.
        """
        try:
            self.load_local(project)
        except PreparedGeometryUnavailable as error:
            return error.state
        return self.prepared_state(project)

    def preserve_local_geometry(self, project: Project) -> PreparedAvailability:
        """Граница явной записи метаданных, не используемая чтением проекта."""
        try:
            engine = self.load_local(project)
            if (
                isinstance(engine, PreparedGeometryEngine)
                and isinstance(engine.snapshot, PreparedSnapshot)
                and self.prepared_store
            ):
                self.prepared_store.preserve_compatible(engine.snapshot)
        except PreparedGeometryUnavailable as error:
            return error.state
        return self.prepared_state(project)

    def validation_input_basis(self, project: Project) -> CalculationInputBasis | None:
        if project.source_file is None or project.source_file.native_session is None:
            return None
        engine = self.load_local(project)
        if not isinstance(engine, PreparedGeometryEngine):
            return None
        return engine.validation_input_basis(project)

    @performance_span("native.prepared.lookup")
    def prepared_engine(self, project: Project) -> GeometryEnginePort | None:
        """Читать совпадающее сохранённое основание без открытия или запросов AutoCAD."""
        try:
            return self.load_local(project)
        except PreparedGeometryUnavailable:
            return None

    def calculate(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometrySnapshot:
        return self.load_local(project, progress=progress).calculate(project, progress)

    def supports_placement_projection(self, project: Project) -> bool:
        """Native-посадки читают расчётный снимок; совместимость требует CAD-граф."""
        return bool(project.source_file and project.source_file.native_session)

    def prepare_available_data(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometrySnapshot:
        """Продолжать явную подготовку после изменения ролей в том же CAD-архиве."""
        require_calculation_source(project.source_file)
        if not pending_native_layer_mapping(project):
            raise PreparedGeometryUnavailable("unsupported")
        require_confirmed_site_boundary(project)
        try:
            self.load_local(project, progress=progress)
        except PreparedGeometryUnavailable as error:
            source = project.source_file
            session = source.native_session if source else None
            if (
                error.state not in {"missing", "stale"}
                or session is None
                or session.plugin_version not in ARCHIVED_PLUGIN_VERSIONS
            ):
                raise
            # Только явная операция восстанавливает актуальное основание. Ошибка
            # целостности не разрешает подменить снимок или подтвердить неизвестные роли.
            self._load_or_prepare_capture(
                project, progress, recoverable_states=frozenset({"missing", "stale"})
            )
        return self.calculate_available_data(project, progress)

    def calculate_available_data(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometrySnapshot:
        """Нерешённые native-роли остаются неизвестными в проверенном локальном снимке.

        Отсутствие или повреждение основания не запускает AutoCAD. Безадресные
        пропуски сохраняются в покрытии; этот маршрут не подтверждает полноту CAD.
        """
        require_calculation_source(project.source_file)
        if not pending_native_layer_mapping(project):
            raise PreparedGeometryUnavailable("unsupported")
        selected = require_confirmed_site_boundary(project)
        engine = self.load_local(project, progress=progress)
        if not isinstance(engine, PreparedGeometryEngine):
            raise PreparedGeometryUnavailable("unsupported")
        engine.assert_current(project)
        usable = {
            record.item.layer
            for record in engine.snapshot.records
            if record.item.layer in selected
            and not record.reason
            and any(
                area.geom_type in {"Polygon", "MultiPolygon"}
                and area.buffer(-record.tolerance_m).area > 0
                for area in engine.projection.get(record.item).geometries
            )
        }
        if invalid := selected - usable:
            raise DomainFailure(
                FailureReason.LIVE_BOUNDARY_UNUSABLE, {"layer_names": sorted(invalid)}
            )
        return engine.calculate(project, progress)

    def assert_current(self, project: Project) -> None:
        method = getattr(self.load_local(project), "assert_current", None)
        if method is not None:
            method(project)

    @contextmanager
    def calculation_scope(self, project: Project) -> Iterator[None]:
        """Удерживать одно проверенное основание на ограниченный шаг поиска."""
        engine = self.load_local(project)
        method = getattr(type(engine), "calculation_scope", None)
        key = getattr(engine, "_geometry_key", None)
        source_scope = (
            self.prepared_store.read_scope(key)
            if self.prepared_store is not None and isinstance(key, str)
            else nullcontext()
        )
        with source_scope:
            if method is None:
                yield
            else:
                with method(engine, project):
                    yield

    def validation_independent_of_planting_zones(self, project: Project) -> bool:
        engine = self.load_local(project)
        method = getattr(type(engine), "validation_independent_of_planting_zones", None)
        return bool(method(engine, project)) if callable(method) else False

    def search_trace_assumption_codes(
        self, project: Project, plant_kinds: frozenset[str]
    ) -> frozenset[str] | None:
        """Дать верхнюю границу кодов лишь провайдерам с явным таким контрактом."""
        engine = self.load_local(project)
        method = getattr(type(engine), "search_trace_assumption_codes", None)
        return method(engine, project, plant_kinds) if method is not None else None

    def prepare_positions(
        self, project: Project, points: list[tuple[float, float]], **kwargs: Any
    ) -> None:
        method = getattr(self.load_local(project), "prepare_positions", None)
        if method is not None:
            method(project, points, **kwargs)

    def metadata_source_coverage(self, project: Project) -> GeometryCoverage | None:
        """Паспорт метаданных не загружает снимок и не строит новые проекции."""
        return self.source_coverage(project, metadata_only=True)

    def source_coverage(
        self, project: Project, *, metadata_only: bool = False
    ) -> GeometryCoverage | None:
        with project_resource_scope(self.prepared_store, project.id):
            return self._source_coverage(project, metadata_only=metadata_only)

    def _source_coverage(
        self, project: Project, *, metadata_only: bool = False
    ) -> GeometryCoverage | None:
        source = project.source_file
        session = source.native_session if source else None
        if session is None:
            if metadata_only:
                return None
            method = getattr(self.load_local(project), "source_coverage", None)
            return method(project) if method is not None else None
        assert source is not None
        if self.prepared_store is None:
            raise PreparedGeometryUnavailable("missing")
        key = preparation_key(project, session)
        with cancellable_lock(self._lock):
            check_operation_cancelled()
            state = self.prepared_store.state(project, session)
            if metadata_only and state != "available":
                raise PreparedGeometryUnavailable(state)
            fingerprint = self.prepared_store.verified_fingerprint(key)
            coverage_fingerprint = fingerprint or (
                self._coverage_cache.verified_fingerprint(key)
                if self._coverage_cache
                else None
            )
            summary = self._coverage_reads.get(project.id)
            if (
                state == "available"
                and summary is not None
                and summary.identity
                == coverage_identity(project, summary.linear_layers)
                and summary.coverage.source_sha256 == source.content_sha256
                and summary.coverage.prepared_key == key
            ):
                if summary.fingerprint == coverage_fingerprint:
                    self._coverage_reads.move_to_end(project.id)
                    return summary.coverage.model_copy(deep=True)
                if metadata_only:
                    raise PreparedGeometryUnavailable("stale")
            self._coverage_reads.pop(project.id, None)
            if (
                metadata_only
                and summary is not None
                and (
                    summary.identity
                    != coverage_identity(project, summary.linear_layers)
                )
            ):
                return None
            cached = self._prepared.get(project.id)
            matching = cached is not None and cached._geometry_key == key
            if (
                matching
                and cached is not None
                and cached._storage_fingerprint != fingerprint
            ):
                if metadata_only:
                    raise PreparedGeometryUnavailable("stale")
                # Изменившийся файл не должен снова подтвердить старый движок
                # после повторной проверки байтов временным чтением паспорта.
                self._prepared.pop(project.id, None)
                cached = None
                matching = False
            if metadata_only:
                if not matching or cached is None:
                    return None
                if preparation_basis(
                    project, session, cached.linear_layers
                ) != cached._binding or not _same_coverage_counts(cached, project):
                    return None
                cached.assert_current(project)
                return (
                    cached._coverage.model_copy(deep=True)
                    if cached._coverage is not None
                    else None
                )
            try:
                if not matching and state == "available" and self._coverage_cache:
                    saved = self._coverage_cache.read(project, key)
                    if saved is not None:
                        coverage, observed, linear_layers = saved
                        return self._remember_coverage(
                            project, coverage, observed, linear_layers
                        )
                return self._read_engine_coverage(
                    project, key, cached if matching else None
                )
            except PreparedGeometryUnavailable as error:
                self.prepared_store.record_failure(key, error.state)
                raise

    def _remember_coverage(
        self,
        project: Project,
        coverage: GeometryCoverage,
        fingerprint: FileFingerprint,
        linear_layers: frozenset[str],
    ) -> GeometryCoverage:
        check_operation_cancelled()
        self._coverage_reads[project.id] = _CoverageRead(
            coverage_identity(project, linear_layers),
            fingerprint,
            linear_layers,
            coverage.model_copy(deep=True),
        )
        self._coverage_reads.move_to_end(project.id)
        while len(self._coverage_reads) > MAX_CACHED_COVERAGE_PROJECTS:
            self._coverage_reads.popitem(last=False)
        return coverage

    def _read_engine_coverage(
        self,
        project: Project,
        key: str,
        cached: PreparedGeometryEngine | None,
    ) -> GeometryCoverage:
        """Первое чтение сохраняет результат полной проверки, не удерживая её граф."""
        assert self.prepared_store is not None
        assert (
            project.source_file is not None
            and project.source_file.native_session is not None
        )
        if cached is not None:
            engine = cached
            engine.assert_current(project)
        else:
            snapshot = self._load_snapshot(project, project.source_file.native_session)
            engine = self._validated_snapshot_engine(project, snapshot)
        fingerprint = self.prepared_store.verified_fingerprint(key)
        identity = coverage_identity(project, engine.linear_layers)
        with cancellable_lock(engine._lock):
            # Счётчик исходных объектов влияет на inventory_missing, но не меняет
            # физическую подготовку. Свежая сводка не наследует прежний счётчик.
            if not _same_coverage_counts(engine, project):
                engine._coverage = None
            coverage = engine.source_coverage(project)
        check_operation_cancelled()
        if (
            fingerprint is None
            or self.prepared_store.verified_fingerprint(key) != fingerprint
        ):
            raise PreparedGeometryUnavailable("stale")
        if self._coverage_cache:
            self._coverage_cache.save(
                project, key, coverage, engine.linear_layers, fingerprint, identity
            )
        if self.prepared_store.verified_fingerprint(
            key
        ) != fingerprint or identity != coverage_identity(
            project, engine.linear_layers
        ):
            raise PreparedGeometryUnavailable("stale")
        return self._remember_coverage(
            project, coverage, fingerprint, engine.linear_layers
        )

    def explain_position(self, project: Project, *args: Any) -> GeometryEvidence | None:
        method = getattr(self.load_local(project), "explain_position", None)
        return method(project, *args) if method is not None else None

    def discard(self, project_id: str) -> None:
        with self._lock:
            self._engines.pop(project_id, None)
            self._prepared.pop(project_id, None)
            self._coverage_reads.pop(project_id, None)
        discard = getattr(type(self.compatibility), "discard", None)
        if discard is not None:
            discard(self.compatibility, project_id)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def invoke(project: Project, *args: Any, **kwargs: Any) -> Any:
            # Редактирование и просмотр CAD требуют AutoCAD; посадки используют локальный снимок.
            engine = (
                self.live_engine(project)
                if name in {"review_area_group", "native_face_review"}
                else self.engine(project)
            )
            method = getattr(engine, name, None)
            if method is None and name in {
                "prepare_positions",
                "assert_current",
                "explain_position",
                "source_coverage",
            }:
                return None
            if method is None:
                raise ValueError("Для действия требуется подключённый сеанс AutoCAD")
            return method(project, *args, **kwargs)

        return invoke

    def validate_zones(
        self, project: Project, zones: list[PlantingZoneAssignment]
    ) -> None:
        if project.source_file and project.source_file.native_session:
            return validate_planting_zones(
                project, zones, changed_only=True, check_source_boundary=False
            )
        if self.compatibility_zones:
            return self.compatibility_zones(project, zones)
        return validate_changed_planting_zones(project, zones)
