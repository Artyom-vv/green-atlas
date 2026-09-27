import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

from app.operations.contracts import (
    OperationError,
    OperationKind,
    OperationStatus,
    ProjectOperation,
)

ACTIVE_STATUSES = {
    OperationStatus.QUEUED,
    OperationStatus.RUNNING,
    OperationStatus.CANCELLING,
}


def interrupted(operation: ProjectOperation) -> ProjectOperation:
    now = datetime.now(UTC).isoformat()
    operation.status = OperationStatus.INTERRUPTED
    operation.stage = "Операция прервана перезапуском сервиса"
    operation.error = OperationError(
        code="OPERATION_INTERRUPTED",
        message="Сохранён последний подтверждённый прогресс. Запустите операцию повторно.",
    )
    operation.completed_at = now
    operation.updated_at = now
    return operation


class InMemoryOperationRepository:
    def __init__(self) -> None:
        self._operations: dict[str, ProjectOperation] = {}
        self._lock = RLock()

    def create(self, operation: ProjectOperation) -> ProjectOperation:
        with self._lock:
            active = next(
                (
                    item
                    for item in self._operations.values()
                    if item.project_id == operation.project_id
                    and item.kind == operation.kind
                    and item.status in ACTIVE_STATUSES
                ),
                None,
            )
            if active is not None:
                return active.model_copy(deep=True)
            if operation.id in self._operations:
                raise ValueError(f"Operation {operation.id} already exists")
            self._operations[operation.id] = operation
            return operation.model_copy(deep=True)

    def get(self, operation_id: str) -> ProjectOperation:
        with self._lock:
            try:
                return self._operations[operation_id].model_copy(deep=True)
            except KeyError as error:
                raise KeyError(f"Operation {operation_id} not found") from error

    def save(self, operation: ProjectOperation) -> ProjectOperation:
        with self._lock:
            self._operations[operation.id] = operation.model_copy(deep=True)
            return operation.model_copy(deep=True)

    def compare_and_save(
        self, expected: ProjectOperation, operation: ProjectOperation
    ) -> ProjectOperation | None:
        with self._lock:
            if self._operations.get(expected.id) != expected:
                return None
            if expected.id != operation.id:
                raise ValueError("Cannot change operation identity")
            return self.save(operation)

    def get_latest(
        self, project_id: str, kind: OperationKind
    ) -> ProjectOperation | None:
        with self._lock:
            matches = [
                item
                for item in self._operations.values()
                if item.project_id == project_id and item.kind == kind
            ]
            if not matches:
                return None
            return max(matches, key=lambda item: item.created_at).model_copy(deep=True)

    def recover_incomplete(self) -> list[ProjectOperation]:
        with self._lock:
            recovered = []
            for operation in self._operations.values():
                if operation.status in ACTIVE_STATUSES:
                    recovered.append(interrupted(operation))
            return [item.model_copy(deep=True) for item in recovered]


