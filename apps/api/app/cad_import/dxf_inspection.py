"""Read native DXF with the existing isolated ezdxf inspection and cache."""

import os
import tempfile
from dataclasses import replace
from pathlib import Path

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError, DrawingInspection
from app.cad_import.policy import ConversionPolicy
from app.cad_import.process import run_converter
from app.shared.python_worker import worker_command

INSPECTION_REVISION = "drawing-inspection-v2"
MAX_INSPECTION_BYTES = 16 * 1024 * 1024


class DxfInspector:
    def __init__(self, cache: Path, policy: ConversionPolicy | None = None) -> None:
        self.cache = cache.resolve()
        self.policy = policy or ConversionPolicy()

    def inspect_dxf(self, source: Path) -> DrawingInspection:
        source = source.resolve(strict=True)
        if not 0 < source.stat().st_size <= self.policy.max_output_bytes:
            raise CadConversionError("Размер DXF вне бюджета проверки CAD")
        digest = file_sha256(source)
        folder = self.cache / "inspections" / digest
        folder.mkdir(parents=True, exist_ok=True)
        cached = folder / f"{INSPECTION_REVISION}.json"
        if cached.is_file() and cached.stat().st_size <= MAX_INSPECTION_BYTES:
            try:
                inspection = DrawingInspection.model_validate_json(
                    cached.read_text(encoding="utf-8")
                )
                if inspection.boundary_catalog is not None:
                    return inspection
            except (OSError, ValueError):
                pass
        with tempfile.TemporaryDirectory(prefix="work-", dir=folder) as scratch:
            work = Path(scratch)
            inspection = self._inspect(source, work)
            if file_sha256(source) != digest:
                raise CadConversionError("Чертёж изменился во время проверки контуров")
            (work / "inspection.json").replace(cached)
            return inspection

    def _inspect(self, output: Path, work: Path | None = None) -> DrawingInspection:
        work = work or output.parent
        summary, log = (
            work / "inspection.json",
            work / "inspection.log",
        )
        run = run_converter(
            worker_command("app.cad_import.inspection", str(output), str(summary)),
            summary,
            log,
            replace(self.policy, max_output_bytes=MAX_INSPECTION_BYTES),
            environment={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    filter(
                        None,
                        (
                            str(Path(__file__).resolve().parents[2]),
                            os.environ.get("PYTHONPATH"),
                        ),
                    )
                ),
                "PYTHONUTF8": "1",
            },
        )
        if run.exit_code != 0 or not summary.is_file():
            details = log.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise CadConversionError(
                f"Чертёж не читается как DXF: {details}"
            )
        return DrawingInspection.model_validate_json(
            summary.read_text(encoding="utf-8")
        )
