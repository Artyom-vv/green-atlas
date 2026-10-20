"""Ownership and durable deletion of generated project resources.

The database commits the deletion before files are touched. The remaining queue
is deliberately small and can be retried after a crash or a Windows sharing
violation. Original drawing paths and user archives are never accepted here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import stat
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock, local
from uuid import UUID

from app.desktop.tickets import direct_path, read_regular
from app.shared.failures import ProjectNotFound

_LOGGER = logging.getLogger(__name__)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COMPONENT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,239}\Z")
_METADATA_LIMIT = 1024 * 1024

RESOURCE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS project_resource_references ("
    "project_id TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, "
    "PRIMARY KEY(project_id, kind, key))",
    "CREATE INDEX IF NOT EXISTS idx_project_resource_reference "
    "ON project_resource_references(kind, key, project_id)",
    "CREATE TABLE IF NOT EXISTS project_resource_cleanup ("
    "project_id TEXT NOT NULL PRIMARY KEY, created_at TEXT NOT NULL, "
    "attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT)",
    "CREATE TABLE IF NOT EXISTS project_resource_deleted ("
    "project_id TEXT NOT NULL PRIMARY KEY, deleted_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS project_resource_locations ("
    "kind TEXT NOT NULL PRIMARY KEY, path TEXT NOT NULL)",
)


class ProjectCleanupPending(ProjectNotFound):
    """A retired project identity cannot be recreated by an old recovery intent."""


class _BusyResource(Exception):
    pass


def _project_hash(project_id: str) -> str:
    return sha256(project_id.encode()).hexdigest()


def _uuid(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _windows_file_lock(
    descriptor: int,
    *,
    exclusive: bool = False,
    blocking: bool = True,
    unlock: bool = False,
) -> None:
    """LockFileEx supports shared parent/child worker leases, unlike msvcrt.locking."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(descriptor))
    overlapped = Overlapped()
    if unlock:
        operation = kernel.UnlockFileEx
        operation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        ]
        operation.restype = wintypes.BOOL
        success = operation(handle, 0, 1, 0, ctypes.byref(overlapped))
    else:
        operation = kernel.LockFileEx
        operation.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(Overlapped),
        ]
        operation.restype = wintypes.BOOL
        flags = (2 if exclusive else 0) | (0 if blocking else 1)
        success = operation(handle, flags, 0, 1, 0, ctypes.byref(overlapped))
    if not success:
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        if error in {32, 33} and not blocking:
            raise _BusyResource
        raise ctypes.WinError(error)  # type: ignore[attr-defined]


