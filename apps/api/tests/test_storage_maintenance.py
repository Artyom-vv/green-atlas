"""Temporary SQLite databases only; no installed Workspace or CAD inputs."""

import gzip
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest

from app.history.adapters import SqliteProjectHistory
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.projects.storage_maintenance import SqliteStorageMaintenance
from app.projects.workspace_backup import backup_workspace

_MIB = 1024**2


def _fixture(tmp_path: Path, *, big_bytes: int = 72 * _MIB):
    repository = SqliteProjectRepository(tmp_path / "workspace.sqlite3")
    deleted = repository.create(Project(name="synthetic large import"))
    neighbour = repository.create(Project(name="working neighbour"))
    repository.save_source(neighbour.id, b"original neighbouring source\x00\xff" * 1024)
    history = SqliteProjectHistory(repository)
    before = neighbour.model_copy(deep=True)
    neighbour.name = "working neighbour saved"
    history.commit(
        neighbour, before, "keep manual history", change_set_id="keep-change"
    )
    with repository._writer_snapshot():
        # Allocate a real SQLite BLOB without allocating a matching Python CAD graph.
        repository._connection.execute(
            "INSERT INTO project_sources(project_id,content) VALUES (?,zeroblob(?))",
            (deleted.id, big_bytes),
        )
    repository._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    return repository, deleted, neighbour


def _neighbour_state(connection: sqlite3.Connection, identifier: str) -> dict:
    return {
        "project": tuple(
            connection.execute(
                "SELECT * FROM projects WHERE id=?", (identifier,)
            ).fetchone()
        ),
        "source": sha256(
            connection.execute(
                "SELECT content FROM project_sources WHERE project_id=?", (identifier,)
            ).fetchone()[0]
        ).hexdigest(),
        "history": [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM plan_changes WHERE project_id=? ORDER BY ordinal",
                (identifier,),
            )
        ],
        "cursor": [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM plan_history_cursors WHERE project_id=?", (identifier,)
            )
        ],
    }


def _small_worker(repository: SqliteProjectRepository) -> SqliteStorageMaintenance:
    return SqliteStorageMaintenance(
        repository.path, min_reclaim_bytes=1, min_free_ratio=0, progress_steps=1
    )


def _traced(worker: SqliteStorageMaintenance, monkeypatch, callback):
    original = worker._connect

    def connect(*, readonly=False):
        connection = original(readonly=readonly)
        connection.set_trace_callback(callback)
        return connection

    monkeypatch.setattr(worker, "_connect", connect)


def test_large_delete_reclaims_file_bytes_and_preserves_neighbour_history_and_backup(
    tmp_path: Path,
):
    repository, deleted, neighbour = _fixture(tmp_path)
    with closing(repository):
        original = _neighbour_state(repository._connection, neighbour.id)
        database = Path(repository.path)
        before_main = database.stat().st_size
        assert before_main > 72 * _MIB
        repository.delete(deleted.id)
        after_delete = database.stat().st_size
        assert after_delete == before_main, (
            "DELETE alone must demonstrate the original freelist problem"
        )
        assert repository.storage_maintenance.pending()
        # Explicit test backup proves that maintenance keeps normal backup/restore
        # data and does not replace it with a stripped project projection.
        backup = backup_workspace(database, reason="synthetic-maintenance-control")
        manifest = json.loads((backup / "manifest.json").read_text())
        record = manifest["database"]
        encoded = (backup / record["path"]).read_bytes()
        restored = tmp_path / "restored.sqlite3"
        restored.write_bytes(
            gzip.decompress(encoded) if record["encoding"] == "gzip" else encoded
        )
        with closing(sqlite3.connect(restored)) as connection:
            assert _neighbour_state(connection, neighbour.id) == original
        start = monotonic()
        result = repository.storage_maintenance.run_once(idle=lambda: True)
        elapsed = monotonic() - start
        assert result.status == "completed"
        assert database.stat().st_size < _MIB
        assert _neighbour_state(repository._connection, neighbour.id) == original
        assert (
            repository._connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        )
        assert not repository.storage_maintenance.pending()
        print(
            "STORAGE_MEASUREMENT="
            + json.dumps(
                {
                    "before_main_bytes": before_main,
                    "after_delete_main_bytes": after_delete,
                    "after_maintenance_main_bytes": database.stat().st_size,
                    "maintenance_s": elapsed,
                    "receipt_before_db_wal_bytes": result.before_bytes,
                    "receipt_after_db_wal_bytes": result.after_bytes,
                    "neighbour_and_history_exact": True,
                    "backup_restore_exact": True,
                }
            )
        )


