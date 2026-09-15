"""Convert immutable DWG bytes into a versioned DXF cache with evidence."""

import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from app.cad_import.cache import cache_folder, file_sha256, publish, read_cached
from app.cad_import.contracts import (
    CadConversion,
    CadConversionError,
    ConvertedDrawing,
    DrawingInspection,
)
from app.cad_import.failures import retain_failure
from app.cad_import.identity import converter_identity
from app.cad_import.policy import ConversionPolicy
from app.cad_import.process import run_converter

DWG_SIGNATURES = {b"AC1015", b"AC1018", b"AC1021", b"AC1024", b"AC1027", b"AC1032"}
INSPECTION_REVISION = "drawing-inspection-v2"
MAX_INSPECTION_BYTES = 16 * 1024 * 1024


class LibreDwgConverter:
    """Explicitly configured LibreDWG; warnings remain visible to callers."""

    def __init__(
        self, executable: Path, cache: Path, policy: ConversionPolicy | None = None
    ) -> None:
        self.executable = executable.resolve(strict=True)
        self.cache = cache.resolve()
        self.policy = policy or ConversionPolicy()
        self.identity = converter_identity(self.executable)

    def convert(self, source: Path) -> ConvertedDrawing:
        source = source.resolve(strict=True)
        if not 0 < source.stat().st_size <= self.policy.max_source_bytes:
            raise CadConversionError("Размер исходного CAD вне допустимого диапазона")
        with source.open("rb") as stream:
            signature = stream.read(6)
        if signature not in DWG_SIGNATURES:
            raise CadConversionError(
                "Неподдержанная сигнатура DWG; проверьте формат файла"
            )
        digest = file_sha256(source)
        folder = cache_folder(self.cache, digest, self.identity)
        folder.mkdir(parents=True, exist_ok=True)
        cached = read_cached(folder, source, digest, self.policy.max_output_bytes)
        if cached and cached.evidence.converter == self.identity:
            return cached
        with tempfile.TemporaryDirectory(prefix="work-", dir=folder) as scratch:
            original = Path(scratch) / "input.dwg"
            shutil.copyfile(source, original)
            if file_sha256(original) != digest:
                raise CadConversionError("Исходный CAD изменился во время чтения")
            try:
                return self._convert_copy(
                    source.name, original, digest, signature, folder
                )
            except CadConversionError as error:
                evidence = retain_failure(folder, original.parent, str(error))
                raise CadConversionError(f"{error}\nДиагностика: {evidence}") from error

    def _convert_copy(
        self, name: str, original: Path, digest: str, signature: bytes, folder: Path
    ) -> ConvertedDrawing:
        work = original.parent
        output, log = work / "drawing.dxf", work / "converter.log"
        run = run_converter(
            [str(self.executable), "-v1", "-o", str(output), str(original)],
            output,
            log,
            self.policy,
        )
        if not output.is_file() or output.stat().st_size == 0:
            details = log.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise CadConversionError(
                f"Конвертер не создал DXF (код {run.exit_code}): {details}"
            )
        inspection = self._inspect(output)
        diagnostics = list(
            dict.fromkeys(
                line.strip()
                for line in log.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if line.strip()
            )
        )
        evidence = CadConversion(
            source_name=name,
            source_sha256=digest,
            source_bytes=original.stat().st_size,
            source_version=signature.decode("ascii"),
            converter=self.identity,
            output_sha256=file_sha256(output),
            output_bytes=output.stat().st_size,
            elapsed_seconds=round(run.elapsed_seconds, 6),
            exit_code=run.exit_code,
            peak_memory_bytes=run.peak_memory_bytes,
            diagnostics=diagnostics,
            inspection=inspection,
            integrity="requires_review"
            if run.exit_code != 0 or diagnostics or inspection.xrefs
            else "unverified",
        )
        return publish(folder, work, evidence)

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
            [
                sys.executable,
                "-m",
                "app.cad_import.inspection",
                str(output),
                str(summary),
            ],
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
                f"Результат конвертации не читается как DXF: {details}"
            )
        return DrawingInspection.model_validate_json(
            summary.read_text(encoding="utf-8")
        )
