"""Ограниченный Core supervisor для принадлежащего заданию Windows Job Object."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.native_query.transport.process_contracts import (
    NativeProcessFailure,
    NativeQueryProcessConfig,
)
from app.native_query.transport.process_control import CoreStop, checkpoint
from app.shared.failures import DomainFailure


class OwnedChild(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def read_available(self, maxbytes: int) -> bytes: ...
    def active_processes(self) -> int: ...
    def terminate_all(self) -> None: ...
    def wait(self, timeout: float) -> int | None: ...
    def close(self) -> None: ...


@dataclass
class CompletedJobCleanup:
    """Разрешение для одного задания с проверкой завершённого CAD-результата.

    Это не признак успеха Core. Проверка вызывается перед очисткой и независимо
    после неё; сигналы остаются в квитанции. Без этого разрешения supervisor строгий.
    """

    validate: Callable[[], None]
    attempted: bool = field(default=False, init=False)
    confirmed: bool = field(default=False, init=False)
    tree_empty: bool | None = field(default=None, init=False)
    signals: tuple[str, ...] = field(default=(), init=False)
    phase: str = field(default="supervision", init=False)
    _used: bool = field(default=False, init=False, repr=False)

    def accepts_signals(self, signals: tuple[str, ...]) -> bool:
        return (
            self.attempted
            and self.confirmed
            and self.tree_empty is True
            and signals == self.signals == ("TerminateJobObject",)
        )

    def begin(self) -> None:
        self.confirmed = False
        if self._used:
            raise CoreStop("invalid_input", "Completed job cleanup policy was reused")
        self._used = True

    def verify(self) -> None:
        try:
            self.validate()
        except DomainFailure:
            raise
        except (OSError, ValueError) as error:
            raise CoreStop(
                "invalid_reply",
                "Completed Core evidence failed independent verification",
            ) from error


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
    *,
    completed_cleanup: CompletedJobCleanup | None = None,
) -> tuple[NativeProcessFailure | None, bytes, tuple[str, ...]]:
    """Сохранить лимиты и явно проверить разрешённую очистку после exit 0."""
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
            if completed_cleanup is not None:
                completed_cleanup.begin()
            while True:
                drain()
                checkpoint(started, config, cancelled)
                if output_size(job, output_names) > config.max_reply_bytes:
                    raise CoreStop(
                        "output_limit", "Native output exceeded its byte budget"
                    )
                exit_code = child.poll()
                if exit_code is not None:
                    if child.active_processes():
                        if exit_code != 0 or completed_cleanup is None:
                            raise CoreStop(
                                "process_group_alive", "Core left live owned processes"
                            )
                        completed_cleanup.phase = "completion_precheck"
                        completed_cleanup.verify()
                        checkpoint(started, config, cancelled)
                        completed_cleanup.phase = "post_exit_cleanup"
                        completed_cleanup.attempted = True
                        completed_cleanup.tree_empty = False
                        stop_owned(child, config.terminate_grace_seconds, signals)
                        completed_cleanup.tree_empty = child.active_processes() == 0
                        if not completed_cleanup.tree_empty or child.poll() != 0:
                            raise CoreStop(
                                "process_group_alive",
                                "Owned post-exit cleanup was not confirmed",
                            )
                        checkpoint(started, config, cancelled)
                        completed_cleanup.phase = "completion_postcheck"
                        completed_cleanup.verify()
                        checkpoint(started, config, cancelled)
                        completed_cleanup.confirmed = True
                    break
                time.sleep(config.poll_seconds)
        except CoreStop as error:
            failure = error.failure
    finally:
        try:
            try:
                stop_owned(child, config.terminate_grace_seconds, signals)
                if completed_cleanup is not None:
                    completed_cleanup.tree_empty = child.active_processes() == 0
                while drain():
                    pass
            except CoreStop as error:
                failure = failure or error.failure
        finally:
            if completed_cleanup is not None:
                completed_cleanup.signals = tuple(signals)
            try:
                child.close()
            finally:
                with (job / "core.log").open("xb") as stream:
                    stream.write(log)
    return failure, bytes(log), tuple(signals)
