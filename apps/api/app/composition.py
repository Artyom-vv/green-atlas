"""Сборка локального API из адаптеров и сценариев; runtime создаётся на процесс и
закрывает принадлежащие ему ресурсы.
"""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from threading import RLock

from app.application import ProjectApplication
from app.cad_intake.assets.application import CadAssetApplication
from app.cad_intake.inspection.config import CadIntakeConfig
from app.dxf_import.legacy.reader import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.exporting.cad_writer import AutoCadReleaseWriter
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.ports import GeometryEnginePort
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.layer_recognition.providers import configured_provider
from app.layer_recognition.service import LayerRecognitionService
from app.location.application import LocationApplication
from app.native_query.capture.capture_store import NativeCaptureStore
from app.native_query.live.live_runtime import configured_live_engine
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.prepared_snapshot import PreparedStore
from app.native_query.project_runtime import ProjectLiveGeometry
from app.native_query.transport.worker_config import worker_config
from app.operations.adapters import SqliteOperationRepository
from app.operations.capture_preparation import OwnedCapturePreparation
from app.planning.patterns.generators import ShapelyCandidateGenerator
from app.planting_zones.ports import ZoneValidator
from app.projects.adapters import SqliteProjectRepository
from app.projects.maintenance_worker import StorageMaintenanceWorker
from app.rendering.application import SceneRendering
from app.shared.error_messages import public_failure_message
from app.shared.identity import random_id, utc_now
from app.validation.adapters import RuleBasedPlanValidator

_LOGGER = logging.getLogger(__name__)


def default_database_path() -> str:
    return os.environ.get(
        "GREEN_ATLAS_DB_PATH",
        str(Path(__file__).resolve().parents[1] / "data" / "green-atlas.sqlite3"),
    )


@dataclass
class Runtime:
    database_path: str
    application: ProjectApplication
    project_repository: SqliteProjectRepository
    operation_repository: SqliteOperationRepository
    cad_assets: CadAssetApplication

    _closed: bool = False
    _resource_lock: RLock = field(default_factory=RLock, init=False)
    _layer_recognition: LayerRecognitionService | None = field(default=None, init=False)
    _scene_rendering: SceneRendering | None = field(default=None, init=False)
    _location: LocationApplication | None = field(default=None, init=False)
    _storage_worker: StorageMaintenanceWorker | None = field(default=None, init=False)

    def cancel_project_resources(self, project_id: str) -> None:
        with self._resource_lock:
            if self._scene_rendering is not None:
                self._scene_rendering.cancel_project(project_id)
        if self._storage_worker is not None:
            self._storage_worker.request()

    @property
    def location(self) -> LocationApplication:
        with self._resource_lock:
            if self._closed:
                raise RuntimeError("Runtime is closed")
            if self._location is None:
                self._location = LocationApplication(
                    self.project_repository,
                    Path(self.database_path + ".renders"),
                    capture_store=NativeCaptureStore(
                        Path(self.database_path + ".cad-archives")
                    ),
                    revision_guard=self.project_repository.revision_transaction,
                )
            return self._location

    @property
    def scene_rendering(self) -> SceneRendering:
        with self._resource_lock:
            if self._closed:
                raise RuntimeError("Runtime is closed")
            if self._scene_rendering is None:
                self._scene_rendering = SceneRendering(
                    self.project_repository,
                    Path(self.database_path + ".renders"),
                    display_features=getattr(
                        self.application.spatial.geometry_query,
                        "cached_scene_features",
                        None,
                    ),
                )
            return self._scene_rendering

    @property
    def layer_recognition(self) -> LayerRecognitionService:
        with self._resource_lock:
            if self._closed:
                raise RuntimeError("Runtime is closed")
            if self._layer_recognition is None:
                provider, error = None, None
                try:
                    provider = configured_provider()
                except ValueError as problem:
                    _LOGGER.exception("Layer recognition configuration failed")
                    error = public_failure_message(problem, "recognition_failed")
                self._layer_recognition = LayerRecognitionService(
                    Path(self.database_path + ".layer-recognition"),
                    provider,
                    error,
                    presets=Path(os.environ["GREEN_ATLAS_LAYER_RECOGNITION_PRESETS"])
                    if os.environ.get("GREEN_ATLAS_LAYER_RECOGNITION_PRESETS")
                    else None,
                )
            return self._layer_recognition

    def close(self) -> None:
        """Однократно закрыть ресурсы, принадлежащие runtime.

        SqliteProjectHistory разделяет соединение проекта; закрытие двух репозиториев
        закрывает все ресурсы composition root ровно один раз.
        """

        with self._resource_lock:
            if self._closed:
                return
            self._closed = True
        # Остановка worker не удерживает resource_lock: его idle-проверка
        # может читать runtime. Ошибка одного владельца не пропускает остальных.
        with ExitStack() as owned:
            owned.callback(self.project_repository.close)
            owned.callback(self.operation_repository.close)
            owned.callback(self.project_repository.resource_lifecycle.drain)
            if self._scene_rendering is not None:
                owned.callback(self._scene_rendering.close)
            if self._layer_recognition is not None:
                owned.callback(self._layer_recognition.close)
            if self._storage_worker is not None:
                owned.callback(self._storage_worker.close)