def _lock(descriptor: int, *, exclusive: bool, blocking: bool) -> None:
    if os.name == "nt":
        _windows_file_lock(descriptor, exclusive=exclusive, blocking=blocking)
    else:
        import fcntl

        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(descriptor, mode | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as error:
            raise _BusyResource from error


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        _windows_file_lock(descriptor, unlock=True)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


class ProjectResourceLifecycle:
    """Small ownership registry sharing the project's SQLite writer boundary.

    ``use`` covers a worker's complete lifetime without holding a SQL lock;
    ``publication`` covers only its final filesystem publication. Every writer
    must register its deterministic generated key before publishing the file.
    ``drain`` never waits for a running worker and leaves its tombstone intact.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        database_path: str,
        transaction: Callable[[], AbstractContextManager[None]],
    ) -> None:
        self.connection = connection
        self.database_path = database_path
        self.transaction = transaction
        self.defer_cleanup = False
        self._local = local()
        self._activity_lock = Lock()
        self._active_users = 0
        self._roots: dict[str, Path] = {}
        if database_path != ":memory:":
            base = str(Path(database_path).absolute())
            self._roots = {
                "geometry": Path(base + ".geometry"),
                "display": Path(base + ".display-geometry"),
                "cad-archive": Path(base + ".cad-archives"),
                "prepared": Path(base + ".prepared-geometry"),
                "prepared-pointer": Path(base + ".prepared-geometry"),
                "prepared-binary": Path(base + ".prepared-geometry") / "binary",
                "domain": Path(base + ".prepared-geometry") / "domains",
                "recognition": Path(base + ".layer-recognition"),
                "render-job": Path(base + ".renders"),
                "render-preparation": Path(base + ".renders") / "preparations",
                "scene-view": Path(base + ".renders") / "scene-views",
                "preview-cache": Path(base + ".renders") / "preview-cache",
                "render-georeference": Path(base + ".renders") / "georeferences",
                "render-location": Path(base + ".renders") / "locations",
                "asset-bundle": Path(base + ".renders") / "assets" / "bundles",
                "asset-blob": Path(base + ".renders") / "assets" / "blobs",
                "cad-preparation-job": Path(base + ".cad-preparation-jobs"),
                "cad-export-job": Path(base + ".cad-release-jobs"),
                "cad-query-job": Path(base + ".cad-release-jobs"),
            }
            self._lease_root = Path(base + ".resource-locks")
        else:
            self._lease_root = None

    def initialize(self) -> None:
        for sql in RESOURCE_SCHEMA:
            self.connection.execute(sql)
        self.connection.execute(
            "INSERT OR IGNORE INTO project_resource_deleted(project_id,deleted_at) "
            "SELECT project_id,created_at FROM project_resource_cleanup"
        )
        for kind, path in self.connection.execute(
            "SELECT kind,path FROM project_resource_locations"
        ):
            try:
                self._roots[kind] = self._external_root(kind, Path(path))
            except ValueError:
                self._roots.pop(kind, None)

    @property
    def has_active_users(self) -> bool:
        with self._activity_lock:
            return self._active_users > 0

    @staticmethod
    def _external_root(kind: str, root: Path) -> Path:
        if kind not in {
            "transfer",
            "handoff",
            "cad-preparation-job",
            "cad-export-job",
            "cad-query-job",
        }:
            raise ValueError("Unknown external generated resource kind")
        root = root.absolute()
        if (
            root == root.parent
            or root == Path.home()
            or any(
                part in {".git", ".local", "preserved-user-data"} for part in root.parts
            )
        ):
            raise ValueError("Protected root cannot contain disposable resources")
        return root

    def configure_root(self, kind: str, root: Path) -> None:
        """Persist a trusted staging root so a restart retries the same targets."""
        root = self._external_root(kind, root)
        # A configured transfer root can be created by its owner later.  Check
        # its existing prefix without requiring the final directory yet.
        direct_path(root, root.parent)
        with self.transaction():
            recorded = self.connection.execute(
                "SELECT path FROM project_resource_locations WHERE kind=?", (kind,)
            ).fetchone()
            previous = Path(recorded[0]) if recorded else self._roots.get(kind)
            if (
                previous is not None
                and previous != root
                and self.connection.execute(
                    "SELECT 1 FROM project_resource_references WHERE kind=? LIMIT 1",
                    (kind,),
                ).fetchone()
            ):
                raise ValueError(
                    "Generated resource root changed while references still exist"
                )
            self.connection.execute(
                "INSERT INTO project_resource_locations(kind,path) VALUES (?,?) "
                "ON CONFLICT(kind) DO UPDATE SET path=excluded.path",
                (kind, str(root)),
            )
        self._roots[kind] = root

    @staticmethod
    def _validate(kind: str, key: str) -> None:
        if not isinstance(key, str) or not _COMPONENT.fullmatch(key):
            raise ValueError("Invalid generated resource key")
        if kind in {
            "geometry",
            "display",
            "prepared",
            "prepared-pointer",
            "prepared-binary",
            "domain",
            "recognition",
            "scene-view",
            "preview-cache",
            "asset-bundle",
            "asset-blob",
        }:
            if not _SHA.fullmatch(key):
                raise ValueError("Generated resource needs a SHA256 key")
        elif kind in {"cad-archive", "render-job", "render-preparation"}:
            if not _uuid(key):
                raise ValueError("Generated resource needs a canonical UUID key")
        elif kind not in {
            "render-georeference",
            "render-location",
            "cad-preparation-job",
            "cad-export-job",
            "cad-query-job",
            "transfer",
            "handoff",
        }:
            raise ValueError("Unknown generated resource kind")

    def assert_creatable(self, project_id: str) -> None:
        if self.connection.execute(
            "SELECT 1 FROM project_resource_deleted WHERE project_id=?", (project_id,)
        ).fetchone():
            raise ProjectCleanupPending(project_id)

    @contextmanager
    def creation(self, project_id: str) -> Iterator[None]:
        """Cover staging as well as INSERT; old recovery intents cannot recreate it."""
        with self.reservation(project_id):
            with self.transaction():
                if self.connection.execute(
                    "SELECT 1 FROM projects WHERE id=?", (project_id,)
                ).fetchone():
                    raise sqlite3.IntegrityError("Project identifier already exists")
            yield

    @contextmanager
    def reservation(self, project_id: str) -> Iterator[None]:
        """Keep a recoverable import's files before the project row is published."""
        outer = project_id not in getattr(self._local, "leases", {})
        try:
            with self._lease(project_id, exclusive=False, blocking=True):
                with self.transaction():
                    self.assert_creatable(project_id)
                yield
        finally:
            if outer and not self.connection.in_transaction:
                self.drain(project_id)

    def _assert_live(self, project_id: str) -> None:
        if not self.connection.execute(
            "SELECT 1 FROM projects WHERE id=?", (project_id,)
        ).fetchone():
            raise ProjectNotFound(project_id)
        self.assert_creatable(project_id)

    @contextmanager
    def _lease(
        self, project_id: str, *, exclusive: bool, blocking: bool
    ) -> Iterator[None]:
        if self._lease_root is None:
            yield
            return
        active = getattr(self._local, "leases", None)
        if active is None:
            active = self._local.leases = {}
        if project_id in active:
            if exclusive:
                raise _BusyResource
            yield
            return
        if os.name == "nt":
            # direct_path validates an existing root through Win32 handles.
            # A new project's lease directory must be created by the same
            # no-reparse adapter before that validation.
            from app.native_query.transport.windows_job_files import prepare_directory

            prepare_directory(self._lease_root)
        root = direct_path(self._lease_root, self._lease_root)
        if os.name != "nt":
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = direct_path(root / f"{_project_hash(project_id)}.lock", root)
        descriptor = os.open(
            path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        locked = False
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Resource lease is not a regular file")
            _lock(descriptor, exclusive=exclusive, blocking=blocking)
            locked = True
            actual = os.fstat(descriptor)
            expected = path.stat(follow_symlinks=False)
            if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError("Resource lease was replaced")
            active[project_id] = descriptor
            with self._activity_lock:
                self._active_users += 1
            try:
                yield
            finally:
                with self._activity_lock:
                    self._active_users -= 1
                active.pop(project_id, None)
        finally:
            if locked:
                _unlock(descriptor)
            os.close(descriptor)

    @contextmanager
    def use(self, project_id: str) -> Iterator[None]:
        """A running task keeps its files until it acknowledges deletion/cancellation."""
        outer = project_id not in getattr(self._local, "leases", {})
        try:
            with self._lease(project_id, exclusive=False, blocking=True):
                if outer:
                    with self.transaction():
                        self._assert_live(project_id)
                yield
        finally:
            if outer and not self.connection.in_transaction:
                self.drain(project_id)

    @contextmanager
    def publication(self, project_id: str) -> Iterator[None]:
        with self.use(project_id), self.transaction():
            self._assert_live(project_id)
            yield

    def register(self, project_id: str, kind: str, key: str) -> None:
        self._validate(kind, key)
        if self.database_path != ":memory:" and kind not in self._roots:
            raise ValueError("Generated resource root has not been configured")
        with self.publication(project_id):
            self._register(project_id, kind, key)

    def _register(self, project_id: str, kind: str, key: str) -> None:
        self._validate(kind, key)
        self.connection.execute(
            "INSERT OR IGNORE INTO project_resource_references(project_id,kind,key) "
            "VALUES (?,?,?)",
            (project_id, kind, key),
        )

    def enqueue_delete(self, project_id: str) -> None:
        """Called in the same transaction as a successful project DELETE."""
        digest = _project_hash(project_id)
        for kind in ("geometry", "display", "prepared-pointer"):
            self._register(project_id, kind, digest)
        if _uuid(project_id):
            self._register(project_id, "cad-archive", project_id)
        if _COMPONENT.fullmatch(project_id):
            for kind in ("render-georeference", "render-location"):
                self._register(project_id, kind, project_id)
        self._discover_legacy(project_id)
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            "INSERT INTO project_resource_deleted(project_id,deleted_at) VALUES (?,?)",
            (project_id, now),
        )
        self.connection.execute(
            "INSERT INTO project_resource_cleanup(project_id,created_at) VALUES (?,?)",
            (project_id, now),
        )

    @staticmethod
    def _small_json(path: Path, root: Path) -> dict | None:
        try:
            with read_regular(direct_path(path, root)) as stream:
                value = stream.read(_METADATA_LIMIT + 1)
            if len(value) > _METADATA_LIMIT:
                return None
            result = json.loads(value)
            return result if isinstance(result, dict) else None
        except (OSError, ValueError):
            return None

    def _discover_legacy(self, project_id: str) -> None:
        # Only small manifests are inspected. Never load scene/geometry payloads
        # or derive a deletion target from original_path/source_file metadata.
        for kind, manifest in (
            ("render-job", "job.json"),
            ("render-preparation", "manifest.json"),
            ("preview-cache", "manifest.json"),
            ("cad-preparation-job", "request.json"),
            ("cad-export-job", "request.json"),
            ("cad-query-job", "request.json"),
        ):
            root = self._roots.get(kind)
            if root is None:
                continue
            try:
                direct_path(root, root)
                entries = list(root.iterdir()) if root.is_dir() else []
            except (OSError, ValueError):
                continue
            for directory in entries:
                key = directory.name
                if kind == "render-preparation" and key.endswith(".pending"):
                    key = key.removesuffix(".pending")
                try:
                    self._validate(kind, key)
                except ValueError:
                    continue
                value = self._small_json(directory / manifest, root)
                if value is None and kind == "render-job":
                    value = self._small_json(directory / "job.pending", root)
                if value and value.get("project_id") == project_id:
                    self._register(project_id, kind, key)
        root = self._roots.get("prepared-pointer")
        if root is not None:
            # Bind legacy current pointers for every live project before dropping
            # one reference: the snapshot can be shared by restored projects.
            owners = {
                row[0] for row in self.connection.execute("SELECT id FROM projects")
            }
            owners.add(project_id)
            for owner in owners:
                value = self._small_json(
                    root / f"project-{_project_hash(owner)}.json", root
                )
                if not value or not isinstance(value.get("payload"), str):
                    continue
                payload = value["payload"]
                if sha256(payload.encode()).hexdigest() != value.get("sha256"):
                    continue
                try:
                    key = json.loads(payload).get("geometry_key")
                    if isinstance(key, str) and _SHA.fullmatch(key):
                        self._register(owner, "prepared", key)
                except (ValueError, AttributeError):
                    continue

    def _paths(self, kind: str, key: str) -> list[Path]:
        self._validate(kind, key)
        root = self._roots.get(kind)
        if root is None:
            if self.database_path == ":memory:":
                return []
            raise ValueError("Generated resource root has not been configured")
        if kind == "prepared-pointer":
            names = [f"project-{key}.json"]
        elif kind == "prepared":
            names = [
                f"{key}.json",
                f"binary/{key}.json",
                f"derived/coverage/{key}.json",
            ]
        elif kind == "prepared-binary":
            names = [
                f"{key}.sqlite3" + suffix for suffix in ("", "-wal", "-shm", "-journal")
            ]
        elif kind in {"domain", "render-georeference"}:
            names = [f"{key}.json"]
        elif kind == "recognition":
            names = [f"{key}.json", f"{key}.progress.json"]
        elif kind == "render-preparation":
            names = [key, f"{key}.pending"]
        elif kind in {"asset-blob", "scene-view"}:
            names = (
                [f"{key}.json", f"{key}.pending"]
                if kind == "scene-view"
                else [key, f"{key}.pending"]
            )
        else:
            names = [key]
        paths = [direct_path(root / name, root) for name in names]
        if kind in {"prepared", "domain"}:
            directories = [root, root / "binary"] if kind == "prepared" else [root]
            for directory in directories:
                try:
                    directory.lstat()
                except FileNotFoundError:
                    continue
                direct_path(directory, root)
                if directory.is_dir():
                    for pattern in (f".{key}.*.pending", f"{key}.*.damaged"):
                        paths.extend(
                            direct_path(path, root) for path in directory.glob(pattern)
                        )
        elif kind == "preview-cache" and root.is_dir():
            for pattern in (f"{key}.pending-*", f"{key}.invalid-*"):
                paths.extend(direct_path(path, root) for path in root.glob(pattern))
        return paths

    @staticmethod
    def _remove(path: Path) -> None:
        """Reject links/reparse points before a symlink-safe recursive removal."""
        direct_path(path, path.parent)
        try:
            info = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Refusing to clean a linked generated resource")
        if stat.S_ISREG(info.st_mode):
            path.unlink()
            return
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Generated resource is not a regular file or directory")
        for directory, names, files in os.walk(path, followlinks=False):
            for name in (*names, *files):
                item = Path(directory) / name
                mode = item.lstat()
                if (
                    stat.S_ISLNK(mode.st_mode)
                    or getattr(mode, "st_file_attributes", 0) & 0x400
                    or not (stat.S_ISDIR(mode.st_mode) or stat.S_ISREG(mode.st_mode))
                ):
                    raise ValueError(
                        "Generated resource contains a link or special file"
                    )
        actual = path.lstat()
        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("Generated resource was replaced during cleanup")
        # shutil.rmtree uses descriptor-relative traversal where supported and
        # does not follow directory symlinks on Windows either.
        shutil.rmtree(path)

    def _shared(self, kind: str, key: str, owner: str) -> bool:
        # Two configured worker kinds may deliberately use the same job root.
        aliases = {
            candidate
            for candidate, root in self._roots.items()
            if root == self._roots.get(kind)
            and candidate
            in {
                "cad-preparation-job",
                "cad-export-job",
                "cad-query-job",
                "transfer",
                "handoff",
            }
        } | {kind}
        return (
            self.connection.execute(
                "SELECT 1 FROM project_resource_references r "
                "WHERE r.project_id<>? AND r.kind IN ("
                + ",".join("?" for _ in aliases)
                + ") AND r.key=? LIMIT 1",
                (owner, *sorted(aliases), key),
            ).fetchone()
            is not None
        )

    def _remove_resource(self, kind: str, key: str, owner: str) -> None:
        if self._shared(kind, key, owner):
            return
        extra: list[Path] = []
        if kind == "prepared":
            records = self.connection.execute(
                "SELECT DISTINCT sha256 FROM prepared_binary_resources WHERE key=?",
                (key,),
            ).fetchall()
            for (digest,) in records:
                if not self.connection.execute(
                    "SELECT 1 FROM prepared_binary_resources WHERE sha256=? AND key<>? LIMIT 1",
                    (digest, key),
                ).fetchone() and not self._shared("prepared-binary", digest, owner):
                    extra.extend(self._paths("prepared-binary", digest))
        elif (
            kind == "prepared-binary"
            and self.connection.execute(
                "SELECT 1 FROM prepared_binary_resources WHERE sha256=? LIMIT 1", (key,)
            ).fetchone()
        ):
            return
        for path in [*self._paths(kind, key), *extra]:
            self._remove(path)
        if kind == "prepared":
            self.connection.execute(
                "DELETE FROM prepared_binary_resources WHERE key=?", (key,)
            )

    def drain(self, project_id: str | None = None) -> int:
        """Retry committed deletions; busy workers/errors leave durable tombstones."""
        if self.connection.in_transaction:
            return 0
        with self.transaction():
            rows = self.connection.execute(
                "SELECT project_id FROM project_resource_cleanup"
                + (" WHERE project_id=?" if project_id is not None else ""),
                (project_id,) if project_id is not None else (),
            ).fetchall()
        completed = 0
        for (owner,) in rows:
            try:
                with (
                    self._lease(owner, exclusive=True, blocking=False),
                    self.transaction(),
                ):
                    if not self.connection.execute(
                        "SELECT 1 FROM project_resource_cleanup WHERE project_id=?",
                        (owner,),
                    ).fetchone():
                        continue
                    if self.connection.execute(
                        "SELECT 1 FROM projects WHERE id=?", (owner,)
                    ).fetchone():
                        continue
                    resources = self.connection.execute(
                        "SELECT kind,key FROM project_resource_references WHERE project_id=? ORDER BY kind,key",
                        (owner,),
                    ).fetchall()
                    for kind, key in resources:
                        self._remove_resource(kind, key, owner)
                    self.connection.execute(
                        "DELETE FROM project_resource_references WHERE project_id=?",
                        (owner,),
                    )
                    self.connection.execute(
                        "DELETE FROM project_resource_cleanup WHERE project_id=?",
                        (owner,),
                    )
                    completed += 1
            except _BusyResource:
                continue
            except (OSError, ValueError, sqlite3.Error) as error:
                with self.transaction():
                    self.connection.execute(
                        "UPDATE project_resource_cleanup SET attempts=attempts+1,last_error=? WHERE project_id=?",
                        (f"{type(error).__name__}: {str(error)[:500]}", owner),
                    )
                _LOGGER.warning(
                    "Project resource cleanup is pending for %s: %s",
                    owner,
                    type(error).__name__,
                )
        return completed
