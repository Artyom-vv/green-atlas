"""Приватные ограниченные сведения о сбое без входных данных и текста исключения.

Журнал не зависит от stdout/stderr desktop-процесса. Полный путь Core допустим
только здесь, в принадлежащем desktop-профилю файле, и не входит в публичный DTO.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.shared.performance import _LocalLog

_MAX_CHAIN = 4
_MAX_FRAMES = 12
_MAX_EVENT_BYTES = 32 * 1024
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
_FILE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,95}\.pyc?\Z")
_KINDS = frozenset(
    {
        "prepare_cad_capture",
        "calculate_geometry",
        "inspect_cad_package",
        "prepare_cad_preview",
        "prepare_cad_project",
    }
)
_NATIVE_CODES = frozenset(
    {
        "invalid_input",
        "cancelled",
        "timeout",
        "launch_failed",
        "process_exit",
        "process_group_alive",
        "output_limit",
        "log_limit",
        "invalid_reply",
        "integrity_failure",
        "io_failure",
        "cleanup_failed",
        "cleanup_failure",
        "invalid_lifecycle",
    }
)
_PHASES = frozenset(
    {
        "input",
        "stage",
        "supervision",
        "validation",
        "integrity",
        "io",
        "cleanup",
        "publish",
        "source_verification",
        "launch",
        "reply",
        "lifecycle",
        "archive_preparation",
        "staging",
        "completion_precheck",
        "post_exit_cleanup",
        "completion_postcheck",
        "completion_validation",
    }
)
_SIGNALS = frozenset({"SIGTERM", "SIGKILL", "TerminateJobObject"})
_sink: _LocalLog | None = None


@dataclass(frozen=True)
class NativeFailureDiagnostic:
    """Внутренняя квитанция отказа; путь и подробности не являются HTTP-контрактом."""

    failure_code: str
    exit_code: int | None = None
    tree_empty: bool | None = None
    signals: tuple[str, ...] = ()
    core_log_path: Path | None = None
    phase: str = "supervision"
    cleanup_attempted: bool = False
    cleanup_confirmed: bool = False


class NativeOperationError(ValueError):
    """Предметный отказ Core с отдельными приватными диагностическими полями."""

    def __init__(self, message: str, *, diagnostic: NativeFailureDiagnostic) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


def configure_operation_diagnostics(data_root: Path) -> bool:
    """До трёх файлов по 2 МиБ в Workspace/Logs; каждый сбой flush + fsync."""
    global _sink
    try:
        _sink = _LocalLog(Path(data_root), filename="operations.jsonl", durable=True)
    except Exception:
        _sink = None
        return False
    return True


def _identifier(value: str | None) -> str | None:
    try:
        return str(UUID(value)) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def _name(value: object) -> str:
    return value if isinstance(value, str) and _NAME.fullmatch(value) else "unknown"


def _stack(error: BaseException) -> list[dict[str, str | int]]:
    frames: list[dict[str, str | int]] = []
    traceback = error.__traceback__
    while traceback is not None:
        code = traceback.tb_frame.f_code
        basename = code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
        frames.append(
            {
                "file": basename if _FILE.fullmatch(basename) else "unknown",
                "function": _name(code.co_name),
                "line": min(max(traceback.tb_lineno, 0), 10_000_000),
            }
        )
        if len(frames) > _MAX_FRAMES:
            del frames[0]
        traceback = traceback.tb_next
    return frames


def _native(diagnostic: NativeFailureDiagnostic) -> dict[str, Any]:
    path = diagnostic.core_log_path
    # Не читаем файл и не resolve-им путь: он указывает на уже созданный журнал
    # задания, а не даёт диагностике дополнительную границу доступа к диску.
    private_path = (
        str(path)
        if isinstance(path, Path)
        and path.is_absolute()
        and path.name == "core.log"
        and path.parent.name.startswith("ga-native-query-")
        and len(str(path)) <= 2048
        and not any(ord(character) < 32 for character in str(path))
        else None
    )
    return {
        "failure_code": diagnostic.failure_code
        if isinstance(diagnostic.failure_code, str)
        and diagnostic.failure_code in _NATIVE_CODES
        else "unknown",
        "phase": diagnostic.phase
        if isinstance(diagnostic.phase, str) and diagnostic.phase in _PHASES
        else "unknown",
        "exit_code": diagnostic.exit_code
        if type(diagnostic.exit_code) is int
        and -(2**31) <= diagnostic.exit_code <= 0xFFFFFFFF
        else None,
        "tree_empty": diagnostic.tree_empty
        if type(diagnostic.tree_empty) is bool
        else None,
        "signals": [
            signal
            for signal in diagnostic.signals[:8]
            if isinstance(signal, str) and signal in _SIGNALS
        ]
        if isinstance(diagnostic.signals, tuple)
        else [],
        "cleanup_attempted": diagnostic.cleanup_attempted is True,
        "cleanup_confirmed": diagnostic.cleanup_confirmed is True,
        "core_log_path": private_path,
    }


def record_operation_failure(
    operation_id: str,
    *,
    code: str,
    error: Exception,
    project_id: str | None = None,
    kind: str | None = None,
) -> bool:
    """Вернуть True только после успешной записи; отказ журнала не меняет расчёт."""
    sink = _sink
    if sink is None or not sink.enabled:
        return False
    try:
        chain: list[dict[str, Any]] = []
        native: dict[str, Any] | None = None
        visited: set[int] = set()
        current: BaseException | None = error
        while (
            current is not None
            and len(chain) < _MAX_CHAIN
            and id(current) not in visited
        ):
            visited.add(id(current))
            chain.append(
                {"class": _name(type(current).__name__), "stack": _stack(current)}
            )
            if native is None and isinstance(current, NativeOperationError):
                if isinstance(current.diagnostic, NativeFailureDiagnostic):
                    native = _native(current.diagnostic)
            current = current.__cause__ or current.__context__
        event = {
            "schema": "green-atlas.operation-failure/1",
            "timestamp": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
            "operation_id": _identifier(operation_id),
            "project_id": _identifier(project_id),
            "kind": kind if kind in _KINDS else "unknown",
            "code": code
            if isinstance(code, str) and _CODE.fullmatch(code)
            else "UNKNOWN",
            "exceptions": chain,
            "native": native,
        }
        if len(json.dumps(event, ensure_ascii=True).encode()) > _MAX_EVENT_BYTES:
            return False
        sink.write(event)
        return sink.enabled
    except Exception:
        return False
