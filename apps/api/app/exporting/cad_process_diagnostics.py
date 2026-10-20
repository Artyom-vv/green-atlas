"""Приватные свидетельства выполнения CAD-процесса; отсутствие квитанции само по себе не
доказывает сбой запуска.
"""

import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from app.native_query.transport import job_files
from app.native_query.transport.process_contracts import NativeProcessFailure
from app.native_query.transport.process_control import CoreStop
from app.operations.progress import OperationCancelled
from app.shared.failures import DomainFailure, FailureReason

MAX_FATAL_REPORT_BYTES = 64 * 1024
STARTUP_FAILURE_MESSAGE = (
    "AutoCAD Core остановился при запуске, до подготовки выпуска. "
    "Сохранённый проект не изменён"
)
UNFINISHED_RELEASE_MESSAGE = "AutoCAD не завершил выпуск. Сохранённый проект не изменён"


def _empty_drawing_fatal_report(job: Path) -> bool:
    try:
        path = job_files.package_file(job, "acad.err")
        with job_files.open_regular(path) as stream:
            content = stream.read(MAX_FATAL_REPORT_BYTES + 1)
    except (OSError, ValueError):
        return False
    if len(content) > MAX_FATAL_REPORT_BYTES:
        return False
    lines = content.splitlines()
    return any(line.strip().startswith(b"FATAL ERROR:") for line in lines) and any(
        b"Drawing:" in line and not line.partition(b"Drawing:")[2].strip()
        for line in lines
    )


def record_process(
    job: Path,
    command: tuple[str, ...],
    *,
    process_id: int,
    exit_code: int | None,
    failure: NativeProcessFailure | None,
    signals: tuple[str, ...],
    elapsed_seconds: float,
    log: bytes,
    completed_cleanup_verified: bool = False,
) -> bool:
    """Сохранять ограниченные сведения своего процесса без окружения и native-каналов.

    Сбой до CAD-команд подтверждают только ненулевой конечный exit, явный fatal-отчёт
    пустого чертежа и полный журнал одной стадии запуска. Таймаут, усечённый журнал и
    отсутствие release.json эту стадию не доказывают.
    """
    startup_failed = (
        exit_code is not None
        and exit_code != 0
        and failure is None
        and not signals
        and b"AutoCAD Core Engine Console" in log
        and b"Version Number:" in log
        and b"AutoCAD menu utilities loaded." not in log
        and b"Command:" not in log
        and _empty_drawing_fatal_report(job)
    )
    receipt = {
        "schema": "green-atlas.cad-release-process/1",
        "state": "completed"
        if failure is None
        and exit_code == 0
        and (
            not signals
            or (completed_cleanup_verified and signals == ("TerminateJobObject",))
        )
        else "failed",
        "command": command,
        "process_id": process_id,
        "exit_code": exit_code,
        "failure": asdict(failure) if failure is not None else None,
        "signals": signals,
        "elapsed_seconds": elapsed_seconds,
        "log_sha256": sha256(log).hexdigest(),
        "startup_failed": startup_failed,
        "completed_cleanup_verified": completed_cleanup_verified,
    }
    # Квитанция принадлежит новому Core-заданию; запись через замену или symlink запрещена.
    with (job / "process.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return startup_failed


def record_interrupted_process(
    job: Path,
    command: tuple[str, ...],
    *,
    process_id: int,
    exit_code: int | None,
    error: Exception,
    signals: tuple[str, ...],
    elapsed_seconds: float,
    max_log_bytes: int,
) -> None:
    """Сохранить отказ supervisor, не подменяя исходную ошибку диагностикой."""
    if isinstance(error, CoreStop):
        failure = error.failure
    elif isinstance(error, OperationCancelled):
        failure = NativeProcessFailure("cancelled", "CAD release was cancelled")
    elif isinstance(error, DomainFailure):
        code = (
            "timeout"
            if error.reason == FailureReason.CAD_RELEASE_TIMEOUT
            else "invalid_reply"
        )
        failure = NativeProcessFailure(code, "CAD release verification failed")
    else:
        failure = NativeProcessFailure(
            "io_failure", "CAD release supervision did not complete"
        )
    try:
        try:
            with job_files.open_regular(
                job_files.package_file(job, "core.log")
            ) as stream:
                log = stream.read(max_log_bytes)
        except (OSError, ValueError):
            log = b""
        record_process(
            job,
            command,
            process_id=process_id,
            exit_code=exit_code,
            failure=failure,
            signals=signals,
            elapsed_seconds=elapsed_seconds,
            log=log,
        )
    except (OSError, ValueError):
        # В том числе полный диск: пользователь всё равно получит исходный отказ.
        pass
