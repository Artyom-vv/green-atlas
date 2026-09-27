from collections.abc import Callable
from typing import Protocol

from app.cad_intake.preview_contracts import CadPreviewRequest, CadPreviewResult
from app.operations.contracts import ProjectOperation
from app.operations.progress import WorkProgress
from app.projects.contracts import Project


class CadPreviewPreparation(Protocol):
    """An isolated workflow publishes its compact operation receipt atomically."""

    def prepare(
        self,
        operation_id: str,
        request: CadPreviewRequest,
        check_cancelled: Callable[[], None],
        report_progress: Callable[[WorkProgress], None],
    ) -> None: ...


class CadPreviewPublication(Protocol):
    """Project/source and the completed operation share one commit boundary."""

    def publish(
        self,
        project: Project,
        source: bytes,
        operation_id: str,
        result: CadPreviewResult,
    ) -> ProjectOperation: ...
