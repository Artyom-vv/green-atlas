from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json
import sqlite3
from threading import RLock

from app.projects.contracts import Project
from app.projects.source_contracts import SourceContentInfo
from app.projects.concurrency import ProjectVersionConflict, advance_expected_project_version, assert_project_version, expected_project_version


EMPTY_FEATURE_COLLECTION = {"type": "FeatureCollection", "features": []}


def _mutation_receipt_payload(project: Project, receipt: dict) -> str:
    return json.dumps({**receipt, "project_id": project.id, "state_version": project.state_version,
        "geometry_version": project.geometry_version, "plan_version": project.plan.version if project.plan else None},
        ensure_ascii=False, allow_nan=False)


def project_projection(project: Project) -> Project:
    geometry = project.geometry.model_copy(update={"feature_collection": EMPTY_FEATURE_COLLECTION}) if project.geometry else None
    return project.model_copy(update={"source_geometry": None, "geometry": geometry})


class InMemoryProjectRepository:
    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._sources: dict[str, bytes] = {}
        self._exports: dict[tuple[str, str], bytes] = {}
        self._releases: dict[tuple[str, str], str] = {}
        self._mutation_receipts: dict[tuple[str, str, str], str] = {}
        self._lock = RLock()

    def create(self, project: Project) -> Project:
        with self._lock:
            now = datetime.now(UTC).isoformat()
            project.created_at = now
            project.updated_at = now
            project.state_version = 1
            self._projects[project.id] = project.model_copy(deep=True)
            return project.model_copy(deep=True)

    def get(self, project_id: str, *, lightweight: bool = False) -> Project:
        with self._lock:
            try:
                project = self._projects[project_id].model_copy(deep=True)
            except KeyError as error:
                raise KeyError(f"Project {project_id} not found") from error
            assert_project_version(project_id, project.state_version)
            return project_projection(project) if lightweight else project

    def save(self, project: Project, *, source: bytes | bytearray | None = None) -> Project:
        with self._lock:
            if project.id not in self._projects:
                raise KeyError(f"Project {project.id} not found")
            current = self._projects[project.id].state_version
            expected = expected_project_version() or project.state_version or current
            if expected != current:
                raise ProjectVersionConflict(project.id, expected, current)
            project.state_version = current + 1
            project.updated_at = datetime.now(UTC).isoformat()
            self._projects[project.id] = project.model_copy(deep=True)
            if source is not None:
                # Production SQLite copies a BLOB as part of the transaction.
                # The in-memory adapter must preserve the same immutable
                # source-of-truth contract when the HTTP collector supplies a
                # mutable bytearray.
                self._sources[project.id] = bytes(source)
            advance_expected_project_version(project.state_version)
            return project.model_copy(deep=True)

    def save_with_receipt(self, project: Project, kind: str, mutation_id: str, receipt: dict) -> Project:
        with self._lock:
            key = (project.id, kind, mutation_id)
            if key in self._mutation_receipts:
                raise ValueError("Квитанция изменения уже существует")
            prospective = project.model_copy(deep=True)
            prospective.state_version = self._projects[project.id].state_version + 1
            encoded = _mutation_receipt_payload(prospective, receipt)
            saved = self.save(project)
            self._mutation_receipts[key] = encoded
            return saved

    def mutation_receipt(self, project_id: str, kind: str, mutation_id: str) -> dict | None:
        with self._lock:
            encoded = self._mutation_receipts.get((project_id, kind, mutation_id))
            return json.loads(encoded) if encoded else None

    def list(self, *, lightweight: bool = False) -> list[Project]:
        projects = sorted((item.model_copy(deep=True) for item in self._projects.values()), key=lambda item: item.updated_at, reverse=True)
        return [project_projection(item) for item in projects] if lightweight else projects

    def save_many(self, projects: list[Project]) -> list[Project]:
        return [self.save(project) for project in projects]

    def delete(self, project_id: str, *, expected_version: int | None = None) -> None:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(f"Project {project_id} not found")
            current = self._projects[project_id].state_version
            expected = expected_version or expected_project_version()
            if expected is not None and expected != current:
                raise ProjectVersionConflict(project_id, expected, current)
            del self._projects[project_id]
            self._sources.pop(project_id, None)
            for key in [key for key in self._exports if key[0] == project_id]:
                del self._exports[key]
            for key in [key for key in self._releases if key[0] == project_id]:
                del self._releases[key]
            for key in [key for key in self._mutation_receipts if key[0] == project_id]:
                del self._mutation_receipts[key]

    def save_source(self, project_id: str, content: bytes | bytearray) -> None:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(f"Project {project_id} not found")
            self._sources[project_id] = bytes(content)

    def get_source(self, project_id: str) -> bytes | None:
        with self._lock:
            return self._sources.get(project_id)

    def get_source_info(self, project_id: str, *, prefix_bytes: int) -> SourceContentInfo | None:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(f"Project {project_id} not found")
            content = self._sources.get(project_id)
            return SourceContentInfo(len(content), content[:prefix_bytes]) if content is not None else None

    def save_export(self, project_id: str, artifact_id: str, content: bytes) -> None:
        with self._lock:
            if project_id not in self._projects:
                raise KeyError(f"Project {project_id} not found")
            key = (project_id, artifact_id)
            if key in self._exports:
                raise ValueError("Файл экспорта уже существует")
            self._exports[key] = content

    def publish_export(self, project: Project, artifact_id: str, content: bytes) -> Project:
        with self._lock:
            if project.id not in self._projects:
                raise KeyError(f"Project {project.id} not found")
            current = self._projects[project.id].state_version
            expected = expected_project_version() or project.state_version or current
            if expected != current:
                raise ProjectVersionConflict(project.id, expected, current)
            key = (project.id, artifact_id)
            if key in self._exports:
                raise ValueError("Файл экспорта уже существует")
            project.state_version = current + 1
            project.updated_at = datetime.now(UTC).isoformat()
            self._projects[project.id] = project.model_copy(deep=True)
            self._exports[key] = content
            advance_expected_project_version(project.state_version)
            return project.model_copy(deep=True)

    def get_export(self, project_id: str, artifact_id: str) -> bytes | None:
        with self._lock:
            return self._exports.get((project_id, artifact_id))

    def publish_release(self, project: Project, release_id: str, payload: str, artifacts: dict[str, bytes]) -> Project:
        with self._lock:
            if project.id not in self._projects:
                raise KeyError(f"Project {project.id} not found")
            release_key = (project.id, release_id)
            if release_key in self._releases:
                raise ValueError("Выпуск уже существует")
            current = self._projects[project.id].state_version
            expected = expected_project_version() or project.state_version or current
            if expected != current:
                raise ProjectVersionConflict(project.id, expected, current)
            project.state_version = current + 1
            project.updated_at = datetime.now(UTC).isoformat()
            self._projects[project.id] = project.model_copy(deep=True)
            self._releases[release_key] = payload
            for artifact_id, content in artifacts.items():
                self._exports[(project.id, artifact_id)] = bytes(content)
            advance_expected_project_version(project.state_version)
            return project.model_copy(deep=True)

    def get_release(self, project_id: str, release_id: str) -> str | None:
        with self._lock:
            return self._releases.get((project_id, release_id))


