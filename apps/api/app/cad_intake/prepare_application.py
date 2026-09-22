from collections.abc import Callable

from app.cad_import.contracts import CadConversionError
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.prepare_admission import (
    require_empty_source_project,
    validate_prepared_intake,
)
from app.cad_intake.prepare_contracts import (
    CadDrawingSnapshotSelection,
    CadPrepareRecord,
    CadPrepareRequest,
)
from app.cad_intake.prepare_ports import CadProjectPreparation
from app.cad_intake.snapshot_source import discover_adjacent_cad_snapshot
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
        with self.lifecycle.lock:
            project = self.lifecycle.repository.get(project_id, lightweight=True)
            require_empty_source_project(project)
            intake = self.lifecycle.get(project_id, request.intake_operation_id)
            passport = (
                intake.cad_intake.passport if intake.cad_intake is not None else None
            )
            if passport is None:
                raise ValueError("Паспорт CAD-комплекта отсутствует")
            self.config.require_enabled(passport.root_id)
            if request.cad_snapshot is None:
                root = self.config.root(passport.root_id).path
                discovered = discover_adjacent_cad_snapshot(root, passport.entry)
                if discovered is not None:
                    request = request.model_copy(
                        update={"cad_snapshot": discovered}
                    )
            entries = passport.entries or [passport.entry]
            selected = {
                item.drawing_path: item for item in request.additional_snapshots
            }
            for drawing_path in entries:
                if drawing_path in request.opening_review.skipped_drawings:
                    continue
                if drawing_path == passport.entry or drawing_path in selected:
                    continue
                discovered = discover_adjacent_cad_snapshot(
                    self.config.root(passport.root_id).path,
                    drawing_path,
                )
                if discovered is not None:
                    selected[drawing_path] = CadDrawingSnapshotSelection(
                        drawing_path=drawing_path,
                        snapshot=discovered,
                    )
            if list(selected.values()) != request.additional_snapshots:
                request = request.model_copy(
                    update={"additional_snapshots": list(selected.values())}
                )
            validate_prepared_intake(
                intake,
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