def create_runtime(
    database_path: str | os.PathLike[str],
    *,
    geometry_engine: GeometryEnginePort | None = None,
    zone_validator: ZoneValidator | None = None,
    recover_operations: bool = True,
) -> Runtime:
    """Собрать независимый граф приложения для одного пути базы."""

    path = (
        ":memory:"
        if str(database_path) == ":memory:"
        else str(Path(database_path).resolve())
    )
    cad_config = CadIntakeConfig.from_environment(path)
    project_repository = SqliteProjectRepository(path)
    project_repository.resource_lifecycle.defer_cleanup = True
    # Восстановление принадлежит запуску владельца API, а не подключению его worker.
    operation_repository = SqliteOperationRepository(path, recover=recover_operations)
    geometry = (
        geometry_engine if geometry_engine is not None else ShapelyGeometryEngine()
    )
    prepare_geometry = None
    prepare_capture = None
    reuse_prepared_capture = None
    prepared_status = None
    prepared_read_status = None
    preserve_local_geometry = None
    basis_reader = None
    if geometry_engine is None:
        geometry, native_zones = configured_live_engine(path, geometry)
        geometry = ProjectLiveGeometry(
            geometry,
            native_zones,
            prepared_store=PreparedStore(
                Path(str(path) + ".prepared-geometry"),
                database_path=Path(path),
                resource_lifecycle=project_repository.resource_lifecycle,
            ),
            archive_store=NativeCaptureStore(Path(path + ".cad-archives")),
            archive_config_factory=partial(
                worker_config, path, job_suffix=".cad-preparation-jobs"
            ),
        )
        prepare_geometry = geometry.prepare_current_capture
        prepare_capture = OwnedCapturePreparation(
            partial(geometry.prepare_capture_from_repository, project_repository.get)
        )
        reuse_prepared_capture = geometry.reuse_prepared_capture
        prepared_status = geometry.prepared_state
        prepared_read_status = geometry.workspace_state
        preserve_local_geometry = geometry.preserve_local_geometry
        basis_reader = geometry.validation_input_basis
        if zone_validator is None:
            zone_validator = geometry.validate_zones
    elif isinstance(geometry_engine, PreparedGeometryEngine):
        prepared_status = geometry_engine.prepared_state
        prepared_read_status = geometry_engine.prepared_state
        basis_reader = geometry_engine.validation_input_basis
    history = SqliteProjectHistory(project_repository)
    history.set_basis_reader(basis_reader)
    application = ProjectApplication(
        repository=project_repository,
        operation_repository=operation_repository,
        history=history,
        dxf_reader=EzdxfReader(),
        geometry=geometry,
        geometry_query=IndexedGeometryQuery(display_repository=project_repository),
        validator=RuleBasedPlanValidator(geometry=geometry),
        writer=DxfRoundTripWriter(),
        cad_writer=AutoCadReleaseWriter(path),
        candidate_generator=ShapelyCandidateGenerator(),
        zone_validator=zone_validator,
        now=utc_now,
        new_id=random_id,
        prepare_geometry=prepare_geometry,
        prepare_capture=prepare_capture,
        reuse_prepared_capture=reuse_prepared_capture,
        prepared_status=prepared_status,
        prepared_read_status=prepared_read_status,
        preserve_local_geometry=preserve_local_geometry,
        basis_reader=basis_reader,
    )
    runtime = Runtime(
        database_path=path,
        application=application,
        project_repository=project_repository,
        operation_repository=operation_repository,
        cad_assets=CadAssetApplication(cad_config, operation_repository),
    )
    application.projects.add_delete_listener(runtime.cancel_project_resources)
    if recover_operations:
        project_repository.resource_lifecycle.drain()
        runtime._storage_worker = StorageMaintenanceWorker(
            project_repository.storage_maintenance,
            idle=lambda: (
                not runtime._closed
                and not project_repository.resource_lifecycle.has_active_users
            ),
        )
        runtime._storage_worker.start_if_pending()
    return runtime


_runtime: Runtime | None = None
_runtime_lock = RLock()


def get_runtime() -> Runtime:
    """Получить runtime процесса, создаваемый при первом обращении."""

    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = create_runtime(default_database_path())
        return _runtime


def get_application() -> ProjectApplication:
    return get_runtime().application


def get_layer_recognition() -> LayerRecognitionService:
    return get_runtime().layer_recognition


def get_cad_assets() -> CadAssetApplication:
    return get_runtime().cad_assets


def get_scene_rendering() -> SceneRendering:
    return get_runtime().scene_rendering


def get_location() -> LocationApplication:
    return get_runtime().location


def get_database_path() -> str:
    return get_runtime().database_path


def close_runtime() -> None:
    """Закрыть и очистить runtime процесса при явном завершении сеанса."""

    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            try:
                _runtime.close()
            finally:
                _runtime = None
