from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import TypeVar


@dataclass(frozen=True)
class WorkProgress:
    """Observable work reported by an adapter.

    ``fraction`` is absent while a native atomic operation cannot expose partial
    completion. Counters always describe the current stage, not a prediction.
    """

    stage: str
    fraction: float | None = None
    processed: int | None = None
    total: int | None = None
    unit: str | None = None


ProgressReporter = Callable[[WorkProgress], None]

_cancellation_check: ContextVar[Callable[[], None] | None] = ContextVar(
    "operation_cancellation_check", default=None
)


def check_operation_cancelled() -> None:
    check = _cancellation_check.get()
    if check is not None:
        check()


@contextmanager
def operation_cancellation(check: Callable[[], None]):
    """Propagate cancellation through blocking adapters without global state."""
    token = _cancellation_check.set(check)
    try:
        yield
    finally:
        _cancellation_check.reset(token)


@contextmanager
def cancellable_lock(lock):
    """A queued operation can be cancelled while another preparation owns a lock."""
    while True:
        check_operation_cancelled()
        if lock.acquire(timeout=0.1):
            break
    try:
        check_operation_cancelled()
        yield
    finally:
        lock.release()

T = TypeVar("T")
PROGRESS_REPORT_INTERVAL_S = 0.25  # Bound journal writes, not work or accuracy.


def progress_items(
    items: Iterable[T], reporter: ProgressReporter | None, stage: str, total: int,
) -> Iterator[T]:
    """Report completed objects in this stage, not a guessed overall percentage."""
    if reporter is None:
        yield from items
        return
    reporter(WorkProgress(stage, processed=0, total=total, unit="объектов"))
    last_report = monotonic()
    for processed, item in enumerate(items, start=1):
        yield item
        now = monotonic()
        if processed == total or now - last_report >= PROGRESS_REPORT_INTERVAL_S:
            reporter(WorkProgress(stage, processed=processed, total=total, unit="объектов"))
            last_report = now


class OperationCancelled(RuntimeError):
    """Control-flow signal raised by a progress reporter after user cancellation."""
