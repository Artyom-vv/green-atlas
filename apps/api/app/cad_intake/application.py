from collections.abc import Callable
from typing import Protocol

from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.contracts import (
    CadIntakeRecord,
    CadIntakeRequest,
    CadPackagePassport,
)
from app.cad_intake.paths import CadDiscovery
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.operations.lifecycle import OperationLifecycle
from app.operations.progress import OperationCancelled, WorkProgress
from app.projects.concurrency import ProjectVersionConflict


class PackageInspection(Protocol):
    def inspect(
        self,
        operation_id: str,
        request: CadIntakeRequest,
        check_cancelled: Callable[[], None],
        report_progress: Callable[[WorkProgress], None],
    ) -> CadPackagePassport: ...


class CadIntakeApplication:
    def __init__(
        self,
        config: CadIntakeConfig,
        lifecycle: OperationLifecycle,
        inspector: PackageInspection,
    ) -> None:
        self.config = config
        self.lifecycle = lifecycle
        self.inspector = inspector
        self.discovery = CadDiscovery(config)

    def start(self, project_id: str, request: CadIntakeRequest) -> ProjectOperation:
        self.config.require_enabled(request.root_id)
        self.discovery.resolve(request.root_id, request.entry, drawing=True)
        for entry in request.additional_entries:
            self.discovery.resolve(request.root_id, entry.path, drawing=True)
        for override in request.overrides:
            self.discovery.resolve(request.root_id, override.owner, drawing=True)
            self.discovery.resolve(request.root_id, override.target, drawing=True)
        with self.lifecycle.lock:
            project = self.lifecycle.repository.get(project_id, lightweight=True)
            if project.plan is not None:
                raise ValueError(
                    "Для нового CAD-комплекта создайте проект без готового плана"
                )
            operation = self.lifecycle.start(
                project_id,
                OperationKind.INSPECT_CAD_PACKAGE,
                cad_intake=CadIntakeRecord(request=request),
            )
            if operation.cad_intake is None or operation.cad_intake.request != request:
                raise ValueError("В проекте уже проверяется другой комплект CAD")
            return operation

    def run(self, operation_id: str) -> None:
        operation = self.lifecycle.claim(
            operation_id, "Ожидаем свободный процесс проверки CAD"
        )
        if operation is None:
            return
        try:
            if (
                operation.kind != OperationKind.INSPECT_CAD_PACKAGE
                or operation.cad_intake is None
            ):
                raise ValueError("Операция не содержит CAD-задание")
            self._check_project(operation)
            passport = self.inspector.inspect(
                operation.id,
                operation.cad_intake.request,
                lambda: self.lifecycle.check_cancelled(operation.id),
                lambda update: self.lifecycle.report(operation.id, update, 0, 100),
            )
            with self.lifecycle.lock:
                self.lifecycle.check_cancelled(operation.id)
                self._check_project(operation)
                self.lifecycle.update(
                    operation.id,
                    status=OperationStatus.COMPLETED,
                    progress=100,
                    stage="Паспорт комплекта готов; требуется проверка исходных данных",
                    cad_intake=CadIntakeRecord(
                        request=operation.cad_intake.request, passport=passport
                    ),
                )
        except OperationCancelled:
            self.lifecycle.complete_cancellation(operation.id)
        except ProjectVersionConflict as error:
            self.lifecycle.fail(
                operation.id,
                "Паспорт не принят: проект изменился",
                "PROJECT_VERSION_CONFLICT",
                error,
            )
        except Exception:
            # Native exceptions can contain private filesystem paths; logs stay in storage.
            self.lifecycle.fail(
                operation.id,
                "Проверка CAD остановлена",
                "CAD_INTAKE_FAILED",
                ValueError(
                    "Не удалось проверить комплект. Повторите проверку исходных данных; диагностика сохранена на сервере"
                ),
            )

    def _check_project(self, operation: ProjectOperation) -> None:
        project = self.lifecycle.repository.get(operation.project_id, lightweight=True)
        if project.state_version != operation.project_state_version:
            raise ProjectVersionConflict(
                project.id, operation.project_state_version, project.state_version
            )
        if project.plan is not None:
            raise ValueError("Проект уже содержит готовый план")
