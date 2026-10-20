"""Ограниченный запуск прежнего CAD-конвертера без shell-подстановок и интерактивных
окон.
"""

import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.cad_import.contracts import CadConversionError, CadConversionMemoryExceeded
from app.cad_import.memory import ProcessTree
from app.cad_import.policy import ConversionPolicy
from app.shared.child_dll_search import child_dll_search
from app.shared.python_worker import is_bundled_worker_command


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
    flags = getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0
    with log.open("wb") as stream:
        process = None
        try:
            with child_dll_search(external=not is_bundled_worker_command(command)):
                process = subprocess.Popen(
                    command,
                    cwd=output.parent,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=flags,
                    env=environment,
                )
        except BaseException:
            if process is not None:
                ProcessTree(process.pid).stop()
                process.wait()
            raise
        peak_memory = 0
        tree = ProcessTree(process.pid)
        try:
            while process.poll() is None:
                if check_cancelled is not None:
                    check_cancelled()
                _check_budget(output, log, started, policy)
                peak_memory = max(peak_memory, tree.resident_bytes())
                if (
                    policy.max_memory_bytes is not None
                    and peak_memory > policy.max_memory_bytes
                ):
                    raise CadConversionMemoryExceeded(
                        limit_bytes=policy.max_memory_bytes,
                        peak_memory_bytes=peak_memory,
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