def test_last_project_reclaims_below_threshold_but_nonempty_small_freelist_is_reused(
    tmp_path: Path,
):
    repository, deleted, neighbour = _fixture(tmp_path, big_bytes=2 * _MIB)
    with closing(repository):
        repository.delete(deleted.id)
        result = repository.storage_maintenance.run_once(idle=lambda: True)
        assert result.status == "below_threshold"
        assert Path(repository.path).stat().st_size > 2 * _MIB
        assert not repository.storage_maintenance.pending()
        repository.delete(neighbour.id)
        result = repository.storage_maintenance.run_once(idle=lambda: True)
        assert result.status == "completed"
        assert Path(repository.path).stat().st_size < _MIB


def test_failed_delete_transaction_does_not_schedule_compaction(
    tmp_path: Path, monkeypatch
):
    with closing(SqliteProjectRepository(tmp_path / "workspace.sqlite3")) as repository:
        project = repository.create(Project(name="keep"))

        def failure(_):
            raise sqlite3.OperationalError("forced rollback")

        monkeypatch.setattr(repository._sources, "delete", failure)
        with pytest.raises(sqlite3.OperationalError):
            repository.delete(project.id)
        assert not repository.storage_maintenance.pending()
        assert repository.get(project.id, lightweight=True).name == "keep"


def test_open_reader_retains_pending_and_retries_after_reader_closes(tmp_path: Path):
    repository, deleted, neighbour = _fixture(tmp_path, big_bytes=4 * _MIB)
    with closing(repository), closing(sqlite3.connect(repository.path)) as reader:
        original = _neighbour_state(repository._connection, neighbour.id)
        reader.execute("BEGIN")
        reader.execute("SELECT id FROM projects").fetchall()
        repository.delete(deleted.id)
        worker = _small_worker(repository)
        assert worker.run_once(idle=lambda: True).status == "busy"
        assert worker.pending()
        assert reader.execute(
            "SELECT 1 FROM projects WHERE id=?", (deleted.id,)
        ).fetchone()
        reader.rollback()
        assert worker.run_once(idle=lambda: True).status == "completed"
        assert not worker.pending()
        assert _neighbour_state(repository._connection, neighbour.id) == original


def test_locked_writer_returns_immediately_and_keeps_durable_retry(tmp_path: Path):
    repository, deleted, _ = _fixture(tmp_path, big_bytes=4 * _MIB)
    with closing(repository), closing(sqlite3.connect(repository.path)) as writer:
        repository.delete(deleted.id)
        worker = _small_worker(repository)
        writer.execute("BEGIN IMMEDIATE")
        start = monotonic()
        assert worker.run_once(idle=lambda: True).status == "busy"
        assert monotonic() - start < 1
        assert worker.pending()
        writer.rollback()
        assert worker.run_once(idle=lambda: True).status == "completed"


def test_busy_checkpoint_after_vacuum_does_not_repeat_the_rebuild(
    tmp_path: Path, monkeypatch
):
    repository, deleted, neighbour = _fixture(tmp_path, big_bytes=4 * _MIB)
    with closing(repository), closing(sqlite3.connect(repository.path)) as reader:
        original = _neighbour_state(repository._connection, neighbour.id)
        repository.delete(deleted.id)
        worker = _small_worker(repository)
        statements = []
        _traced(worker, monkeypatch, statements.append)
        enough_space = worker._enough_space

        def pin_reader(connection):
            result = enough_space(connection)
            reader.execute("BEGIN")
            reader.execute("SELECT id FROM projects").fetchall()
            return result

        monkeypatch.setattr(worker, "_enough_space", pin_reader)
        assert worker.run_once(idle=lambda: True).status == "busy"
        assert worker.pending()
        assert repository._connection.execute(
            "SELECT generation,vacuumed_generation FROM project_storage_maintenance"
        ).fetchone()[:] == (1, 1)
        reader.rollback()
        assert worker.run_once(idle=lambda: True).status == "completed"
        assert statements.count("VACUUM main") == 1
        assert _neighbour_state(repository._connection, neighbour.id) == original


