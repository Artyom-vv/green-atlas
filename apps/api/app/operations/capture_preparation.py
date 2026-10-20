"""Явная подготовка сохранённого CAD-захвата без подтверждения смысловых назначений
расчётных объектов.
"""

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass

from app.operations.contracts import OperationKind, OperationStatus, ProjectOperation
from app.operations.lifecycle import OperationLifecycle
from app.operations.progress import (
    OperationCancelled,
    ProgressReporter,
    WorkProgress,
    operation_cancellation,
)
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project
from app.projects.ports import ProjectSnapshotRepository
from app.projects.resource_scope import project_resource_scope
from app.shared.failures import DomainFailure, FailureReason


@dataclass(frozen=True)
class OwnedCapturePreparation:
    """Подготовщик сам владеет полным чтением и принимает только метаданные."""

    run: Callable[[Project, ProgressReporter | None], None]


CapturePreparationCallback = (
    Callable[[Project, ProgressReporter | None], None] | OwnedCapturePreparation
)


class CapturePreparationApplication:
    def __init__(
        self,
        repository: ProjectSnapshotRepository,
        lifecycle: OperationLifecycle,
        prepare: CapturePreparationCallback | None,
        reuse_prepared_capture: Callable[[Project, ProgressReporter | None], bool]
        | None = None,
        discard_display_projection: Callable[[str], None] | None = None,
    ) -> None:
        self.repository = repository
        self.lifecycle = lifecycle
        self.prepare = prepare
        self.reuse_prepared_capture = reuse_prepared_capture
        self.discard_display_projection = discard_display_projection

    def start(self, project_id: str) -> ProjectOperation:
        project = self.repository.get(project_id, lightweight=True)
        if self.prepare is None or not (
            project.source_file and project.source_file.native_session
        ):
            raise DomainFailure(FailureReason.CAPTURE_REQUIRED)
        return self.lifecycle.start(project_id, OperationKind.PREPARE_CAD_CAPTURE)

    def run(self, operation_id: str) -> None:
        operation = self.lifecycle.claim(operation_id, "Подготавливаем CAD-архив")
        if operation is None:
            return
        token = set_expected_project_version(str(operation.project_state_version))
        resources = ExitStack()
        try:
            resources.enter_context(
                project_resource_scope(self.repository, operation.project_id)
            )
            self.lifecycle.check_cancelled(operation_id)
            project = self.repository.get(operation.project_id, lightweight=True)
            previous_recipe = (
                project.source_file.preparation_recipe if project.source_file else None
            )
            if self.prepare is None:
                raise DomainFailure(FailureReason.CAPTURE_PREPARATION_UNAVAILABLE)
            with operation_cancellation(
                lambda: self.lifecycle.check_cancelled(operation_id)
            ):

                def report(update: WorkProgress) -> None:
                    self.lifecycle.report(operation_id, update, 1, 98)

                reused = (
                    self.reuse_prepared_capture(project, report)
                    if self.reuse_prepared_capture is not None
                    else False
                )
                if not reused:
                    if self.discard_display_projection is not None:
                        self.lifecycle.check_cancelled(operation_id)
                        current = self.repository.get(
                            operation.project_id, lightweight=True
                        )
                        if current.state_version != operation.project_state_version:
                            raise ProjectVersionConflict(
                                current.id,
                                operation.project_state_version,
                                current.state_version,
                            )
                        # Индекс отображения воспроизводим из того же сохранённого
                        # снимка. Его не удерживаем рядом с новым полным чтением
                        # и Core; расчётные движки и данные проекта остаются на месте.
                        self.discard_display_projection(operation.project_id)
                    if isinstance(self.prepare, OwnedCapturePreparation):
                        # Полный исходник принадлежит подготовщику и освобождается
                        # до записи снимка. Операция сохраняет только рецепт и CAS.
                        project = project.model_copy(
                            update={"geometry": None, "source_geometry": None}
                        )
                        self.prepare.run(project, report)
                    else:
                        # Прежний адаптер получает полный Project, как и раньше.
                        project = self.repository.get(operation.project_id)
                        self.prepare(project, report)
            with self.lifecycle.lock:
                self.lifecycle.check_cancelled(operation_id)
                current = self.repository.get(operation.project_id, lightweight=True)
                if current.state_version != operation.project_state_version:
                    raise ProjectVersionConflict(
                        current.id,
                        operation.project_state_version,
                        current.state_version,
                    )
                # Рецепт записывается с тем же optimistic-lock, что и проект.
                # Отмена или конфликт оставляют прежнее основание и план доступными.
                if (
                    project.source_file
                    and project.source_file.preparation_recipe != previous_recipe
                ):
                    if self.reuse_prepared_capture is not None or isinstance(
                        self.prepare, OwnedCapturePreparation
                    ):
                        self.repository.save_preparation_recipe(project)
                    else:
                        self.repository.save(project)
                self.lifecycle.update(
                    operation_id,
                    status=OperationStatus.COMPLETED,
                    progress=100,
                    stage="CAD-архив подготовлен. Назначения слоёв не изменены",
                )
        except OperationCancelled:
            self.lifecycle.complete_cancellation(operation_id)
        except ProjectVersionConflict as error:
            self.lifecycle.fail(
                operation_id, "Проект изменился", "PROJECT_VERSION_CONFLICT", error
            )
        except Exception as error:
            self.lifecycle.fail(
                operation_id, "Подготовка остановлена", "CAD_PREPARATION_FAILED", error
            )
        finally:
            reset_expected_project_version(token)
            resources.close()
