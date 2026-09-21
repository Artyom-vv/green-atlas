import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from app.cad_import.process import run_converter
from app.cad_intake.capacity import inspection_capacity
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.preview_contracts import CadPreviewRequest
from app.cad_intake.preview_work import PreviewWork
from app.operations.adapters import SqliteOperationRepository
from app.operations.progress import WorkProgress

MAX_PREVIEW_RECEIPT_BYTES = 64 * 1024


class ProcessCadPreviewPreparation:
    def __init__(self, config: CadIntakeConfig, database_path: str | Path) -> None:
        self.config = config
        self.database = Path(database_path).resolve()

    def prepare(
        self,
        operation_id: str,
        request: CadPreviewRequest,
        check_cancelled: Callable[[], None],
        report_progress: Callable[[WorkProgress], None],
    ) -> None:
        with inspection_capacity(check_cancelled):
            report_progress(
                WorkProgress(stage="Проверяем выбранные источники CAD", fraction=None)
            )
            self._prepare(operation_id, request, check_cancelled)

    def _prepare(
        self,
        operation_id: str,
        request: CadPreviewRequest,
        check_cancelled: Callable[[], None],
    ) -> None:
        journal = SqliteOperationRepository(self.database, recover=False)
        try:
            intake = journal.get(request.intake_operation_id)
            if intake.cad_intake is None:
                raise ValueError("Паспорт исходного комплекта отсутствует")
            root = self.config.root(intake.cad_intake.request.root_id)
        finally:
            journal.close()
        directory = self.config.storage / "operations" / operation_id
        directory.mkdir(parents=True, exist_ok=True)
        receipt = directory / "publication.json"
        task = directory / "preview-request.json"
        work = PreviewWork(
            operation_id=operation_id,
            database=self.database,
            root=root.path,
            storage=self.config.storage,
            receipt=receipt,
        )
        task.write_text(work.model_dump_json(), encoding="utf-8")
        result = run_converter(
            [sys.executable, "-m", "app.cad_intake.preview_worker", str(task)],
            receipt,
            directory / "preview-worker.log",
            replace(self.config.policy, max_output_bytes=MAX_PREVIEW_RECEIPT_BYTES),
            environment={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
            },
            check_cancelled=check_cancelled,
        )
        if result.exit_code != 0:
            raise ValueError(
                "Подготовка CAD остановлена; диагностика сохранена на сервере"
            )
        # The authoritative receipt is in SQLite, committed with project/source.
        # A missing process output is deliberately not permission to publish again.
