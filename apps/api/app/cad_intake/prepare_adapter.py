import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path

from app.cad_import.process import run_converter
from app.cad_intake.capacity import inspection_capacity
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.prepare_contracts import CadPrepareRequest
from app.cad_intake.prepare_policy import MAX_PREPARE_RECEIPT_BYTES, PREPARE_POLICY
from app.cad_intake.work import CadWork
from app.operations.adapters import SqliteOperationRepository
from app.operations.progress import WorkProgress


class ProcessCadProjectPreparation:
    def __init__(self, config: CadIntakeConfig, database_path: str | Path) -> None:
        self.config = config
        self.database = Path(database_path).resolve()

    def prepare(
        self,
        operation_id: str,
        request: CadPrepareRequest,
        check_cancelled: Callable[[], None],
        report_progress: Callable[[WorkProgress], None],
    ) -> None:
        with inspection_capacity(check_cancelled):
            report_progress(WorkProgress(stage="Проверяем полный DXF", fraction=None))
            journal = SqliteOperationRepository(self.database, recover=False)
            try:
                intake = journal.get(request.intake_operation_id)
                if intake.cad_intake is None:
                    raise ValueError("Нет паспорта исходника")
                root = self.config.root(intake.cad_intake.request.root_id)
            finally:
                journal.close()
            directory = self.config.storage / "operations" / operation_id
            directory.mkdir(parents=True, exist_ok=True)
            work = CadWork(
                operation_id=operation_id,
                database=self.database,
                root=root.path,
                storage=self.config.storage,
                receipt=directory / "publication.json",
            )
            task = directory / "prepare-request.json"
            task.write_text(work.model_dump_json(), encoding="utf-8")
            result = run_converter(
                [sys.executable, "-m", "app.cad_intake.prepare_worker", str(task)],
                work.receipt,
                directory / "prepare-worker.log",
                replace(PREPARE_POLICY, max_output_bytes=MAX_PREPARE_RECEIPT_BYTES),
                environment={
                    **os.environ,
                    "PYTHONUTF8": "1",
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                },
                check_cancelled=check_cancelled,
            )
            (directory / "process-metrics.json").write_text(
                json.dumps(asdict(result)), encoding="utf-8"
            )
            if result.exit_code != 0:
                raise ValueError(
                    "Полный DXF не подготовлен; подробности сохранены в журнале"
                )
