"""Shared journal transitions; work itself remains in scenario applications."""

from collections.abc import Callable
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Literal

from app.cad_intake.contracts import CadIntakeRecord
from app.cad_intake.preview_contracts import CadPreviewRecord
from app.operations.contracts import (
    OperationError,
    OperationKind,
    OperationStatus,
    ProjectOperation,
)
from app.operations.ports import OperationRepository
from app.operations.progress import OperationCancelled, WorkProgress
from app.projects.concurrency import assert_project_version
from app.projects.ports import ProjectSnapshotRepository

ACTIVE = {OperationStatus.QUEUED, OperationStatus.RUNNING, OperationStatus.CANCELLING}


class _Unset(Enum):
    VALUE = "unset"


class OperationLifecycle:
    def __init__(
        self,
        repository: ProjectSnapshotRepository,
        operations: OperationRepository,
        lock: RLock,
        now: Callable[[], datetime],
        new_id: Callable[[], str],
    ) -> None:
        self.repository = repository
        self.operations = operations
        self.lock = lock
        self.now = now
        self.new_id = new_id

    def start(
        self,
        project_id: str,
        kind: OperationKind,
        *,
        cad_intake: CadIntakeRecord | None = None,
        cad_preview: CadPreviewRecord | None = None,
    ) -> ProjectOperation:
        with self.lock:
            project = self.repository.get(project_id, lightweight=True)
            assert_project_version(project_id, project.state_version)
            latest = self.operations.get_latest(project_id, kind)
            if latest is not None and latest.status in ACTIVE:
                return latest
            retry = (
                latest.id
                if latest
                and latest.status
                in {
                    OperationStatus.CANCELLED,
                    OperationStatus.INTERRUPTED,
                    OperationStatus.FAILED,
                }
                else None
            )
            return self.operations.create(
                ProjectOperation(
                    id=self.new_id(),
                    project_id=project_id,
                    kind=kind,
                    created_at=self.now().isoformat(),
                    updated_at=self.now().isoformat(),
                    retry_of_operation_id=retry,
                    project_state_version=project.state_version,
                    cad_intake=cad_intake,
                    cad_preview=cad_preview,
                )
            )

    def get(self, project_id: str, operation_id: str) -> ProjectOperation:
        operation = self.operations.get(operation_id)
        if operation.project_id != project_id:
            raise KeyError("Операция не найдена")
        return operation

    def latest(self, project_id: str, kind: OperationKind) -> ProjectOperation | None:
        self.repository.get(project_id, lightweight=True)
        return self.operations.get_latest(project_id, kind)

    def claim(self, operation_id: str, stage: str) -> ProjectOperation | None:
        with self.lock:
            current = self.operations.get(operation_id)
            if current.status != OperationStatus.QUEUED:
                return None
            next_operation = current.model_copy(deep=True)
            next_operation.status = OperationStatus.RUNNING
            next_operation.started_at = self.now().isoformat()
            next_operation.updated_at = next_operation.started_at
            next_operation.stage = stage
            return self.operations.compare_and_save(current, next_operation)

    def check_cancelled(self, operation_id: str) -> None:
        if self.operations.get(operation_id).status not in {
            OperationStatus.QUEUED,
            OperationStatus.RUNNING,
        }:
            raise OperationCancelled("Операция больше не выполняется")

    def update(
        self,
        operation_id: str,
        *,
        status: OperationStatus,
        progress: int,
        stage: str,
        progress_mode: Literal["determinate", "indeterminate"] = "determinate",
        processed_items: int | None | _Unset = _Unset.VALUE,
        total_items: int | None | _Unset = _Unset.VALUE,
        progress_unit: str | None | _Unset = _Unset.VALUE,
        error: OperationError | None = None,
        cad_intake: CadIntakeRecord | None = None,
        cad_preview: CadPreviewRecord | None = None,
    ) -> ProjectOperation:
        with self.lock:
            while True:
                current = self.operations.get(operation_id)
                if current.status not in ACTIVE:
                    if status == OperationStatus.RUNNING:
                        raise OperationCancelled("Операция больше не выполняется")
                    return current
                if (
                    current.status == OperationStatus.CANCELLING
                    and status != OperationStatus.CANCELLED
                ):
                    raise OperationCancelled("Операция остановлена пользователем")
                operation = current.model_copy(deep=True)
                operation.status = status
                operation.progress = (
                    max(current.progress, progress)
                    if status == OperationStatus.RUNNING
                    else progress
                )
                operation.progress_mode = progress_mode
                operation.stage = stage
                if processed_items is not _Unset.VALUE:
                    operation.processed_items = processed_items
                if total_items is not _Unset.VALUE:
                    operation.total_items = total_items
                if progress_unit is not _Unset.VALUE:
                    operation.progress_unit = progress_unit
                if error is not None:
                    operation.error = error
                if cad_intake is not None:
                    operation.cad_intake = cad_intake
                if cad_preview is not None:
                    operation.cad_preview = cad_preview
                operation.updated_at = self.now().isoformat()
                if status == OperationStatus.RUNNING and operation.started_at is None:
                    operation.started_at = operation.updated_at
                if status not in ACTIVE:
                    operation.completed_at = operation.updated_at
                saved = self.operations.compare_and_save(current, operation)
                if saved is not None:
                    return saved

    def report(
        self, operation_id: str, update: WorkProgress, start: int, end: int
    ) -> None:
        self.check_cancelled(operation_id)
        current = self.operations.get(operation_id)
        fraction = update.fraction
        progress = (
            current.progress
            if fraction is None
            else min(end - 1, max(start, round(start + (end - start) * fraction)))
        )
        self.update(
            operation_id,
            status=OperationStatus.RUNNING,
            progress=progress,
            stage=update.stage,
            progress_mode="indeterminate" if fraction is None else "determinate",
            processed_items=update.processed,
            total_items=update.total,
            progress_unit=update.unit,
        )

    def complete_cancellation(self, operation_id: str) -> ProjectOperation:
        current = self.operations.get(operation_id)
        return self.update(
            operation_id,
            status=OperationStatus.CANCELLED,
            progress=current.progress,
            stage="Операция остановлена пользователем",
            progress_mode=current.progress_mode,
        )

    def cancel(self, project_id: str, operation_id: str) -> ProjectOperation:
        with self.lock:
            while True:
                current = self.get(project_id, operation_id)
                if current.status == OperationStatus.QUEUED:
                    # CAS below ensures a concurrent claim receives a running cancellation.
                    next_status = OperationStatus.CANCELLED
                elif current.status == OperationStatus.RUNNING:
                    next_status = OperationStatus.CANCELLING
                else:
                    return current
                operation = current.model_copy(deep=True)
                operation.status = next_status
                operation.cancel_requested_at = self.now().isoformat()
                operation.updated_at = operation.cancel_requested_at
                operation.stage = "Останавливаем операцию после текущего шага"
                if next_status == OperationStatus.CANCELLED:
                    operation.completed_at = operation.updated_at
                    operation.stage = "Операция остановлена пользователем"
                saved = self.operations.compare_and_save(current, operation)
                if saved is not None:
                    return saved

    def fail(self, operation_id: str, stage: str, code: str, error: Exception) -> None:
        current = self.operations.get(operation_id)
        try:
            self.update(
                operation_id,
                status=OperationStatus.FAILED,
                progress=current.progress,
                stage=stage,
                progress_mode=current.progress_mode,
                error=OperationError(code=code, message=str(error)),
            )
        except OperationCancelled:
            self.complete_cancellation(operation_id)
