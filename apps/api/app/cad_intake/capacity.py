"""One native CAD inspector per API process, without another job registry."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import BoundedSemaphore

_inspection_slot = BoundedSemaphore(1)
CAPACITY_POLL_SECONDS = 0.1


@contextmanager
def inspection_capacity(check_cancelled: Callable[[], None]) -> Iterator[None]:
    check_cancelled()
    while not _inspection_slot.acquire(timeout=CAPACITY_POLL_SECONDS):
        check_cancelled()
    try:
        check_cancelled()
        yield
    finally:
        _inspection_slot.release()
