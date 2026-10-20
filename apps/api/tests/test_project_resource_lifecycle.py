"""Synthetic projects only: deletion must survive failures without losing neighbours."""

import ctypes
import json
import os
import select
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.history.adapters import SqliteProjectHistory
from app.native_query.search.domain_checkpoint import DomainCheckpointStore
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.projects.resource_lifecycle import (
    ProjectCleanupPending,
    ProjectResourceLifecycle,
    _BusyResource,
    _windows_file_lock,
)
from app.shared.failures import ProjectNotFound


def _file(path: Path, value: str = "generated") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    return path


def _geometry_path(repository: SqliteProjectRepository, project: Project) -> Path:
    return (
        Path(repository.path + ".geometry")
        / sha256(project.id.encode()).hexdigest()
        / "graph.json.gz"
    )


def _pending(repository: SqliteProjectRepository) -> list:
    return repository._connection.execute(
        "SELECT project_id,attempts,last_error FROM project_resource_cleanup"
    ).fetchall()


def test_failed_project_transaction_retains_project_and_every_file(
    tmp_path: Path, monkeypatch
) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project = repository.create(Project(name="keep"))
        source = _file(_geometry_path(repository, project))
        release = _file(
            Path(repository.path + ".cad-archives")
            / project.id
            / "capture"
            / "Drawing.dwg"
        )

        def fail(_project_id: str) -> None:
            raise sqlite3.OperationalError("forced transaction rollback")

        monkeypatch.setattr(repository._sources, "delete", fail)
        with pytest.raises(sqlite3.OperationalError, match="forced"):
            repository.delete(project.id)
        assert repository.get(project.id, lightweight=True).name == "keep"
        assert source.exists() and release.exists()
        assert not _pending(repository)
        assert (
            repository._connection.execute(
                "SELECT count(*) FROM project_resource_deleted"
            ).fetchone()[0]
            == 0
        )


def test_outer_rollback_never_removes_files(tmp_path: Path) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project = repository.create(Project(name="keep"))
        source = _file(_geometry_path(repository, project))
        with pytest.raises(RuntimeError, match="abort"):
            with repository._writer_snapshot():
                repository.delete(project.id)
                assert source.exists()
                raise RuntimeError("abort outer transaction")
        assert repository.get(project.id, lightweight=True).id == project.id
        assert source.exists() and not _pending(repository)


def test_delete_removes_owned_resources_and_keeps_sources_neighbours_and_shared(
    tmp_path: Path, monkeypatch
) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        first = repository.create(Project(name="delete"))
        second = repository.create(Project(name="working"))
        first_geometry = _file(_geometry_path(repository, first))
        second_geometry = _file(_geometry_path(repository, second))
        original = _file(tmp_path / "originals" / "Original.dwg", "human original")
        preserved = _file(
            tmp_path / ".local" / "preserved-user-data" / "Drawing.dwg",
            "human recovery",
        )
        lifecycle = repository.resource_lifecycle
        own_key, common_key, digest = "a" * 64, "b" * 64, "c" * 64
        own = _file(Path(repository.path + ".prepared-geometry") / f"{own_key}.json")
        common = _file(
            Path(repository.path + ".prepared-geometry") / f"{common_key}.json"
        )
        binary = _file(
            Path(repository.path + ".prepared-geometry")
            / "binary"
            / f"{digest}.sqlite3"
        )
        archive = _file(
            Path(repository.path + ".cad-archives")
            / first.id
            / "capture"
            / "Drawing.dwg"
        )
        job_id = str(uuid4())
        render = _file(Path(repository.path + ".renders") / job_id / "large-render.png")
        # Legacy job ownership comes from a small manifest, never the large scene.
        _file(render.parent / "job.json", json.dumps({"project_id": first.id}))
        for owner, key in (
            (first.id, own_key),
            (first.id, common_key),
            (second.id, common_key),
        ):
            lifecycle.register(owner, "prepared", key)
        with repository._writer_snapshot():
            repository._connection.execute(
                "INSERT INTO prepared_binary_resources VALUES (?,?,?,?,?)",
                (own_key, "d" * 64, "e" * 64, digest, 9),
            )
        # No public read/hydration is needed for deletion.
        monkeypatch.setattr(
            repository,
            "_read",
            Mock(side_effect=AssertionError("geometry must not be loaded")),
        )
        repository.delete(first.id)
        assert not first_geometry.exists() and not own.exists() and not binary.exists()
        assert not archive.exists() and not render.exists()
        assert second_geometry.exists() and common.exists()
        assert (
            original.read_text() == "human original"
            and preserved.read_text() == "human recovery"
        )
        assert not _pending(repository)
        repository.delete(second.id)
        assert not common.exists()


