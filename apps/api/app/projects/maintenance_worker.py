"""Один владелец фонового уплотнения; пользовательский запрос прерывает его."""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Event, Lock, Thread, current_thread
from time import monotonic
from typing import Protocol
from weakref import WeakSet

_LOGGER = logging.getLogger(__name__)
_foreground_lock = Lock()
_foreground_users = 0
_last_foreground = monotonic()
_owners: WeakSet["StorageMaintenanceWorker"] = WeakSet()


class MaintenanceStore(Protocol):
    def pending(self) -> bool: ...
    def cancel_current(self) -> None: ...
    def run_once(
        self, *, idle: Callable[[], bool], cancelled: Callable[[], bool]
    ) -> object: ...


@contextmanager
def foreground_activity() -> Iterator[None]:
    """Начать учёт до создания runtime и до первого чтения SQLite в HTTP."""
    global _foreground_users, _last_foreground
    with _foreground_lock:
        _foreground_users += 1
        _last_foreground = monotonic()
        owners = tuple(_owners)
    try:
        for owner in owners:
            owner.interrupt()
        yield
    finally:
        with _foreground_lock:
            _foreground_users -= 1
            _last_foreground = monotonic()
            owners = tuple(_owners)
        for owner in owners:
            owner.wake()


class StorageMaintenanceWorker:
    def __init__(
        self,
        store: MaintenanceStore,
        *,
        idle: Callable[[], bool],
        quiet_seconds: float = 3,
        retry_seconds: float = 30,
    ) -> None:
        self.store, self._external_idle = store, idle
        self.quiet_seconds, self.retry_seconds = quiet_seconds, retry_seconds
        self._stop, self._wake = Event(), Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self._requested = 0
        with _foreground_lock:
            _owners.add(self)

    def idle(self) -> bool:
        with _foreground_lock:
            quiet = (
                not _foreground_users
                and monotonic() - _last_foreground >= self.quiet_seconds
            )
        return quiet and not self._stop.is_set() and self._external_idle()

    def start_if_pending(self) -> None:
        try:
            pending = self.store.pending()
        except Exception:
            _LOGGER.warning("Проверка очереди уплотнения отложена", exc_info=True)
            pending = True
        if pending:
            self.request()

    def request(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self._requested += 1
            if self._thread is None:
                self._thread = Thread(
                    target=self._run, name="ga-storage-maintenance", daemon=True
                )
                try:
                    self._thread.start()
                except RuntimeError:
                    self._thread = None
                    _LOGGER.warning("Запуск уплотнения отложен", exc_info=True)
                    return
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    def interrupt(self) -> None:
        try:
            self.store.cancel_current()
        except Exception:
            _LOGGER.warning("Прерывание уплотнения отложено", exc_info=True)

    def _run(self) -> None:
        delay: float | None = self.quiet_seconds
        while not self._stop.is_set():
            self._wake.wait(delay)
            self._wake.clear()
            if self._stop.is_set():
                return
            if not self.idle():
                delay = max(0.05, self.quiet_seconds)
                continue
            with self._lock:
                requested = self._requested
            try:
                if self.store.pending():
                    self.store.run_once(idle=self.idle, cancelled=self._stop.is_set)
                pending = self.store.pending()
            except Exception:
                _LOGGER.exception("Отложенное уплотнение хранилища не завершено")
                pending = True
            with self._lock:
                # DELETE во время уплотнения не теряет сигнал о новом поколении.
                pending = pending or requested != self._requested
            delay = self.retry_seconds if pending else None

    def close(self) -> None:
        with _foreground_lock:
            _owners.discard(self)
        self._stop.set()
        self.interrupt()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join()
