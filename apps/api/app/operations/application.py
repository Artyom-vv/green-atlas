"""Сценарии фонового расчёта геометрии с журналом прогресса, отменой и проверкой ревизии
перед сохранением.
"""

from collections.abc import Callable, Iterator
from contextlib import ExitStack
from datetime import datetime
from threading import Event, RLock

from app.dxf_import.contracts import ImportMode
from app.dxf_import.source.admission import (
    pending_native_layer_mapping,
    require_calculation_source,
    require_confirmed_layer_mapping,
    require_confirmed_site_boundary,
    require_usable_site_boundary,
)
from app.geometry.contracts import GeometrySnapshot
from app.geometry.ports import GeometryEnginePort
from app.history.ports import ProjectHistoryResetPort
from app.operations.capture_preparation import (
    CapturePreparationApplication,
    CapturePreparationCallback,
)
from app.operations.contracts import (
    OperationKind,
    OperationStatus,
    ProjectOperation,
)
from app.operations.lifecycle import OperationLifecycle
from app.operations.ports import OperationRepository
from app.operations.progress import (
    OperationCancelled,
    ProgressReporter,
    operation_cancellation,
)
from app.planting_zones.domain import attach_planting_zone_features
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project, ProjectStatus
from app.projects.ports import ProjectSnapshotRepository
from app.projects.resource_scope import project_resource_scope
from app.shared.failures import DomainFailure, FailureReason
from app.validation.application import PlanValidation