def test_legacy_current_prepared_pointer_preserves_a_shared_snapshot(
    tmp_path: Path,
) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        first, second = (
            repository.create(Project(name=name)) for name in ("first", "second")
        )
        key = "a" * 64
        store = DomainCheckpointStore(Path(repository.path + ".prepared-geometry"))
        for project in (first, second):
            store.save(
                "project-" + sha256(project.id.encode()).hexdigest(),
                {"geometry_key": key},
            )
        snapshot = _file(store.directory / f"{key}.json", "must not parse a large map")
        repository.delete(first.id)
        assert snapshot.exists()
        repository.delete(second.id)
        assert not snapshot.exists()


def test_filesystem_failure_is_durable_and_restart_retries(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "data.sqlite3"
    with closing(SqliteProjectRepository(path)) as repository:
        project = repository.create(Project(name="delete"))
        source = _file(_geometry_path(repository, project))
        with monkeypatch.context() as patch:
            patch.setattr(
                ProjectResourceLifecycle,
                "_remove",
                staticmethod(Mock(side_effect=PermissionError("busy file"))),
            )
            repository.delete(project.id)
        assert source.exists()
        assert len(_pending(repository)) == 1 and _pending(repository)[0][1] == 1
        with pytest.raises(ProjectNotFound):
            repository.get(project.id, lightweight=True)
    with closing(SqliteProjectRepository(path)) as restarted:
        assert restarted.resource_lifecycle.drain() == 1
        assert not source.exists() and not _pending(restarted)
        # Old handoff retries must never resurrect the deleted identity.
        with pytest.raises(ProjectCleanupPending):
            restarted.create(Project(id=project.id, name="late old retry"))


@pytest.mark.parametrize(
    "key", ["../originals", "/tmp/originals", "a/b", "a\\b", "..", ".", "a:stream"]
)
def test_registration_rejects_traversal_before_filesystem_access(
    tmp_path: Path, key: str
) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project = repository.create(Project(name="keep"))
        with pytest.raises(ValueError):
            repository.resource_lifecycle.register(project.id, "transfer", key)
        assert (
            repository._connection.execute(
                "SELECT count(*) FROM project_resource_references"
            ).fetchone()[0]
            == 0
        )


def test_symlink_inside_owned_directory_is_fail_closed(tmp_path: Path) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project = repository.create(Project(name="delete"))
        source = _file(_geometry_path(repository, project))
        protected = _file(tmp_path / "originals" / "Original.dwg", "original")
        link = source.parent / "xref"
        link.symlink_to(protected.parent, target_is_directory=True)
        repository.delete(project.id)
        assert (
            protected.read_text() == "original"
            and source.exists()
            and link.is_symlink()
        )
        assert len(_pending(repository)) == 1
        link.unlink()
        assert repository.resource_lifecycle.drain() == 1
        assert not source.exists() and protected.exists()


def test_generated_root_symlink_cannot_escape_to_originals(tmp_path: Path) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project = repository.create(Project(name="delete"))
        root = Path(repository.path + ".cad-archives")
        root.mkdir(exist_ok=True)
        root.rmdir()
        protected = _file(
            tmp_path / "originals" / project.id / "Original.dwg", "original"
        )
        root.symlink_to(protected.parent.parent, target_is_directory=True)
        repository.delete(project.id)
        assert protected.read_text() == "original" and root.is_symlink()
        assert len(_pending(repository)) == 1


def test_active_worker_blocks_cleanup_and_late_publication_then_finally_retries(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data.sqlite3"
    with (
        closing(SqliteProjectRepository(path)) as repository,
        closing(SqliteProjectRepository(path)) as worker_repository,
    ):
        project = repository.create(Project(name="delete"))
        source = _file(_geometry_path(repository, project))
        entered, deleted = Event(), Event()

        def worker() -> None:
            with worker_repository.resource_lifecycle.use(project.id):
                # Nested publication must not deadlock on Windows either.
                with worker_repository.resource_lifecycle.publication(project.id):
                    worker_repository.resource_lifecycle.register(
                        project.id, "domain", "a" * 64
                    )
                entered.set()
                assert deleted.wait(5)
                with pytest.raises(ProjectNotFound):
                    with worker_repository.resource_lifecycle.publication(project.id):
                        raise AssertionError("deleted worker cannot publish")
                assert source.exists()

        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(worker)
            assert entered.wait(5)
            repository.delete(project.id)
            assert source.exists() and len(_pending(repository)) == 1
            with pytest.raises(ProjectCleanupPending):
                repository.create(Project(id=project.id, name="racing old retry"))
            deleted.set()
            running.result(timeout=5)
        assert not source.exists() and not _pending(repository)


def test_domain_write_after_deletion_does_not_recreate_files(tmp_path: Path) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project = repository.create(Project(name="delete"))
        store = DomainCheckpointStore(
            Path(repository.path + ".prepared-geometry") / "domains",
            resource_lifecycle=repository.resource_lifecycle,
            project_id=project.id,
            resource_kind="domain",
        )
        key = "a" * 64
        store.save(key, {"checkpoint": 1})
        repository.delete(project.id)
        assert not (store.directory / f"{key}.json").exists()
        with pytest.raises(ProjectNotFound):
            store.save(key, {"checkpoint": 2})
        assert not (store.directory / f"{key}.json").exists()


def test_shared_resource_is_retained_for_an_already_deleted_active_worker(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data.sqlite3"
    with (
        closing(SqliteProjectRepository(path)) as repository,
        closing(SqliteProjectRepository(path)) as worker_repository,
    ):
        first, second = (
            repository.create(Project(name=name)) for name in ("first", "second")
        )
        key = "a" * 64
        for owner in (first.id, second.id):
            repository.resource_lifecycle.register(owner, "prepared", key)
        resource = _file(Path(repository.path + ".prepared-geometry") / f"{key}.json")
        entered, resume = Event(), Event()

        def worker() -> None:
            with worker_repository.resource_lifecycle.use(first.id):
                entered.set()
                assert resume.wait(5)
                assert resource.exists()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            try:
                assert entered.wait(5)
                repository.delete(first.id)
                repository.delete(second.id)
                assert resource.exists(), (
                    "deleted worker still holds the shared snapshot"
                )
                assert len(_pending(repository)) == 1
            finally:
                resume.set()
            future.result(timeout=5)
        assert not resource.exists() and not _pending(repository)


def test_explicit_transfer_root_survives_restart_and_cannot_be_retargeted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "data.sqlite3"
    root, other_root = tmp_path / "Transfers", tmp_path / "OtherTransfers"
    with closing(SqliteProjectRepository(path)) as repository:
        project = repository.create(Project(name="delete"))
        lifecycle = repository.resource_lifecycle
        lifecycle.configure_root("transfer", root)
        lifecycle.register(project.id, "transfer", "safe-staging")
        transfer = _file(root / "safe-staging" / "Drawing.dwg")
        neighbour = _file(root / "other-staging" / "Drawing.dwg")
        with pytest.raises(ValueError, match="root changed"):
            lifecycle.configure_root("transfer", other_root)
        lifecycle.defer_cleanup = True
        repository.delete(project.id)
    with closing(SqliteProjectRepository(path)) as restarted:
        assert restarted.resource_lifecycle.drain() == 1
        assert not transfer.exists() and neighbour.exists()


def test_prepublication_reservation_keeps_recoverable_import_without_empty_project(
    tmp_path: Path,
) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project_id = str(uuid4())
        with repository.resource_lifecycle.reservation(project_id):
            with pytest.raises(ProjectNotFound):
                repository.get(project_id, lightweight=True)
            archive = _file(
                Path(repository.path + ".cad-archives") / project_id / "Drawing.dwg"
            )
            project = repository.create(
                Project(id=project_id, name="publish after capture")
            )
        assert archive.exists() and project.id == project_id
        repository.delete(project_id)
        assert not archive.exists()
        with pytest.raises(ProjectCleanupPending):
            with repository.resource_lifecycle.reservation(project_id):
                raise AssertionError("old recovery intent cannot run again")


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX child lease integration; Win32 API mocked separately",
)
def test_parent_and_child_shared_leases_do_not_deadlock_and_child_crash_is_retryable(
    tmp_path: Path,
) -> None:
    script = """import sys
from app.projects.adapters import SqliteProjectRepository
with SqliteProjectRepository(sys.argv[1]).resource_lifecycle.use(sys.argv[2]):
    print('entered', flush=True)
    sys.stdin.readline()
"""
    # The process is deliberately killed to exercise OS lease release. It reads
    # only this synthetic database, never a user's Workspace or CAD application.
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project = repository.create(Project(name="delete"))
        source = _file(_geometry_path(repository, project))
        child = None
        try:
            with repository.resource_lifecycle.use(project.id):
                child = subprocess.Popen(
                    [sys.executable, "-c", script, repository.path, project.id],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=Path(__file__).resolve().parents[1],
                )
                assert select.select([child.stdout], [], [], 5)[0], (
                    "child shared lease was blocked"
                )
                assert child.stdout.readline().strip() == "entered"
                repository.delete(project.id)
                assert source.exists() and _pending(repository)
            assert source.exists(), (
                "child still owns resources after parent releases its lease"
            )
            child.kill()
            child.communicate(timeout=5)
            assert repository.resource_lifecycle.drain() == 1
            assert not source.exists()
        finally:
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


def test_win32_shared_worker_lock_and_nonblocking_exclusive_cleanup_flags(
    monkeypatch,
) -> None:
    lock, unlock = Mock(return_value=1), Mock(return_value=1)
    kernel = SimpleNamespace(LockFileEx=lock, UnlockFileEx=unlock)
    monkeypatch.setitem(
        sys.modules,
        "msvcrt",
        SimpleNamespace(get_osfhandle=lambda descriptor: descriptor + 100),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    _windows_file_lock(3, exclusive=False, blocking=True)
    assert lock.call_args.args[1:5] == (0, 0, 1, 0)
    _windows_file_lock(3, exclusive=True, blocking=False)
    assert lock.call_args.args[1:5] == (3, 0, 1, 0)
    _windows_file_lock(3, unlock=True)
    assert unlock.call_args.args[1:4] == (0, 1, 0)
    lock.return_value = 0
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 33, raising=False)
    with pytest.raises(_BusyResource):
        _windows_file_lock(3, exclusive=True, blocking=False)


def test_delete_history_is_in_the_same_commit_and_keeps_other_project_history(
    tmp_path: Path, monkeypatch
) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        first, second = (
            repository.create(Project(name=name)) for name in ("first", "second")
        )
        SqliteProjectHistory(repository)
        with repository._writer_snapshot():
            for owner in (first.id, second.id):
                repository._connection.execute(
                    "INSERT INTO plan_history_cursors VALUES (?,?)", (owner, 1)
                )
                repository._connection.execute(
                    "INSERT INTO plan_change_receipts VALUES (?,?,?,?)",
                    (owner, "change", "{}", "applied"),
                )
        # The filesystem drain is intentionally deferred, as it is in Runtime.
        repository.resource_lifecycle.defer_cleanup = True
        repository.delete(first.id)
        for table in ("plan_history_cursors", "plan_change_receipts"):
            assert [
                row[0]
                for row in repository._connection.execute(
                    f"SELECT project_id FROM {table}"
                )
            ] == [second.id]


def test_crash_intermediates_are_removed_only_for_registered_generated_keys(
    tmp_path: Path,
) -> None:
    with closing(SqliteProjectRepository(tmp_path / "data.sqlite3")) as repository:
        project = repository.create(Project(name="delete"))
        key, preparation_id = "a" * 64, str(uuid4())
        prepared = Path(repository.path + ".prepared-geometry")
        renders = Path(repository.path + ".renders")
        paths = [
            prepared / "derived" / "coverage" / f"{key}.json",
            prepared / f".{key}.old.pending",
            prepared / f"{key}.old.damaged",
            prepared / "binary" / f".{key}.old.pending",
            renders / "scene-views" / f"{key}.pending",
            renders / "assets" / "blobs" / f"{key}.pending",
            renders / "preparations" / f"{preparation_id}.pending" / "scene.json",
            renders / "preview-cache" / f"{key}.pending-{uuid4()}" / "mesh.glb",
            renders / "preview-cache" / f"{key}.invalid-{uuid4()}" / "mesh.glb",
            Path(repository.path + ".layer-recognition") / f"{key}.json",
            Path(repository.path + ".layer-recognition") / f"{key}.progress.json",
        ]
        for path in paths:
            _file(path)
        neighbour = _file(
            prepared / ("b" * 64 + ".json"), "unknown legacy file retained"
        )
        for kind in (
            "prepared",
            "scene-view",
            "asset-blob",
            "preview-cache",
            "recognition",
        ):
            repository.resource_lifecycle.register(project.id, kind, key)
        repository.resource_lifecycle.register(
            project.id, "render-preparation", preparation_id
        )
        repository.delete(project.id)
        assert all(not path.exists() for path in paths)
        assert neighbour.exists() and not _pending(repository)
