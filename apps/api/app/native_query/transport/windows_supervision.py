"""Ограниченный Core supervisor для принадлежащего заданию Windows Job Object."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from app.native_query.transport.process_contracts import (
    NativeProcessFailure,
    NativeQueryProcessConfig,
)
from app.native_query.transport.process_control import CoreStop, checkpoint


class OwnedChild(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def read_available(self, maxbytes: int) -> bytes: ...
    def active_processes(self) -> int: ...
    def terminate_all(self) -> None: ...
    def wait(self, timeout: float) -> int | None: ...
    def close(self) -> None: ...


def stop_owned(child: OwnedChild, grace: float, signals: list[str]) -> None:
    """Остановить только собственный job и дождаться всех его процессов."""
    if child.active_processes():
        child.terminate_all()
        signals.append("TerminateJobObject")
    deadline = time.monotonic() + grace
    while child.active_processes():
        if time.monotonic() >= deadline:
            raise OSError("Windows Job Object не завершил все принадлежащие процессы")
        time.sleep(min(0.01, grace))
    if child.wait(max(0.0, deadline - time.monotonic())) is None:
        raise OSError("Не получен конечный статус принадлежащего Core-процесса")


def supervise(
    child: OwnedChild,
    job: Path,
    started: float,
    config: NativeQueryProcessConfig,
    cancelled: Callable[[], bool] | None,
    output_names: tuple[str, ...],
    output_size: Callable[[Path, tuple[str, ...]], int],
) -> tuple[NativeProcessFailure | None, bytes, tuple[str, ...]]:
    """Сохранять прежние лимиты и отклонять успех лидера с живым потомком."""
    log = bytearray()
    signals: list[str] = []
    failure = None

    def drain() -> bool:
        remaining = config.max_log_bytes - len(log)
        chunk = child.read_available(min(65536, remaining + 1))
        log.extend(chunk[:remaining])
        if len(chunk) > remaining:
            raise CoreStop("log_limit", "Core log exceeded its byte budget")
        return bool(chunk)

    try:
        try:
            while True:
                drain()
                checkpoint(started, config, cancelled)
                if output_size(job, output_names) > config.max_reply_bytes:
                    raise CoreStop("output_limit", "Native output exceeded its byte budget")
                if child.poll() is not None:
                    if child.active_processes():
                        raise CoreStop(
                            "process_group_alive", "Core left live owned processes"
                        )
                    break
                time.sleep(config.poll_seconds)
        except CoreStop as error:
            failure = error.failure
    finally:
        try:
            try:
                stop_owned(child, config.terminate_grace_seconds, signals)
                while drain():
                    pass
            except CoreStop as error:
                failure = failure or error.failure
        finally:
            try:
                child.close()
            finally:
                with (job / "core.log").open("xb") as stream:
                    stream.write(log)
    return failure, bytes(log), tuple(signals)
