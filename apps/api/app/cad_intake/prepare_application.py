from collections.abc import Callable

from app.cad_import.contracts import CadConversionError
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.prepare_admission import (
    require_empty_source_project,
    validate_prepared_intake,
)
from app.cad_intake.prepare_contracts import CadPrepareRecord, CadPrepareRequest
from app.cad_intake.prepare_ports import CadProjectPreparation
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.operations.lifecycle import OperationLifecycle
from app.operations.progress import OperationCancelled
from app.projects.concurrency import ProjectVersionConflict


class CadPrepareApplication:
    def __init__(
        self,
        config: CadIntakeConfig,
        lifecycle: OperationLifecycle,
        preparation: CadProjectPreparation,
        invalidate_spatial: Callable[[str], None],
    ) -> None:
        self.config = config
        self.lifecycle = lifecycle
        self.preparation = preparation
        self.invalidate_spatial = invalidate_spatial

    def start(self, project_id: str, request: CadPrepareRequest) -> ProjectOperation:
        self.config.require_enabled()
        with self.lifecycle.lock:
            project = self.lifecycle.repository.get(project_id, lightweight=True)
            require_empty_source_project(project)
            validate_prepared_intake(
                self.lifecycle.get(project_id, request.intake_operation_id),
                project_id,
                request,
            )
            operation = self.lifecycle.start(
                project_id,
                OperationKind.PREPARE_CAD_PROJECT,
                cad_prepare=CadPrepareRecord(request=request),
            )
            if (
                operation.cad_prepare is None
                or operation.cad_prepare.request != request
            ):
                raise ValueError("В проекте уже готовится другой исходник")
            return operation

    def run(self, operation_id: str) -> None:
        operation = self.lifecycle.claim(operation_id, "Ожидаем подготовку полного DXF")
        if operation is None:
            return
        try:
            if (
                operation.kind != OperationKind.PREPARE_CAD_PROJECT
                or operation.cad_prepare is None
            ):
                raise ValueError("Операция не содержит полного исходника")
            self.preparation.prepare(
                operation.id,
                operation.cad_prepare.request,
                lambda: self.lifecycle.check_cancelled(
                    operation.id, allow_completed=True
                ),
                lambda update: self.lifecycle.report(operation.id, update, 0, 99),
            )
            if (
                self.lifecycle.operations.get(operation.id).status
                != OperationStatus.COMPLETED
            ):
                raise ValueError("Публикация проекта не подтверждена")
        except OperationCancelled:
            self.lifecycle.complete_cancellation(operation.id)
        except ProjectVersionConflict as error:
            self.lifecycle.fail(
                operation.id, "Проект изменился", "PROJECT_VERSION_CONFLICT", error
            )
        except CadConversionError as error:
            self.lifecycle.fail(
                operation.id,
                "Подготовка превысила ресурсный бюджет",
                "CAD_PREPARE_RESOURCE_LIMIT",
                error,
            )
        except Exception:
            self.lifecycle.fail(
                operation.id,
                "Полный исходник не опубликован",
                "CAD_PREPARE_FAILED",
                ValueError(
                    "Подготовка остановлена. Проект сохранён; причина доступна в журнале подготовки."
                ),
            )
        finally:
            if (
                self.lifecycle.operations.get(operation.id).status
                == OperationStatus.COMPLETED
            ):
                self.invalidate_spatial(operation.project_id)