class SqliteOperationRepository:
    """Durable operation journal for the current API deployment."""

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __init__(self, path: str | Path, *, recover: bool = True) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            # The operation journal can be written by a web worker and a
            # background worker at the same time. Prefer its durable active
            # operation constraint to a transient SQLite lock failure.
            self._connection.execute("PRAGMA busy_timeout=5000")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_operations (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_project_operations_latest ON project_operations (project_id, kind, created_at DESC)"
            )
            # Earlier single-process versions had no database guard against
            # two workers accepting the same click simultaneously. Preserve
            # the newest active record if a legacy database contains such a
            # pair, then make the invariant durable for every later writer.
            self._collapse_duplicate_active_operations()
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_project_operations_one_active
                ON project_operations (project_id, kind)
                WHERE status IN ('queued', 'running', 'cancelling')
                """
            )
        if recover:
            self.recover_incomplete()

    def _collapse_duplicate_active_operations(self) -> None:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        rows = self._connection.execute(
            f"""
            SELECT payload FROM project_operations
            WHERE status IN ({placeholders})
            ORDER BY project_id, kind, created_at DESC
            """,
            tuple(status.value for status in ACTIVE_STATUSES),
        ).fetchall()
        seen: set[tuple[str, OperationKind]] = set()
        for row in rows:
            operation = ProjectOperation.model_validate_json(row["payload"])
            key = (operation.project_id, operation.kind)
            if key not in seen:
                seen.add(key)
                continue
            stopped = interrupted(operation)
            self._connection.execute(
                "UPDATE project_operations SET status = ?, payload = ?, updated_at = ? WHERE id = ?",
                (
                    stopped.status.value,
                    stopped.model_dump_json(),
                    stopped.updated_at,
                    stopped.id,
                ),
            )

    def _active(self, project_id: str, kind: OperationKind) -> ProjectOperation | None:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        row = self._connection.execute(
            f"""
            SELECT payload FROM project_operations
            WHERE project_id = ? AND kind = ? AND status IN ({placeholders})
            ORDER BY created_at DESC LIMIT 1
            """,
            (project_id, kind.value, *(status.value for status in ACTIVE_STATUSES)),
        ).fetchone()
        return ProjectOperation.model_validate_json(row["payload"]) if row else None

    def create(self, operation: ProjectOperation) -> ProjectOperation:
        try:
            with self._lock, self._connection:
                active = self._active(operation.project_id, operation.kind)
                if active is not None:
                    return active
                self._connection.execute(
                    "INSERT INTO project_operations (id, project_id, kind, status, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation.id,
                        operation.project_id,
                        operation.kind.value,
                        operation.status.value,
                        operation.model_dump_json(),
                        operation.created_at,
                        operation.updated_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            # Another application worker can cross the read/create boundary.
            # The partial unique index makes that race return the same active
            # operation instead of creating two calculations for one map.
            with self._lock:
                active = self._active(operation.project_id, operation.kind)
            if active is not None:
                return active
            raise ValueError(f"Operation {operation.id} already exists") from error
        return operation.model_copy(deep=True)

    def get(self, operation_id: str) -> ProjectOperation:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM project_operations WHERE id = ?", (operation_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Operation {operation_id} not found")
        return ProjectOperation.model_validate_json(row["payload"])

    def save(self, operation: ProjectOperation) -> ProjectOperation:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE project_operations SET status = ?, payload = ?, updated_at = ? WHERE id = ?",
                (
                    operation.status.value,
                    operation.model_dump_json(),
                    operation.updated_at,
                    operation.id,
                ),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Operation {operation.id} not found")
        return operation.model_copy(deep=True)

    def get_latest(
        self, project_id: str, kind: OperationKind
    ) -> ProjectOperation | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM project_operations WHERE project_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
                (project_id, kind.value),
            ).fetchone()
        return ProjectOperation.model_validate_json(row["payload"]) if row else None

    def compare_and_save(
        self, expected: ProjectOperation, operation: ProjectOperation
    ) -> ProjectOperation | None:
        if expected.id != operation.id:
            raise ValueError("Cannot change operation identity")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT payload FROM project_operations WHERE id = ?", (expected.id,)
            ).fetchone()
            if (
                row is None
                or ProjectOperation.model_validate_json(row["payload"]) != expected
            ):
                return None
            cursor = self._connection.execute(
                """UPDATE project_operations SET status = ?, payload = ?, updated_at = ?
                WHERE id = ? AND payload = ?""",
                (
                    operation.status.value,
                    operation.model_dump_json(),
                    operation.updated_at,
                    operation.id,
                    row["payload"],
                ),
            )
        return operation.model_copy(deep=True) if cursor.rowcount else None

    def recover_incomplete(self) -> list[ProjectOperation]:
        with self._lock, self._connection:
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            rows = self._connection.execute(
                f"SELECT payload FROM project_operations WHERE status IN ({placeholders})",
                tuple(status.value for status in ACTIVE_STATUSES),
            ).fetchall()
            recovered = []
            for row in rows:
                while row is not None:
                    current = ProjectOperation.model_validate_json(row["payload"])
                    if current.status not in ACTIVE_STATUSES:
                        break
                    operation = interrupted(current)
                    # A worker can publish after recovery reads active records.
                    # Its committed receipt must win over this stale snapshot.
                    updated = self._connection.execute(
                        "UPDATE project_operations SET status = ?, payload = ?, updated_at = ? WHERE id = ? AND payload = ?",
                        (
                            operation.status.value,
                            operation.model_dump_json(),
                            operation.updated_at,
                            operation.id,
                            row["payload"],
                        ),
                    )
                    if updated.rowcount:
                        recovered.append(operation)
                        break
                    # A changed active progress record still needs recovery;
                    # a newly terminal receipt must remain untouched.
                    row = self._connection.execute(
                        "SELECT payload FROM project_operations WHERE id = ?",
                        (operation.id,),
                    ).fetchone()
        return [item.model_copy(deep=True) for item in recovered]
