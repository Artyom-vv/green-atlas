import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from app.cad_import.cache import file_sha256
from app.cad_import.package_contracts import SourcePackage
from app.cad_import.process import run_converter
from app.cad_intake.capacity import inspection_capacity
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.contracts import CadIntakeRequest, CadPackagePassport
from app.cad_intake.passport import make_passport
from app.cad_intake.worker import InspectionWork
from app.operations.progress import WorkProgress


class ProcessPackageInspection:
    def __init__(self, config: CadIntakeConfig) -> None:
        self.config = config

    def inspect(
        self,
        operation_id: str,
        request: CadIntakeRequest,
        check_cancelled: Callable[[], None],
        report_progress: Callable[[WorkProgress], None],
    ) -> CadPackagePassport:
        with inspection_capacity(check_cancelled):
            report_progress(
                WorkProgress(stage="Проверяем чертёж и внешние ссылки", fraction=None)
            )
            return self._inspect(operation_id, request, check_cancelled)

    def _inspect(
        self,
        operation_id: str,
        request: CadIntakeRequest,
        check_cancelled: Callable[[], None],
    ) -> CadPackagePassport:
        self.config.require_enabled()
        assert self.config.converter is not None
        root = self.config.root(request.root_id)
        directory = self.config.storage / "operations" / operation_id
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / "source-package.json"
        work = InspectionWork(
            root=root.path,
            cache=self.config.storage / "cache",
            converter=self.config.converter,
            output=output,
            request=request,
        )
        task = directory / "request.json"
        task.write_text(work.model_dump_json(indent=2), encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        environment["PYTHONUTF8"] = "1"
        result = run_converter(
            [sys.executable, "-m", "app.cad_intake.worker", str(task)],
            output,
            directory / "worker.log",
            replace(self.config.policy, max_output_bytes=16 * 1024 * 1024),
            environment=environment,
            check_cancelled=check_cancelled,
        )
        if result.exit_code != 0 or not output.exists():
            raise ValueError(
                "Не удалось проверить комплект CAD. Диагностика сохранена на сервере"
            )
        check_cancelled()
        package = SourcePackage.model_validate_json(output.read_text(encoding="utf-8"))
        hashes: dict[str, str] = {}
        for drawing in package.drawings:
            check_cancelled()
            if drawing.normalized_path and drawing.status == "readable":
                hashes[drawing.path] = file_sha256(Path(drawing.normalized_path))
        return make_passport(package, request.root_id, file_sha256(output), hashes)