def test_new_delete_between_compaction_and_ack_is_not_lost(tmp_path: Path, monkeypatch):
    repository, deleted, _ = _fixture(tmp_path, big_bytes=4 * _MIB)
    with closing(repository):
        repository.delete(deleted.id)
        worker = _small_worker(repository)
        ack = worker._ack
        first = True

        def raced_ack(connection, generation):
            nonlocal first
            if first:
                first = False
                late = repository.create(Project(name="deleted during old ACK"))
                repository.delete(late.id)
            ack(connection, generation)

        monkeypatch.setattr(worker, "_ack", raced_ack)
        assert worker.run_once(idle=lambda: True).status == "completed"
        assert worker.pending()
        assert (
            repository._connection.execute(
                "SELECT generation FROM project_storage_maintenance"
            ).fetchone()[0]
            == 2
        )
        assert worker.run_once(idle=lambda: True).status in {
            "completed",
            "below_threshold",
        }
        assert not worker.pending()


def test_insufficient_space_and_foreground_activity_defer_without_touching_data(
    tmp_path: Path, monkeypatch
):
    repository, deleted, neighbour = _fixture(tmp_path, big_bytes=4 * _MIB)
    with closing(repository):
        repository.delete(deleted.id)
        original = _neighbour_state(repository._connection, neighbour.id)
        worker = _small_worker(repository)
        assert worker.run_once(idle=lambda: False).status == "deferred"
        monkeypatch.setattr(worker, "_enough_space", lambda _: False)
        assert worker.run_once(idle=lambda: True).status == "insufficient_space"
        assert worker.pending()
        assert _neighbour_state(repository._connection, neighbour.id) == original


def test_new_work_interrupts_an_in_progress_vacuum_and_retry_preserves_neighbour(
    tmp_path: Path, monkeypatch
):
    repository, deleted, neighbour = _fixture(tmp_path, big_bytes=8 * _MIB)
    with closing(repository):
        repository.delete(deleted.id)
        original = _neighbour_state(repository._connection, neighbour.id)
        worker = _small_worker(repository)
        vacuum = Event()
        _traced(
            worker,
            monkeypatch,
            lambda sql: vacuum.set() if sql == "VACUUM main" else None,
        )
        result = worker.run_once(idle=lambda: not vacuum.is_set())
        assert result.status == "deferred" and worker.pending()
        assert _neighbour_state(repository._connection, neighbour.id) == original
        assert worker.run_once(idle=lambda: True).status == "completed"


def test_cancel_current_interrupts_other_thread_without_holding_repository_lock(
    tmp_path: Path, monkeypatch
):
    repository, deleted, neighbour = _fixture(tmp_path, big_bytes=8 * _MIB)
    with closing(repository):
        repository.delete(deleted.id)
        worker = _small_worker(repository)
        vacuum, entered, resume = Event(), Event(), Event()
        _traced(
            worker,
            monkeypatch,
            lambda sql: vacuum.set() if sql == "VACUUM main" else None,
        )

        def idle():
            if vacuum.is_set():
                entered.set()
                assert resume.wait(5)
            return True

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.run_once, idle=idle)
            try:
                assert entered.wait(5)
                # This read proves that the worker does not hold repository._lock.
                assert repository.get(neighbour.id, lightweight=True).id == neighbour.id
                worker.cancel_current()
            finally:
                resume.set()
            assert future.result(timeout=5).status == "cancelled"
        assert worker.pending()
        assert worker.run_once(idle=lambda: True).status == "completed"


