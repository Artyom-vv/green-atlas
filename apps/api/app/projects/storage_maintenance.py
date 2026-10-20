"""Idle, retryable reclamation of SQLite pages released by project deletion.

Only SQLite writes its database and journals. VACUUM is never run on the
request connection, in the deletion transaction, or as an opening migration.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from time import monotonic

from app.desktop.tickets import direct_path
from app.projects.resource_lifecycle import _BusyResource, _lock, _unlock

_LOGGER = logging.getLogger(__name__)
_MIB = 1024**2
MAINTENANCE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS project_storage_maintenance ("
    "id INTEGER NOT NULL PRIMARY KEY CHECK(id=1), "
    "generation INTEGER NOT NULL, vacuumed_generation INTEGER NOT NULL DEFAULT 0, "
    "requested_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)"
)


@dataclass(frozen=True)
class MaintenanceResult:
    status: str
    before_bytes: int = 0
    after_bytes: int = 0
    reclaimed_bytes: int = 0
    error: str | None = None


class SqliteStorageMaintenance:
    """A single idle owner calls run_once; competing owners skip, never wait.

    Byte receipts are logical database/WAL file sizes, not APFS extent claims.
    Runtime supplies activity and shutdown predicates; the progress handler
    aborts the SQLite transaction when useful foreground work returns.
    """

    def __init__(
        self,
        database_path: str,
        *,
        min_reclaim_bytes: int = 64 * _MIB,
        min_free_ratio: float = 0.20,
        absolute_reclaim_bytes: int = 256 * _MIB,
        reserve_bytes: int = 64 * _MIB,
        max_duration_s: float = 30.0,
        progress_steps: int = 4096,
    ) -> None:
        self.database_path = database_path
        self.min_reclaim_bytes = min_reclaim_bytes
        self.min_free_ratio = min_free_ratio
        self.absolute_reclaim_bytes = absolute_reclaim_bytes
        self.reserve_bytes = reserve_bytes
        self.max_duration_s = max_duration_s
        self.progress_steps = progress_steps
        self._cancel = Event()
        self._state_lock = Lock()
        self._run_lock = Lock()
        self._running_connection: sqlite3.Connection | None = None

    @staticmethod
    def initialize(connection: sqlite3.Connection) -> None:
        connection.execute(MAINTENANCE_SCHEMA)

    @staticmethod
    def request(connection: sqlite3.Connection) -> None:
        """Enqueue in the same writer transaction that deleted source/history BLOBs."""
        if not connection.in_transaction:
            raise RuntimeError(
                "Storage maintenance must be requested in the delete transaction"
            )
        connection.execute(
            "INSERT INTO project_storage_maintenance(id,generation,requested_at) VALUES (1,1,?) "
            "ON CONFLICT(id) DO UPDATE SET generation=generation+1,requested_at=excluded.requested_at",
            (datetime.now(UTC).isoformat(),),
        )

    def _path(self) -> Path:
        path = Path(self.database_path).absolute()
        return direct_path(path, path.parent)

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        path = self._path()
        return sqlite3.connect(
            path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
            uri=True,
            timeout=0,
            isolation_level=None,
            check_same_thread=False,
        )

    def pending(self) -> bool:
        if self.database_path == ":memory:":
            return False
        try:
            with closing(self._connect(readonly=True)) as connection:
                return (
                    connection.execute(
                        "SELECT 1 FROM project_storage_maintenance WHERE id=1"
                    ).fetchone()
                    is not None
                )
        except (OSError, ValueError, sqlite3.Error):
            # No destructive fallback for a temporarily unreadable database.
            return True

    def cancel_current(self) -> None:
        self._cancel.set()
        with self._state_lock:
            connection = self._running_connection
            if connection is not None:
                try:
                    connection.interrupt()
                except sqlite3.Error:
                    pass

    @contextmanager
    def _owner(self) -> Iterator[None]:
        root = Path(str(self._path()) + ".resource-locks")
        direct_path(root, root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = direct_path(root / "storage-maintenance.lock", root)
        descriptor = os.open(
            path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        locked = False
        try:
            actual = os.fstat(descriptor)
            expected = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
                expected.st_dev,
                expected.st_ino,
            ):
                raise ValueError("Storage maintenance lease was replaced")
            _lock(descriptor, exclusive=True, blocking=False)
            locked = True
            yield
        finally:
            if locked:
                _unlock(descriptor)
            os.close(descriptor)

    def _bytes(self) -> int:
        path = self._path()
        total = 0
        for candidate in (path, Path(str(path) + "-wal")):
            direct_path(candidate, path.parent)
            try:
                total += candidate.stat().st_size
            except FileNotFoundError:
                pass
        return total

    def _result(
        self, status: str, before: int, error: str | None = None
    ) -> MaintenanceResult:
        after = self._bytes()
        return MaintenanceResult(status, before, after, max(0, before - after), error)

    @staticmethod
    def _checkpoint(connection: sqlite3.Connection) -> bool:
        # busy_timeout=0: a pinned reader or another writer defers the work.
        busy, _, _ = connection.execute(
            "PRAGMA main.wal_checkpoint(TRUNCATE)"
        ).fetchone()
        return busy == 0

    @staticmethod
    def _ack(connection: sqlite3.Connection, generation: int) -> None:
        connection.execute(
            "DELETE FROM project_storage_maintenance WHERE id=1 AND generation=?",
            (generation,),
        )

    @staticmethod
    def _record_failure(connection: sqlite3.Connection, error: str) -> None:
        try:
            connection.execute(
                "UPDATE project_storage_maintenance SET attempts=attempts+1,last_error=? WHERE id=1",
                (error[:500],),
            )
        except sqlite3.Error:
            # The original durable request remains even if a busy writer also
            # prevents recording this attempt's optional diagnostic.
            pass

    def _enough_space(self, connection: sqlite3.Connection) -> bool:
        pages = connection.execute("PRAGMA main.page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA main.page_size").fetchone()[0]
        required = (
            2 * max(self._path().stat().st_size, pages * page_size) + self.reserve_bytes
        )
        # SQLite's temporary database can use the OS temp filesystem. Check
        # both filesystems without changing global temp-directory settings.
        temporary = [Path(tempfile.gettempdir())]
        if os.environ.get("SQLITE_TMPDIR"):
            temporary.append(Path(os.environ["SQLITE_TMPDIR"]))
        return all(
            shutil.disk_usage(directory).free >= required
            for directory in (self._path().parent, *temporary)
        )

    def run_once(
        self,
        *,
        idle: Callable[[], bool],
        cancelled: Callable[[], bool] = lambda: False,
    ) -> MaintenanceResult:
        if self.database_path == ":memory:":
            return MaintenanceResult("noop")
        if cancelled():
            return MaintenanceResult("cancelled")
        if not idle():
            return MaintenanceResult("deferred")
        if not self._run_lock.acquire(blocking=False):
            return MaintenanceResult("busy")
        try:
            with self._owner(), closing(self._connect()) as connection:
                self._cancel.clear()
                with self._state_lock:
                    self._running_connection = connection
                try:
                    return self._run(connection, idle=idle, cancelled=cancelled)
                finally:
                    connection.set_progress_handler(None, 0)
                    with self._state_lock:
                        self._running_connection = None
        except _BusyResource:
            return MaintenanceResult("busy")
        except (OSError, ValueError, sqlite3.Error) as error:
            _LOGGER.warning("Storage maintenance will retry: %s", type(error).__name__)
            return MaintenanceResult("failed", error=type(error).__name__)
        finally:
            self._run_lock.release()

    def _run(
        self,
        connection: sqlite3.Connection,
        *,
        idle: Callable[[], bool],
        cancelled: Callable[[], bool],
    ) -> MaintenanceResult:
        row = connection.execute(
            "SELECT generation,vacuumed_generation FROM project_storage_maintenance WHERE id=1"
        ).fetchone()
        if row is None:
            return MaintenanceResult("noop")
        generation, vacuumed = row
        before = self._bytes()
        deadline = monotonic() + self.max_duration_s
        interruption: str | None = None

        def progress() -> int:
            nonlocal interruption
            if self._cancel.is_set() or cancelled():
                interruption = "cancelled"
            elif monotonic() >= deadline or not idle():
                interruption = "deferred"
            # A foreground request can arrive while the idle predicate runs.
            if self._cancel.is_set() or cancelled():
                interruption = "cancelled"
            return int(interruption is not None)

        connection.execute("PRAGMA busy_timeout=0")
        connection.execute("PRAGMA cache_size=-4096")
        connection.execute("PRAGMA mmap_size=0")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.set_progress_handler(progress, self.progress_steps)
        try:
            if progress():
                return self._result(interruption or "deferred", before)
            if not self._checkpoint(connection):
                return self._result("busy", before)
            pages = connection.execute("PRAGMA main.page_count").fetchone()[0]
            free = connection.execute("PRAGMA main.freelist_count").fetchone()[0]
            page_size = connection.execute("PRAGMA main.page_size").fetchone()[0]
            empty = (
                connection.execute("SELECT 1 FROM projects LIMIT 1").fetchone() is None
            )
            enough = free > 0 and (
                empty
                or free * page_size >= self.absolute_reclaim_bytes
                or free * page_size >= self.min_reclaim_bytes
                and free / max(1, pages) >= self.min_free_ratio
            )
            if enough:
                if not self._enough_space(connection):
                    return self._result("insufficient_space", before)
                connection.execute("VACUUM main")
                connection.set_progress_handler(None, 0)
                connection.execute(
                    "UPDATE project_storage_maintenance SET vacuumed_generation=max(vacuumed_generation,?) WHERE id=1",
                    (generation,),
                )
                connection.set_progress_handler(progress, self.progress_steps)
                vacuumed = generation
            if progress():
                return self._result(interruption or "deferred", before)
            if not self._checkpoint(connection):
                return self._result("busy", before)
            # A new DELETE increments generation and survives this older ACK.
            connection.set_progress_handler(None, 0)
            self._ack(connection, generation)
            self._checkpoint(connection)
            return self._result(
                "completed" if enough or vacuumed else "below_threshold", before
            )
        except sqlite3.Error as error:
            connection.set_progress_handler(None, 0)
            if self._cancel.is_set() or cancelled():
                interruption = "cancelled"
            if connection.in_transaction:
                connection.rollback()
            diagnostic = type(error).__name__ + ": " + str(error)
            self._record_failure(connection, diagnostic)
            status = interruption or (
                "busy"
                if getattr(error, "sqlite_errorcode", None)
                in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
                else "failed"
            )
            return self._result(status, before, diagnostic)
