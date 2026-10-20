"""Общий допуск по времени и отмене для платформенных Core supervisor."""

import time
from collections.abc import Callable

from app.native_query.transport.process_contracts import (
    FailureCode,
    NativeProcessFailure,
    NativeQueryProcessConfig,
)


class CoreStop(Exception):
    def __init__(self, code: FailureCode, message: str):
        self.failure = NativeProcessFailure(code, message)
        super().__init__(message)


def checkpoint(
    started: float,
    config: NativeQueryProcessConfig,
    cancelled: Callable[[], bool] | None,
) -> None:
    if cancelled is not None and cancelled():
        raise CoreStop("cancelled", "Native query cancelled")
    if time.monotonic() - started >= config.timeout_seconds:
        raise CoreStop("timeout", "Native query exceeded its time budget")