def test_process_crash_during_native_vacuum_keeps_queue_and_database_recoverable(
    tmp_path: Path,
):
    repository, deleted, neighbour = _fixture(tmp_path, big_bytes=8 * _MIB)
    script = """import os,sys
from app.projects.storage_maintenance import SqliteStorageMaintenance
worker=SqliteStorageMaintenance(sys.argv[1],min_reclaim_bytes=1,min_free_ratio=0,progress_steps=1)
connect=worker._connect
started=False
def trace(sql):
    global started
    if sql=='VACUUM main': started=True
def opened(*,readonly=False):
    connection=connect(readonly=readonly)
    connection.set_trace_callback(trace)
    return connection
worker._connect=opened
def idle():
    if started and worker._running_connection.in_transaction: os._exit(77)
    return True
worker.run_once(idle=idle)
raise SystemExit(78)
"""
    with closing(repository):
        repository.delete(deleted.id)
        original = _neighbour_state(repository._connection, neighbour.id)
        child = subprocess.run(
            [sys.executable, "-c", script, repository.path],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert child.returncode == 77, child.stderr
        assert repository.storage_maintenance.pending()
        assert _neighbour_state(repository._connection, neighbour.id) == original
        assert (
            repository._connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        )
        assert (
            _small_worker(repository).run_once(idle=lambda: True).status == "completed"
        )
        assert _neighbour_state(repository._connection, neighbour.id) == original


def test_activity_counter_tracks_real_outer_leases_and_nested_use(tmp_path: Path):
    with closing(SqliteProjectRepository(tmp_path / "workspace.sqlite3")) as repository:
        project = repository.create(Project(name="active"))
        lifecycle = repository.resource_lifecycle
        assert not lifecycle.has_active_users
        with lifecycle.use(project.id):
            assert lifecycle.has_active_users
            with lifecycle.publication(project.id):
                assert lifecycle.has_active_users
        assert not lifecycle.has_active_users


def test_absolute_threshold_reclaims_large_free_space_even_at_a_small_ratio(
    tmp_path: Path,
):
    repository, deleted, neighbour = _fixture(tmp_path, big_bytes=4 * _MIB)
    with closing(repository):
        with repository._writer_snapshot():
            repository._connection.execute(
                "UPDATE project_sources SET content=zeroblob(?) WHERE project_id=?",
                (32 * _MIB, neighbour.id),
            )
        repository._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        repository.delete(deleted.id)
        pages = repository._connection.execute("PRAGMA page_count").fetchone()[0]
        free = repository._connection.execute("PRAGMA freelist_count").fetchone()[0]
        assert free / pages < 0.20
        # Scale the absolute bound for this real SQLite fixture. The ordinary
        # 64 MiB/20% branch cannot trigger here; only the absolute bound can.
        worker = SqliteStorageMaintenance(
            repository.path, absolute_reclaim_bytes=3 * _MIB
        )
        before = Path(repository.path).stat().st_size
        assert worker.run_once(idle=lambda: True).status == "completed"
        assert Path(repository.path).stat().st_size < before - 3 * _MIB


def test_sqlite_tmpdir_headroom_is_checked_and_competing_owner_never_waits(
    tmp_path: Path, monkeypatch
):
    repository, deleted, _ = _fixture(tmp_path, big_bytes=4 * _MIB)
    with closing(repository):
        repository.delete(deleted.id)
        first, second = _small_worker(repository), _small_worker(repository)
        with first._owner():
            assert second.run_once(idle=lambda: True).status == "busy"
        temporary = tmp_path / "sqlite-tmp"
        temporary.mkdir()
        monkeypatch.setenv("SQLITE_TMPDIR", str(temporary))
        import app.projects.storage_maintenance as module

        disk_usage = module.shutil.disk_usage
        checked = []

        def usage(path):
            checked.append(Path(path))
            return (
                SimpleNamespace(free=0) if Path(path) == temporary else disk_usage(path)
            )

        monkeypatch.setattr(module.shutil, "disk_usage", usage)
        assert second.run_once(idle=lambda: True).status == "insufficient_space"
        assert temporary in checked and second.pending()


def test_progress_budget_and_unavailable_pending_read_leave_a_retry(
    tmp_path: Path, monkeypatch
):
    repository, deleted, _ = _fixture(tmp_path, big_bytes=4 * _MIB)
    with closing(repository):
        repository.delete(deleted.id)
        worker = SqliteStorageMaintenance(repository.path, max_duration_s=0)
        assert worker.run_once(idle=lambda: True).status == "deferred"
        assert worker.pending()
        worker.cancel_current()  # Safe when no private connection is running.

        def busy(*, readonly=False):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(worker, "_connect", busy)
        assert worker.pending()
