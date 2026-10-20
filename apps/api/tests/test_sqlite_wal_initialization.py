"""Первое включение WAL повторяет только блокировку в прежнем бюджете ожидания."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Condition
from unittest.mock import Mock

import pytest
from test_prepared_storage import resource
from test_prepared_storage import saved as saved_fixture
from test_prepared_storage import setup as setup_fixture

from app.shared import sqlite_sql

saved = saved_fixture
setup = setup_fixture


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sqlite_sql, "monotonic", clock.monotonic)
    monkeypatch.setattr(sqlite_sql, "sleep", clock.sleep)
    return clock


def failure(code):
    error = sqlite3.OperationalError("controlled SQLite failure")
    if code is not None:
        error.sqlite_errorcode = code
    return error


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, 261, 262])
def test_busy_wal_retry_preserves_remaining_deadline_and_connection(clock, code):
    connection = Mock(spec=sqlite3.Connection)
    statements = []
    busy = failure(code)

    def execute(statement):
        statements.append(statement)
        if statement == "PRAGMA journal_mode=WAL" and statements.count(statement) == 1:
            clock.now = 2.5
            raise busy

    connection.execute.side_effect = execute
    sqlite_sql.configure_sqlite_connection(connection)
    assert statements == [
        "PRAGMA busy_timeout=5000",
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=2490",
        "PRAGMA journal_mode=WAL",
        "PRAGMA busy_timeout=5000",
        "PRAGMA synchronous=NORMAL",
    ]
    assert clock.waits == [0.01]
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()
    connection.close.assert_not_called()


@pytest.mark.parametrize("code", [sqlite3.SQLITE_ERROR, sqlite3.SQLITE_IOERR, None])
def test_other_wal_errors_propagate_without_retry(clock, code):
    connection = Mock(spec=sqlite3.Connection)
    error = failure(code)
    connection.execute.side_effect = [None, error]
    with pytest.raises(sqlite3.OperationalError) as caught:
        sqlite_sql.configure_sqlite_connection(connection)
    assert caught.value is error
    assert [call.args[0] for call in connection.execute.call_args_list] == [
        "PRAGMA busy_timeout=5000",
        "PRAGMA journal_mode=WAL",
    ]
    assert clock.waits == []


def test_first_sqlite_wait_cannot_grant_another_timeout(clock):
    connection = Mock(spec=sqlite3.Connection)
    error = failure(sqlite3.SQLITE_BUSY)

    def execute(statement):
        if statement == "PRAGMA journal_mode=WAL":
            clock.now = 5.0
            raise error

    connection.execute.side_effect = execute
    with pytest.raises(sqlite3.OperationalError) as caught:
        sqlite_sql.configure_sqlite_connection(connection)
    assert caught.value is error
    assert connection.execute.call_count == 2
    assert clock.waits == []


def test_persistent_immediate_busy_stops_at_existing_timeout(clock):
    connection = Mock(spec=sqlite3.Connection)
    error = failure(sqlite3.SQLITE_BUSY)

    def execute(statement):
        if statement == "PRAGMA journal_mode=WAL":
            raise error

    connection.execute.side_effect = execute
    with pytest.raises(sqlite3.OperationalError) as caught:
        sqlite_sql.configure_sqlite_connection(connection)
    assert caught.value is error
    assert clock.now == pytest.approx(5.0)
    assert sum(clock.waits) == pytest.approx(5.0)
    timeouts = [
        int(call.args[0].split("=")[1])
        for call in connection.execute.call_args_list
        if call.args[0].startswith("PRAGMA busy_timeout=")
    ]
    assert timeouts[0] == timeouts[-1] == 5000
    assert all(0 <= value < 5000 for value in timeouts[1:-1])
    assert timeouts[1:-1] == sorted(timeouts[1:-1], reverse=True)
    assert not any(
        call.args[0].startswith("PRAGMA synchronous=")
        for call in connection.execute.call_args_list
    )


def test_busy_synchronous_configuration_is_not_retried(clock):
    connection = Mock(spec=sqlite3.Connection)
    error = failure(sqlite3.SQLITE_BUSY)
    connection.execute.side_effect = [None, None, error]
    with pytest.raises(sqlite3.OperationalError) as caught:
        sqlite_sql.configure_sqlite_connection(connection)
    assert caught.value is error
    assert connection.execute.call_count == 3
    assert clock.waits == []


def test_independent_prepared_builders_recover_after_real_initial_wal_contention(
    saved, monkeypatch
):
    snapshot, store, source = saved
    original = source.read_bytes()
    catalog = store.directory / "binary/catalog.sqlite3"
    catalog.parent.mkdir(mode=0o700)
    # У свежего каталога ещё нет ни WAL, ни таблицы доверенных регистраций.
    blocker = sqlite3.connect(catalog, check_same_thread=False)
    blocker.execute("CREATE TABLE initialization_witness (id INTEGER)")
    blocker.commit()
    assert blocker.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    blocker.execute("BEGIN IMMEDIATE")
    initializers = Barrier(2)
    condition = Condition()
    observed_busy = []
    original_connect = sqlite3.connect

    class ObserveInitialWal(sqlite3.Connection):
        first_wal = True

        def execute(self, sql, *args, **kwargs):
            if sql == "PRAGMA journal_mode=WAL" and self.first_wal:
                self.first_wal = False
                initializers.wait(timeout=5)
                try:
                    return super().execute(sql, *args, **kwargs)
                except sqlite3.OperationalError as error:
                    with condition:
                        observed_busy.append(error.sqlite_errorcode)
                        condition.notify_all()
                    raise
            return super().execute(sql, *args, **kwargs)

    def connect(database, *args, **kwargs):
        if database == catalog:
            kwargs["factory"] = ObserveInitialWal
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)

    def release_after_both_real_busy_errors():
        with condition:
            assert condition.wait_for(lambda: len(observed_busy) == 2, timeout=5)
        blocker.rollback()

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            release = pool.submit(release_after_both_real_busy_errors)
            futures = [pool.submit(resource, saved) for _ in range(2)]
            first, second = [future.result() for future in futures]
            release.result()
    finally:
        blocker.close()
    assert observed_busy == [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_BUSY]
    assert first.registration == second.registration
    assert first.path == second.path
    first.assert_unchanged()
    second.assert_unchanged()
    assert source.read_bytes() == original
    assert not list(first.path.parent.glob("*.pending"))
    assert resource(saved).registration == first.registration
    assert first.registration.key == snapshot.key
    with original_connect(catalog) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert connection.execute(
            "SELECT COUNT(*) FROM prepared_binary_resources"
        ).fetchone() == (1,)
