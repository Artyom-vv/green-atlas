from threading import RLock, Thread, Event

import pytest

from app.operations.progress import (
    OperationCancelled, cancellable_lock, operation_cancellation,
)


def test_cancelling_a_waiter_does_not_release_the_other_threads_lock():
    lock = RLock()
    acquired, release = Event(), Event()
    def owner():
        with lock:
            acquired.set()
            release.wait(2)
    worker = Thread(target=owner)
    worker.start()
    assert acquired.wait(1)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationCancelled()
    try:
        with operation_cancellation(cancel), pytest.raises(OperationCancelled):
            with cancellable_lock(lock):
                pytest.fail("cancelled waiter acquired the lock")
        assert not lock.acquire(blocking=False)
    finally:
        release.set()
        worker.join()
    with cancellable_lock(lock):
        pass
