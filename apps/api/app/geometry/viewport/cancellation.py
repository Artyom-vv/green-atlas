"""Отмена ответа карты отдельно от подготовки общего пространственного индекса."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.operations.progress import OperationCancelled, check_operation_cancelled

_viewport_check: ContextVar[Callable[[], None] | None] = ContextVar(
    "viewport_cancellation_check", default=None
)


class ViewportRequestCancelled(OperationCancelled):
    """Клиент больше не ожидает этот ответ карты."""


@contextmanager
def viewport_cancellation(check: Callable[[], None]) -> Iterator[None]:
    token = _viewport_check.set(check)
    try:
        yield
    finally:
        _viewport_check.reset(token)


def check_viewport_cancelled() -> None:
    # Обычная отмена подготовки остаётся действующей. HTTP-разрыв видят только
    # ожидание и отображение: читатель и _IndexBuilder используют прежний checker.
    check_operation_cancelled()
    check = _viewport_check.get()
    if check is not None:
        check()
