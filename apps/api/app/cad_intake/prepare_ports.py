from collections.abc import Callable
from typing import Protocol

from app.cad_intake.prepare_contracts import CadPrepareRequest
from app.operations.progress import WorkProgress


class CadProjectPreparation(Protocol):
    def prepare(
        self,
        operation_id: str,
        request: CadPrepareRequest,
        check_cancelled: Callable[[], None],
        report_progress: Callable[[WorkProgress], None],
    ) -> None: ...
