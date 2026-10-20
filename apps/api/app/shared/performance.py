"""Ограниченный локальный журнал этапов без содержимого проектов и секретов."""

from __future__ import annotations

import json
import math
import os
import re
import stat
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import RLock
from time import monotonic
from typing import Any, ParamSpec, TypeVar
from uuid import UUID, uuid4

_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 2
_STAGE_SAMPLES = 8
_MAX_LABELS = 64
_LABEL = re.compile(r"[a-z][a-z0-9_.-]{0,79}\Z")
_ERROR = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_METRICS = frozenset(
    {
        "processed",
        "total",
        "cache_hits",
        "cache_hit",
        "objects",
        "records",
        "faces",
        "bytes",
        "items",
        "batch_size",
        "candidates",
        "year",
    }
)
_current_trace: ContextVar[PerformanceTrace | None] = ContextVar(
    "performance_trace", default=None
)
_current_stages: ContextVar[tuple[_Stage, ...]] = ContextVar(
    "performance_stages", default=()
)
_sink: _LocalLog | None = None
P = ParamSpec("P")
R = TypeVar("R")


def _label(value: str) -> str:
    return (
        value if isinstance(value, str) and _LABEL.fullmatch(value) else "invalid_label"
    )


def _project_id(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        return "invalid"


def _metrics(values: dict[str, Any]) -> dict[str, int | float | bool | None]:
    return {
        key: value
        for key, value in values.items()
        if key in _METRICS
        and (value is None or type(value) in {int, float, bool})
        and (type(value) is not float or math.isfinite(value))
    }


class _LocalLog:
    def __init__(
        self,
        data_root: Path,
        *,
        filename: str = "performance.jsonl",
        durable: bool = False,
    ) -> None:
        if filename not in {"performance.jsonl", "operations.jsonl"}:
            raise ValueError("Unknown local log filename")
        self.root = data_root.absolute()
        self.directory = self.root / "Logs"
        self.path = self.directory / filename
        self.durable = durable
        self.enabled = True
        self._lock = RLock()
        from app.desktop.tickets import direct_path

        direct_path(self.root, self.root)
        if sys.platform == "win32":
            from app.native_query.transport.windows_job_files import prepare_directory

            prepare_directory(self.directory)
        else:
            self.directory.mkdir(mode=0o700, exist_ok=True)
        self._validate_paths()
        # Fail open now on read-only storage; never alter stdout readiness output.
        with self._open() as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() > _MAX_BYTES:
                stream.truncate(0)
        for path in self._paths()[1:]:
            if path.exists() and path.stat().st_size > _MAX_BYTES:
                path.unlink()

    def _paths(self) -> list[Path]:
        return [
            self.path,
            *(
                self.path.with_name(self.path.name + f".{index}")
                for index in range(1, _BACKUPS + 1)
            ),
        ]

    def _validate_paths(self) -> None:
        from app.desktop.tickets import direct_path

        for path in self._paths():
            direct_path(path, self.root)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Unsafe local log target")

    def _open(self):
        if sys.platform == "win32":
            from app.native_query.transport import windows_job_files

            try:
                return windows_job_files.open_regular(self.path, writable=True)
            except FileNotFoundError:
                # Existing adapter pins every parent and creates with CREATE_NEW.
                return windows_job_files._open(self.path, writable=True, create=True)
        flags = (
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(self.path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Unsafe local log inode")
            return os.fdopen(descriptor, "ab")
        except BaseException:
            os.close(descriptor)
            raise

    def _rotate(self) -> None:
        paths = self._paths()
        self._validate_paths()
        paths[-1].unlink(missing_ok=True)
        for source, destination in reversed(list(zip(paths, paths[1:]))):
            try:
                if source.stat().st_size > _MAX_BYTES:
                    source.unlink()
                else:
                    source.replace(destination)
            except FileNotFoundError:
                pass

    def write(self, value: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            encoded = (
                json.dumps(
                    value, ensure_ascii=True, separators=(",", ":"), allow_nan=False
                )
                + "\n"
            ).encode()
            if len(encoded) > _MAX_BYTES:
                return
            with self._lock:
                self._validate_paths()
                with self._open() as stream:
                    stream.seek(0, os.SEEK_END)
                    rotate = stream.tell() + len(encoded) > _MAX_BYTES
                    if not rotate:
                        stream.write(encoded)
                        if self.durable:
                            stream.flush()
                            os.fsync(stream.fileno())
                        return
                self._rotate()
                with self._open() as stream:
                    stream.seek(0, os.SEEK_END)
                    stream.write(encoded)
                    if self.durable:
                        stream.flush()
                        os.fsync(stream.fileno())
        except Exception:
            # Diagnostics may fail; calculation and integrity failures still propagate.
            self.enabled = False


def configure_performance_log(data_root: Path) -> bool:
    """Use at most three 2 MiB files under the desktop-owned DataRoot."""
    global _sink
    try:
        _sink = _LocalLog(Path(data_root))
    except Exception:
        _sink = None
        return False
    return True


@dataclass
class _Counters:
    count: int = 0
    bytes: int = 0
    seconds: float = 0.0

    def fields(self) -> dict[str, int | float]:
        return {
            "hash_count": self.count,
            "hash_bytes": self.bytes,
            "hash_ms": round(self.seconds * 1000, 3),
        }


@dataclass
class _Stage:
    trace: PerformanceTrace
    name: str
    identifier: int
    parent: int | None
    metadata: dict[str, Any]
    started: float = field(default_factory=monotonic)
    counters: _Counters = field(default_factory=_Counters)
    sampled: bool = True


class PerformanceTrace:
    def __init__(
        self, endpoint: str, project_id: str | None, sink: _LocalLog | None
    ) -> None:
        self.request_id = uuid4().hex
        self.endpoint = _label(endpoint)
        self.project_id = _project_id(project_id)
        self.started = monotonic()
        self.outcome = "ok"
        self.status_code: int | None = None
        self.error_class: str | None = None
        self._sink = sink
        self._lock = RLock()
        self._stage_sequence = 0
        self._counters = _Counters()
        self._stage_counts: dict[str, int] = {}
        self._stage_summary: dict[str, dict[str, int | float]] = {}
        self._suppressed_stages = 0

    def set_outcome(self, outcome: str, status_code: int | None = None) -> None:
        self.outcome = outcome if outcome in {"ok", "error", "cancelled"} else "error"
        self.status_code = (
            status_code
            if type(status_code) is int and 100 <= status_code <= 599
            else None
        )

    def _emit(self, event: str, **fields: Any) -> None:
        if self._sink is None:
            return
        try:
            with self._lock:
                self._sink.write(
                    {
                        "event": event,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "request_id": self.request_id,
                        "pid": os.getpid(),
                        "endpoint": self.endpoint,
                        "project_id": self.project_id,
                        "elapsed_ms": round((monotonic() - self.started) * 1000, 3),
                        **fields,
                    }
                )
        except Exception:
            self._sink.enabled = False


def _failure(error: BaseException) -> tuple[str, str]:
    name = type(error).__name__
    outcome = (
        "cancelled"
        if name in {"OperationCancelled", "CancelledError", "ClientDisconnect"}
        else "error"
    )
    return outcome, name if _ERROR.fullmatch(name) else "Exception"


@contextmanager
def performance_trace(
    endpoint: str, project_id: str | None = None
) -> Iterator[PerformanceTrace]:
    sink = _sink if _sink is not None and _sink.enabled else None
    trace = PerformanceTrace(endpoint, project_id, sink)
    token = _current_trace.set(trace) if trace._sink is not None else None
    stages = _current_stages.set(()) if token is not None else None
    trace._emit(
        "trace_start",
        platform=sys.platform,
        python_version=".".join(str(value) for value in sys.version_info[:3]),
        source_manifest_sha256=None,
    )
    try:
        yield trace
    except BaseException as error:
        trace.outcome, trace.error_class = _failure(error)
        raise
    finally:
        trace._emit(
            "trace_end",
            wall_ms=round((monotonic() - trace.started) * 1000, 3),
            outcome=trace.outcome,
            status_code=trace.status_code,
            error_class=trace.error_class,
            stage_summary=trace._stage_summary,
            suppressed_stage_count=trace._suppressed_stages,
            **trace._counters.fields(),
        )
        if stages is not None:
            _current_stages.reset(stages)
        if token is not None:
            _current_trace.reset(token)


@contextmanager
def performance_stage(name: str, **metadata: Any) -> Iterator[None]:
    trace = _current_trace.get()
    if trace is None or trace._sink is None or not trace._sink.enabled:
        yield
        return
    parents = _current_stages.get()
    with trace._lock:
        trace._stage_sequence += 1
        label = _label(name)
        aggregate_only = label.startswith("domain.batch.")
        if label not in trace._stage_counts and len(trace._stage_counts) >= _MAX_LABELS:
            label = "other_stages"
        count = trace._stage_counts.get(label, 0) + 1
        trace._stage_counts[label] = count
        sampled = not aggregate_only and count <= _STAGE_SAMPLES
        trace._suppressed_stages += int(not sampled)
        stage = _Stage(
            trace,
            label,
            trace._stage_sequence,
            parents[-1].identifier if parents else None,
            _metrics(metadata),
            sampled=sampled,
        )
    fields = dict(
        stage=stage.name, stage_id=stage.identifier, parent_stage_id=stage.parent
    )
    if stage.sampled:
        trace._emit("stage_start", **fields, **stage.metadata)
    token = _current_stages.set((*parents, stage))
    outcome, error_class = "ok", None
    try:
        yield
    except BaseException as error:
        outcome, error_class = _failure(error)
        trace.error_class = error_class
        raise
    finally:
        wall_ms = round((monotonic() - stage.started) * 1000, 3)
        with trace._lock:
            summary = trace._stage_summary.setdefault(
                stage.name,
                {
                    "count": 0,
                    "wall_ms": 0.0,
                    "max_ms": 0.0,
                    "hash_count": 0,
                    "hash_bytes": 0,
                    "hash_ms": 0.0,
                },
            )
            summary["count"] += 1
            summary["wall_ms"] = round(summary["wall_ms"] + wall_ms, 3)
            summary["max_ms"] = max(summary["max_ms"], wall_ms)
            for key, value in stage.counters.fields().items():
                summary[key] = round(summary[key] + value, 3)
        if stage.sampled or outcome != "ok":
            trace._emit(
                "stage_end",
                **fields,
                **stage.metadata,
                wall_ms=wall_ms,
                sampled=stage.sampled,
                outcome=outcome,
                error_class=error_class,
                **stage.counters.fields(),
            )
        _current_stages.reset(token)


def performance_span(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            with performance_stage(name):
                return function(*args, **kwargs)

        return wrapped

    return decorate


def record_hash(byte_count: int, elapsed_seconds: float) -> None:
    trace = _current_trace.get()
    if trace is None or type(byte_count) is not int or byte_count < 0:
        return
    if (
        not isinstance(elapsed_seconds, (int, float))
        or not math.isfinite(elapsed_seconds)
        or elapsed_seconds < 0
    ):
        return
    with trace._lock:
        for counters in (
            trace._counters,
            *(
                stage.counters
                for stage in _current_stages.get()
                if stage.trace is trace
            ),
        ):
            counters.count += 1
            counters.bytes += byte_count
            counters.seconds += elapsed_seconds