class SqliteProjectRepository:
    """Small durable store: project metadata, compact projection, original DXF."""

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            # Separate API workers use separate SQLite connections. A short
            # wait lets the losing writer observe the committed state-version
            # and receive the normal 409 conflict instead of a transient
            # ``database is locked`` error while the winner commits.
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, payload TEXT NOT NULL, projection TEXT, source BLOB, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, state_version INTEGER NOT NULL DEFAULT 1)")
            self._connection.execute("CREATE TABLE IF NOT EXISTS project_exports (project_id TEXT NOT NULL, artifact_id TEXT NOT NULL, content BLOB NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project_id, artifact_id))")
            self._connection.execute("CREATE TABLE IF NOT EXISTS project_releases (project_id TEXT NOT NULL, release_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (project_id, release_id))")
            self._connection.execute("CREATE TABLE IF NOT EXISTS project_mutation_receipts (project_id TEXT NOT NULL, kind TEXT NOT NULL, mutation_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(project_id, kind, mutation_id))")
            self._connection.execute("CREATE INDEX IF NOT EXISTS idx_project_exports_project ON project_exports (project_id, created_at DESC)")
            columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(projects)").fetchall()}
            if "projection" not in columns:
                self._connection.execute("ALTER TABLE projects ADD COLUMN projection TEXT")
            if "state_version" not in columns:
                self._connection.execute("ALTER TABLE projects ADD COLUMN state_version INTEGER NOT NULL DEFAULT 1")

    @staticmethod
    def _payloads(project: Project) -> tuple[str, str]:
        return project.model_dump_json(), project_projection(project).model_dump_json()

    def create(self, project: Project) -> Project:
        now = datetime.now(UTC).isoformat()
        project.created_at = now
        project.updated_at = now
        project.state_version = 1
        payload, projection = self._payloads(project)
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO projects (id, payload, projection, created_at, updated_at, state_version) VALUES (?, ?, ?, ?, ?, ?)", (project.id, payload, projection, now, now, 1))
        return project

    def _read(self, project_id: str, *, lightweight: bool) -> Project:
        field = "projection" if lightweight else "payload"
        with self._lock:
            row = self._connection.execute(f"SELECT {field} AS payload, state_version FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(f"Project {project_id} not found")
        project = Project.model_validate_json(row["payload"])
        project.state_version = int(row["state_version"])
        assert_project_version(project_id, project.state_version)
        return project

    def get(self, project_id: str, *, lightweight: bool = False) -> Project:
        return self._read(project_id, lightweight=lightweight)

    def list(self, *, lightweight: bool = False) -> list[Project]:
        field = "projection" if lightweight else "payload"
        with self._lock:
            rows = self._connection.execute(f"SELECT {field} AS payload, state_version FROM projects ORDER BY updated_at DESC").fetchall()
        projects = []
        for row in rows:
            project = Project.model_validate_json(row["payload"])
            project.state_version = int(row["state_version"])
            projects.append(project)
        return projects

    def save(self, project: Project, *, source: bytes | bytearray | None = None) -> Project:
        original = project.state_version
        expected = expected_project_version() or original or 1
        project.state_version = expected + 1
        project.updated_at = datetime.now(UTC).isoformat()
        payload, projection = self._payloads(project)
        with self._lock, self._connection:
            if source is None:
                cursor = self._connection.execute("UPDATE projects SET payload=?, projection=?, updated_at=?, state_version=? WHERE id=? AND state_version=?", (payload, projection, project.updated_at, project.state_version, project.id, expected))
            else:
                cursor = self._connection.execute("UPDATE projects SET payload=?, projection=?, source=?, updated_at=?, state_version=? WHERE id=? AND state_version=?", (payload, projection, source, project.updated_at, project.state_version, project.id, expected))
        if cursor.rowcount == 0:
            project.state_version = original
            try:
                current = self._read(project.id, lightweight=True).state_version
            except KeyError:
                raise KeyError(f"Project {project.id} not found") from None
            raise ProjectVersionConflict(project.id, expected, current)
        advance_expected_project_version(project.state_version)
        return project

    def save_many(self, projects: list[Project]) -> list[Project]:
        return [self.save(project) for project in projects]

    def save_with_receipt(self, project: Project, kind: str, mutation_id: str, receipt: dict) -> Project:
        """Persist the project and exact mutation receipt in one transaction."""
        original_version, original_updated_at = project.state_version, project.updated_at
        expected = expected_project_version() or original_version or 1
        project.state_version = expected + 1
        project.updated_at = datetime.now(UTC).isoformat()
        try:
            payload, projection = self._payloads(project)
            encoded = _mutation_receipt_payload(project, receipt)
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    "UPDATE projects SET payload=?, projection=?, updated_at=?, state_version=? WHERE id=? AND state_version=?",
                    (payload, projection, project.updated_at, project.state_version, project.id, expected))
                if cursor.rowcount == 0:
                    row = self._connection.execute("SELECT state_version FROM projects WHERE id=?", (project.id,)).fetchone()
                    if row is None:
                        raise KeyError(f"Project {project.id} not found")
                    raise ProjectVersionConflict(project.id, expected, int(row["state_version"]))
                self._connection.execute("INSERT INTO project_mutation_receipts VALUES (?, ?, ?, ?)",
                    (project.id, kind, mutation_id, encoded))
        except Exception:
            project.state_version, project.updated_at = original_version, original_updated_at
            raise
        advance_expected_project_version(project.state_version)
        return project

    def mutation_receipt(self, project_id: str, kind: str, mutation_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM project_mutation_receipts WHERE project_id=? AND kind=? AND mutation_id=?",
                (project_id, kind, mutation_id)).fetchone()
            return json.loads(row["payload"]) if row else None

    def delete(self, project_id: str, *, expected_version: int | None = None) -> None:
        expected = expected_version or expected_project_version()
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM projects WHERE id = ?" if expected is None else "DELETE FROM projects WHERE id = ? AND state_version = ?", (project_id,) if expected is None else (project_id, expected))
            if cursor.rowcount:
                self._connection.execute("DELETE FROM project_exports WHERE project_id = ?", (project_id,))
                self._connection.execute("DELETE FROM project_releases WHERE project_id = ?", (project_id,))
                self._connection.execute("DELETE FROM project_mutation_receipts WHERE project_id = ?", (project_id,))
        if cursor.rowcount:
            return
        try:
            current = self._read(project_id, lightweight=True).state_version
        except KeyError:
            raise KeyError(f"Project {project_id} not found") from None
        raise ProjectVersionConflict(project_id, expected or 1, current)

    def save_source(self, project_id: str, content: bytes | bytearray) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute("UPDATE projects SET source=? WHERE id=?", (content, project_id))
        if cursor.rowcount == 0:
            raise KeyError(f"Project {project_id} not found")

    def get_source(self, project_id: str) -> bytes | None:
        with self._lock:
            row = self._connection.execute("SELECT source FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(f"Project {project_id} not found")
        return bytes(row["source"]) if row["source"] is not None else None

    def get_source_info(self, project_id: str, *, prefix_bytes: int) -> SourceContentInfo | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT length(source) AS size, substr(source, 1, ?) AS prefix FROM projects WHERE id=?",
                (prefix_bytes, project_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Project {project_id} not found")
        return SourceContentInfo(row["size"], bytes(row["prefix"])) if row["size"] is not None else None

    def save_export(self, project_id: str, artifact_id: str, content: bytes) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connection:
            exists = self._connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
            if exists is None:
                raise KeyError(f"Project {project_id} not found")
            try:
                self._connection.execute(
                    "INSERT INTO project_exports (project_id, artifact_id, content, created_at) VALUES (?, ?, ?, ?)",
                    (project_id, artifact_id, content, now),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError("Файл экспорта уже существует") from error

    def publish_export(self, project: Project, artifact_id: str, content: bytes) -> Project:
        """Commit export bytes and the current project revision together.

        The artefact must never outlive a failed state transition. SQLite's
        transaction covers both tables, while the version predicate makes a
        concurrent project edit fail before either value becomes visible.
        """

        original_version = project.state_version
        original_updated_at = project.updated_at
        expected = expected_project_version() or original_version or 1
        project.state_version = expected + 1
        project.updated_at = datetime.now(UTC).isoformat()
        payload, projection = self._payloads(project)
        conflict = False
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    "UPDATE projects SET payload=?, projection=?, updated_at=?, state_version=? WHERE id=? AND state_version=?",
                    (payload, projection, project.updated_at, project.state_version, project.id, expected),
                )
                if cursor.rowcount == 0:
                    conflict = True
                else:
                    self._connection.execute(
                        "INSERT INTO project_exports (project_id, artifact_id, content, created_at) VALUES (?, ?, ?, ?)",
                        (project.id, artifact_id, content, project.updated_at),
                    )
        except sqlite3.IntegrityError as error:
            project.state_version = original_version
            project.updated_at = original_updated_at
            raise ValueError("Файл экспорта уже существует") from error
        if conflict:
            project.state_version = original_version
            project.updated_at = original_updated_at
            try:
                current = self._read(project.id, lightweight=True).state_version
            except KeyError:
                raise KeyError(f"Project {project.id} not found") from None
            raise ProjectVersionConflict(project.id, expected, current)
        advance_expected_project_version(project.state_version)
        return project

    def get_export(self, project_id: str, artifact_id: str) -> bytes | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT content FROM project_exports WHERE project_id = ? AND artifact_id = ?",
                (project_id, artifact_id),
            ).fetchone()
        return bytes(row["content"]) if row is not None else None

    def publish_release(self, project: Project, release_id: str, payload: str, artifacts: dict[str, bytes]) -> Project:
        original_version = project.state_version
        original_updated_at = project.updated_at
        expected = expected_project_version() or original_version or 1
        project.state_version = expected + 1
        project.updated_at = datetime.now(UTC).isoformat()
        stored_payload, projection = self._payloads(project)
        conflict = False
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    "UPDATE projects SET payload=?, projection=?, updated_at=?, state_version=? WHERE id=? AND state_version=?",
                    (stored_payload, projection, project.updated_at, project.state_version, project.id, expected),
                )
                if cursor.rowcount == 0:
                    conflict = True
                else:
                    self._connection.execute(
                        "INSERT INTO project_releases (project_id, release_id, payload, created_at) VALUES (?, ?, ?, ?)",
                        (project.id, release_id, payload, project.updated_at),
                    )
                    for artifact_id, content in artifacts.items():
                        self._connection.execute(
                            "INSERT INTO project_exports (project_id, artifact_id, content, created_at) VALUES (?, ?, ?, ?)",
                            (project.id, artifact_id, content, project.updated_at),
                        )
        except sqlite3.IntegrityError as error:
            project.state_version = original_version
            project.updated_at = original_updated_at
            raise ValueError("Выпуск уже существует") from error
        if conflict:
            project.state_version = original_version
            project.updated_at = original_updated_at
            try:
                current = self._read(project.id, lightweight=True).state_version
            except KeyError:
                raise KeyError(f"Project {project.id} not found") from None
            raise ProjectVersionConflict(project.id, expected, current)
        advance_expected_project_version(project.state_version)
        return project

    def get_release(self, project_id: str, release_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM project_releases WHERE project_id = ? AND release_id = ?",
                (project_id, release_id),
            ).fetchone()
        return str(row["payload"]) if row is not None else None
