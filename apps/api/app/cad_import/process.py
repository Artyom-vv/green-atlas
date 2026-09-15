"""Bound a converter process without shell expansion or an interactive window."""

import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.cad_import.contracts import CadConversionError
from app.cad_import.memory import ProcessTree
from app.cad_import.policy import ConversionPolicy


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    elapsed_seconds: float
    peak_memory_bytes: int


def run_converter(
    command: list[str],
    output: Path,
    log: Path,
    policy: ConversionPolicy,
    *,
    environment: dict[str, str] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> ProcessResult:
    started = time.monotonic()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log.open("wb") as stream:
        process = subprocess.Popen(
            command,
            cwd=output.parent,
            stdout=stream,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            env=environment,
        )
        peak_memory = 0
        tree = ProcessTree(process.pid)
        try:
            while process.poll() is None:
                if check_cancelled is not None:
                    check_cancelled()
                _check_budget(output, log, started, policy)
                peak_memory = max(peak_memory, tree.resident_bytes())
                if peak_memory > policy.max_memory_bytes:
                    raise CadConversionError(
                        "Превышен бюджет памяти преобразования CAD"
                    )
                time.sleep(policy.poll_seconds)
            _check_budget(output, log, started, policy)
            if check_cancelled is not None:
                check_cancelled()
            return ProcessResult(
                process.returncode, time.monotonic() - started, peak_memory
            )
        finally:
            tree.stop()
            process.wait()


def _check_budget(
    output: Path, log: Path, started: float, policy: ConversionPolicy
) -> None:
    if time.monotonic() - started > policy.timeout_seconds:
        raise CadConversionError("Превышено время преобразования CAD")
    if output.exists() and output.stat().st_size > policy.max_output_bytes:
        raise CadConversionError("Результат CAD превышает допустимый объём")
    if log.exists() and log.stat().st_size > policy.max_log_bytes:
        raise CadConversionError("Конвертер CAD выдал слишком большой журнал ошибок")
