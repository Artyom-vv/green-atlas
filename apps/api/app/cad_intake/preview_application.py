from collections.abc import Callable

from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.preview_admission import (
    require_empty_preview_project,
    validate_preview_intake,
)
from app.cad_intake.preview_contracts import CadPreviewRecord, CadPreviewRequest
from app.cad_intake.preview_ports import CadPreviewPreparation
from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.operations.lifecycle import OperationLifecycle
from app.operations.progress import OperationCancelled
from app.projects.concurrency import ProjectVersionConflict


class CadPreviewApplication:
    def __init__(
        self,
        config: CadIntakeConfig,
        lifecycle: OperationLifecycle,
        preparation: CadPreviewPreparation,
        invalidate_spatial: Callable[[str], None],
    ) -> None:
        self.config = config
        self.lifecycle = lifecycle
        self.preparation = preparation
        self.invalidate_spatial = invalidate_spatial

    def start(self, project_id: str, request: CadPreviewRequest) -> ProjectOperation:
        self.config.require_enabled()
        with self.lifecycle.lock:
            project = self.lifecycle.repository.get(project_id, lightweight=True)
            require_empty_preview_project(project)
            intake = self.lifecycle.get(project_id, request.intake_operation_id)
            validate_preview_intake(intake, project_id, request)
            operation = self.lifecycle.start(
                project_id,
                OperationKind.PREPARE_CAD_PREVIEW,
                cad_preview=CadPreviewRecord(request=request),
            )
            if (
                operation.cad_preview is None
                or operation.cad_preview.request != request
            ):
                raise ValueError(
                    "В проекте уже готовится другой предварительный фрагмент"
                )
            return operation

    def run(self, operation_id: str) -> None:
        operation = self.lifecycle.claim(
            operation_id, "Ожидаем свободный процесс подготовки CAD"
        )
        if operation is None:
            return
        try:
            if (
                operation.kind != OperationKind.PREPARE_CAD_PREVIEW
                or operation.cad_preview is None
            ):
                raise ValueError("Операция не содержит выбранной территории CAD")
            project = self.lifecycle.repository.get(
                operation.project_id, lightweight=True
            )
            if project.state_version != operation.project_state_version:
                raise ProjectVersionConflict(
                    project.id, operation.project_state_version, project.state_version
                )
            require_empty_preview_project(project)
            self.preparation.prepare(
                operation.id,
                operation.cad_preview.request,
                lambda: self.lifecycle.check_cancelled(
                    operation.id, allow_completed=True
                ),
                lambda update: self.lifecycle.report(operation.id, update, 0, 99),
            )
            if (
                self.lifecycle.operations.get(operation.id).status
                != OperationStatus.COMPLETED
            ):
                raise ValueError("Рабочий процесс не подтвердил публикацию карты")
        except OperationCancelled:
            self.lifecycle.complete_cancellation(operation.id)
        except ProjectVersionConflict as error:
            self.lifecycle.fail(
                operation.id,
                "Карта не открыта: проект изменился",
                "PROJECT_VERSION_CONFLICT",
                error,
            )
        except Exception:
            self.lifecycle.fail(
                operation.id,
                "Подготовка предварительной карты остановлена",
                "CAD_PREVIEW_FAILED",
                ValueError(
                    "Не удалось подготовить предварительную карту. Исходный проект сохранён; диагностика доступна на сервере"
                ),
            )
        finally:
            # A committed receipt wins a lost worker response, cancellation or
            # post-commit process failure. Lifecycle never overwrites terminal state.
            if (
                self.lifecycle.operations.get(operation.id).status
                == OperationStatus.COMPLETED
            ):
                self.invalidate_spatial(operation.project_id)