class GeometryOperationApplication:
    def __init__(
        self,
        *,
        repository: ProjectSnapshotRepository,
        operation_repository: OperationRepository,
        geometry: GeometryEnginePort,
        validation: PlanValidation,
        commit_lock: RLock,
        invalidate_spatial: Callable[[str], None],
        now: Callable[[], datetime],
        new_id: Callable[[], str],
        history: ProjectHistoryResetPort | None = None,
        prepare_geometry: Callable[[Project, ProgressReporter | None], GeometrySnapshot]
        | None = None,
        prepare_capture: CapturePreparationCallback | None = None,
        reuse_prepared_capture: Callable[[Project, ProgressReporter | None], bool]
        | None = None,
        discard_display_projection: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.operation_repository = operation_repository
        self.geometry = geometry
        self._prepare_geometry = prepare_geometry or geometry.calculate
        self.validation = validation
        self._operation_commit_lock = commit_lock
        self._discard_spatial_indexes = invalidate_spatial
        self._now = now
        self._new_id = new_id
        self.history = history
        self.lifecycle = OperationLifecycle(
            repository, operation_repository, commit_lock, now, new_id
        )
        self.capture_preparation = CapturePreparationApplication(
            repository,
            self.lifecycle,
            prepare_capture,
            reuse_prepared_capture,
            discard_display_projection,
        )

    def cancel_active(self, project_id: str) -> None:
        with self._operation_commit_lock:
            for kind in OperationKind:
                active = self.operation_repository.get_latest(project_id, kind)
                if active is not None and active.status in {
                    OperationStatus.QUEUED,
                    OperationStatus.RUNNING,
                    OperationStatus.CANCELLING,
                }:
                    self.lifecycle.complete_cancellation(active.id)

    @staticmethod
    def _assign_geometry(project: Project, geometry: GeometrySnapshot) -> None:
        project.geometry = geometry
        project.map_ready = True
        project.geometry_version += 1
        project.site_area_m2 = geometry.site_area_m2
        project.planning_area_m2 = geometry.planning_area_m2
        project.allowed_area_m2 = geometry.allowed_area_m2

    def get_operation(self, project_id: str, operation_id: str) -> ProjectOperation:
        return self.lifecycle.get(project_id, operation_id)

    def get_latest_operation(
        self, project_id: str, kind: OperationKind
    ) -> ProjectOperation | None:
        return self.lifecycle.latest(project_id, kind)

    def cancel_operation(self, project_id: str, operation_id: str) -> ProjectOperation:
        return self.lifecycle.cancel(project_id, operation_id)

    def watch_operation(
        self,
        project_id: str,
        operation_id: str,
        *,
        stop: Event,
    ) -> Iterator[ProjectOperation | None]:
        self.lifecycle.get(project_id, operation_id)
        return self.operation_repository.watch(operation_id, stop=stop)

    def cancel_operation_watch(self, stop: Event) -> None:
        self.operation_repository.cancel_watch(stop)

    def start_geometry_operation(self, project_id: str) -> ProjectOperation:
        project = self.repository.get(project_id, lightweight=True)
        require_calculation_source(project.source_file)
        available_data = self._uses_available_data(project)
        if available_data:
            require_confirmed_site_boundary(project)
        else:
            require_confirmed_layer_mapping(project)
            if project.import_status.mode == ImportMode.AUTOCAD_LIVE:
                # Старый допуск читает исходные поверхности до захвата commit lock.
                full_project = self.repository.get(project_id)
                if full_project.state_version != project.state_version:
                    raise ProjectVersionConflict(
                        project_id, project.state_version, full_project.state_version
                    )
                require_usable_site_boundary(full_project)
        # База защищает разные процессы; эта блокировка закрывает промежуток чтения
        # и создания внутри процесса и сохраняет идемпотентность двойного нажатия.
        with self._operation_commit_lock:
            current = self.repository.get(project_id, lightweight=True)
            if current.state_version != project.state_version:
                raise ProjectVersionConflict(
                    project_id, project.state_version, current.state_version
                )
            if (
                current.plan is not None
                and current.map_ready
                and current.source_review is None
            ):
                raise DomainFailure(FailureReason.GEOMETRY_RECALCULATION_UNAVAILABLE)
            return self.lifecycle.start(project_id, OperationKind.CALCULATE_GEOMETRY)

    def run_geometry_operation(self, operation_id: str) -> None:
        operation = self.lifecycle.claim(operation_id, "Подготавливаем геометрию слоёв")
        if operation is None:
            return
        version_token = set_expected_project_version(
            str(operation.project_state_version)
        )
        resources = ExitStack()
        try:
            resources.enter_context(
                project_resource_scope(self.repository, operation.project_id)
            )
            self.lifecycle.check_cancelled(operation_id)
            self.lifecycle.update(
                operation_id,
                status=OperationStatus.RUNNING,
                progress=0,
                progress_mode="indeterminate",
                stage="Загружаем исходные данные проекта",
            )
            # calculate сам копирует исходные объекты перед назначением ролей. Вторая
            # глубокая копия дублировала бы крупный снимок ещё до расчёта. Отделяем оболочку
            # Project, разделяя неизменяемый исходник до единственной нужной копии движка.
            project = self.repository.get(operation.project_id).model_copy(deep=False)
            require_calculation_source(project.source_file)
            available_data = self._uses_available_data(project)
            if not available_data:
                require_confirmed_layer_mapping(project)
                require_usable_site_boundary(project)
            release_source_after = project.plan is not None
            if project.source_review is not None and project.source_geometry is None:
                if project.geometry is None:
                    raise DomainFailure(FailureReason.CALCULATION_SOURCE_MISSING)
                project.source_geometry = project.geometry.model_copy(
                    update={
                        "feature_collection": {
                            "type": "FeatureCollection",
                            "features": [
                                feature
                                for feature in project.geometry.feature_collection.get(
                                    "features", []
                                )
                                if feature.get("properties", {}).get("source_layer")
                            ],
                        }
                    }
                )
            with operation_cancellation(
                lambda: self.lifecycle.check_cancelled(operation_id)
            ):
                calculate: Callable[
                    [Project, ProgressReporter | None], GeometrySnapshot
                ] = (
                    getattr(
                        self.geometry,
                        "prepare_available_data",
                        getattr(self.geometry, "calculate_available_data"),
                    )
                    if available_data
                    else self._prepare_geometry
                )
                geometry = calculate(
                    project,
                    lambda update: self.lifecycle.report(operation_id, update, 2, 96),
                )
            with self._operation_commit_lock:
                self.lifecycle.check_cancelled(operation_id)
                self.lifecycle.update(
                    operation_id,
                    status=OperationStatus.RUNNING,
                    progress=99,
                    progress_mode="indeterminate",
                    processed_items=None,
                    total_items=None,
                    progress_unit=None,
                    stage="Фиксируем проверенную геометрию",
                )
                self._assign_geometry(project, geometry)
                project.source_review = None
                attach_planting_zone_features(project)
                if project.plan is not None:
                    self.validation.refresh(
                        project, project.plan, increment_version=False
                    )
                    project.status = ProjectStatus.EDITING
                if release_source_after:
                    project.source_geometry = None
                self.repository.save(project)
                if self.history is not None:
                    self.history.clear(project.id)
                self._discard_spatial_indexes(project.id)
                self.lifecycle.update(
                    operation_id,
                    status=OperationStatus.COMPLETED,
                    progress=100,
                    stage="Карта подготовлена",
                )
        except OperationCancelled:
            self.lifecycle.complete_cancellation(operation_id)
        except ProjectVersionConflict as error:
            self.lifecycle.fail(
                operation_id, "Расчёт не записан", "PROJECT_VERSION_CONFLICT", error
            )
        except Exception as error:
            self.lifecycle.fail(
                operation_id, "Расчёт остановлен", "CALCULATION_FAILED", error
            )
        finally:
            reset_expected_project_version(version_token)
            resources.close()

    def _uses_available_data(self, project: Project) -> bool:
        return pending_native_layer_mapping(project) and callable(
            getattr(self.geometry, "calculate_available_data", None)
        )
