from collections.abc import Callable
from datetime import datetime
from threading import RLock

from app.dxf_import.admission import (
    require_calculation_source,
    require_confirmed_layer_mapping,
)
from app.geometry.contracts import GeometrySnapshot
from app.geometry.ports import GeometryEnginePort
from app.history.ports import ProjectHistoryResetPort
from app.operations.contracts import (
    OperationKind,
    OperationStatus,
    ProjectOperation,
)
from app.operations.lifecycle import OperationLifecycle
from app.operations.ports import OperationRepository
from app.operations.progress import OperationCancelled
from app.planting_zones.domain import attach_planting_zone_features
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project, ProjectStatus
from app.projects.ports import ProjectSnapshotRepository
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
    ) -> None:
        self.repository = repository
        self.operation_repository = operation_repository
        self.geometry = geometry
        self.validation = validation
        self._operation_commit_lock = commit_lock
        self._discard_spatial_indexes = invalidate_spatial
        self._now = now
        self._new_id = new_id
        self.history = history
        self.lifecycle = OperationLifecycle(
            repository, operation_repository, commit_lock, now, new_id
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

    def start_geometry_operation(self, project_id: str) -> ProjectOperation:
        # The database guards multi-process deployments; this lock covers the
        # in-process read/create gap and keeps a double click idempotent even
        # with the lightweight in-memory adapter used in tests.
        with self._operation_commit_lock:
            project = self.repository.get(project_id)
            require_calculation_source(project.source_file)
            require_confirmed_layer_mapping(project)
            if project.plan is not None and project.map_ready and project.source_review is None:
                raise ValueError(
                    "Нельзя пересчитывать карту после открытия ручной схемы. Создайте новый проект."
                )
            return self.lifecycle.start(project_id, OperationKind.CALCULATE_GEOMETRY)

    def run_geometry_operation(self, operation_id: str) -> None:
        operation = self.lifecycle.claim(operation_id, "Подготавливаем геометрию слоёв")
        if operation is None:
            return
        version_token = set_expected_project_version(
            str(operation.project_state_version)
        )
        try:
            self.lifecycle.check_cancelled(operation_id)
            self.lifecycle.update(
                operation_id,
                status=OperationStatus.RUNNING,
                progress=1,
                stage="Подготавливаем геометрию слоёв",
            )
            # ``calculate`` makes its own isolated copy of the source
            # features before annotating their mapped role. A second deep
            # copy here would duplicate an entire 50 MB DXF snapshot before
            # the calculation has even begun. Keep the Project wrapper
            # detached, but share immutable source payload until the engine
            # takes the one copy it actually needs.
            project = self.repository.get(operation.project_id).model_copy(deep=False)
            require_calculation_source(project.source_file)
            require_confirmed_layer_mapping(project)
            release_source_after = project.plan is not None
            if project.source_review is not None and project.source_geometry is None:
                if project.geometry is None:
                    raise ValueError("Исходная карта недоступна для расчёта")
                project.source_geometry = project.geometry.model_copy(update={
                    "feature_collection": {
                        "type": "FeatureCollection",
                        "features": [feature for feature in project.geometry.feature_collection.get("features", [])
                                     if feature.get("properties", {}).get("source_layer")],
                    }
                })
            geometry = self.geometry.calculate(
                project,
                lambda update: self.lifecycle.report(operation_id, update, 2, 96),
            )
            with self._operation_commit_lock:
                self.lifecycle.check_cancelled(operation_id)
                self.lifecycle.update(
                    operation_id,
                    status=OperationStatus.RUNNING,
                    progress=99,
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
