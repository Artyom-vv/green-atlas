from dataclasses import dataclass
from datetime import UTC, datetime
import json
from threading import RLock
from uuid import uuid4

from app.planning.contracts import Plan
from app.history.contracts import PlanHistoryEntry, PlanHistoryState
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project, ProjectStatus
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict, advance_expected_project_version, expected_project_version


@dataclass
class _Snapshot:
    label: str
    planting_zones: list[PlantingZoneAssignment]
    plan: Plan | None
    status: ProjectStatus
    id: str = ""
    created_at: str = ""


class InMemoryProjectHistory:
    """Bounded, per-project history of the editable project state."""

    def __init__(self, limit: int = 50) -> None:
        self.limit = limit
        self._undo: dict[str, list[_Snapshot]] = {}
        self._redo: dict[str, list[_Snapshot]] = {}
        self._lock = RLock()

    def _snapshot(self, project: Project, label: str) -> _Snapshot:
        # History begins only after the manual plan has been opened and is
        # cleared before every operation that can replace geometry (import,
        # recalculation or a new set of planting zones). Re-copying a large
        # DXF-derived feature collection for each tree click therefore adds
        # memory pressure without making any undo state more correct.
        return _Snapshot(
            label=label,
            planting_zones=[zone.model_copy(deep=True) for zone in project.planting_zones],
            plan=project.plan.model_copy(deep=True) if project.plan else None,
            status=project.status,
            id=str(uuid4()),
            created_at=datetime.now(UTC).isoformat(),
        )

    def _restore(self, project: Project, snapshot: _Snapshot) -> Project:
        if project.planting_zones != snapshot.planting_zones:
            raise ValueError("История плана относится к прежним участкам. Обновите историю перед отменой или повтором.")
        project.planting_zones = [zone.model_copy(deep=True) for zone in snapshot.planting_zones]
        project.plan = snapshot.plan.model_copy(deep=True) if snapshot.plan else None
        project.status = snapshot.status
        return project

    def clear(self, project_id: str) -> None:
        with self._lock:
            self._undo.pop(project_id, None)
            self._redo.pop(project_id, None)

    def rebase_planting_zones(self, project_id: str, zones: list[PlantingZoneAssignment]) -> None:
        """Keep plan undo/redo valid after an independently managed area rename."""
        with self._lock:
            for snapshot in [*self._undo.get(project_id, []), *self._redo.get(project_id, [])]:
                snapshot.planting_zones = [zone.model_copy(deep=True) for zone in zones]

    def record(self, project: Project, label: str) -> PlanHistoryState:
        with self._lock:
            undo = self._undo.setdefault(project.id, [])
            undo.append(self._snapshot(project, label))
            if len(undo) > self.limit:
                del undo[:-self.limit]
            self._redo.pop(project.id, None)
            return self.state(project.id)

    def state(self, project_id: str) -> PlanHistoryState:
        with self._lock:
            undo = self._undo.get(project_id, [])
            redo = self._redo.get(project_id, [])
            entries = [
                PlanHistoryEntry(id=item.id, ordinal=index + 1, label=item.label, created_at=item.created_at, applied=True)
                for index, item in enumerate(undo)
            ] + [
                PlanHistoryEntry(id=item.id, ordinal=len(undo) + index + 1, label=item.label, created_at=item.created_at, applied=False)
                for index, item in enumerate(reversed(redo))
            ]
            return PlanHistoryState(
                can_undo=bool(undo),
                can_redo=bool(redo),
                undo_label=undo[-1].label if undo else None,
                redo_label=redo[-1].label if redo else None,
                entries=list(reversed(entries)),
            )

    def undo(self, project: Project) -> Project:
        with self._lock:
            undo = self._undo.get(project.id, [])
            if not undo:
                raise ValueError("Нет изменений для отмены")
            snapshot = undo[-1]
            redo = self._redo.setdefault(project.id, [])
            current = self._snapshot(project, snapshot.label)
            current.id = snapshot.id
            current.created_at = snapshot.created_at
            restored = self._restore(project, snapshot)
            undo.pop()
            redo.append(current)
            return restored

    def redo(self, project: Project) -> Project:
        with self._lock:
            redo = self._redo.get(project.id, [])
            if not redo:
                raise ValueError("Нет изменений для повтора")
            snapshot = redo[-1]
            undo = self._undo.setdefault(project.id, [])
            current = self._snapshot(project, snapshot.label)
            current.id = snapshot.id
            current.created_at = snapshot.created_at
            restored = self._restore(project, snapshot)
            redo.pop()
            undo.append(current)
            return restored


class SqliteProjectHistory:
    """Durable plan history committed with the project in one transaction."""

    durable = True

    def __init__(self, repository: SqliteProjectRepository, limit: int = 50) -> None:
        self.repository = repository
        self.limit = limit
        self._lock: RLock = repository._lock
        self._connection = repository._connection
        with self._lock, self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_changes (
                    project_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    change_set_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    before_payload TEXT NOT NULL,
                    after_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, ordinal),
                    UNIQUE (project_id, change_set_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS plan_history_cursors (
                    project_id TEXT PRIMARY KEY,
                    cursor INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_plan_changes_project ON plan_changes (project_id, ordinal)"
            )
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS plan_change_receipts (
                    project_id TEXT NOT NULL, change_set_id TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL,
                    PRIMARY KEY(project_id, change_set_id)
                )
            """)

    def receipt(self, project_id: str, change_set_id: str) -> dict | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload, status FROM plan_change_receipts WHERE project_id=? AND change_set_id=?",
                (project_id, change_set_id)).fetchone()
            return {**json.loads(row["payload"]), "status": row["status"]} if row else None

    @staticmethod
    def _snapshot_payload(project: Project) -> str:
        return json.dumps(
            {
                "planting_zones": [zone.model_dump(mode="json") for zone in project.planting_zones],
                "plan": project.plan.model_dump(mode="json") if project.plan else None,
                "status": project.status.value,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _restore(project: Project, payload: str) -> Project:
        value = json.loads(payload)
        zones = [PlantingZoneAssignment.model_validate(item) for item in value["planting_zones"]]
        if project.planting_zones != zones:
            raise ValueError("История плана относится к прежним участкам. Обновите историю перед отменой или повтором.")
        project.planting_zones = zones
        project.plan = Plan.model_validate(value["plan"]) if value["plan"] is not None else None
        project.status = ProjectStatus(value["status"])
        return project

    def _cursor(self, project_id: str) -> int:
        row = self._connection.execute(
            "SELECT cursor FROM plan_history_cursors WHERE project_id = ?", (project_id,)
        ).fetchone()
        return int(row["cursor"]) if row is not None else 0

    def _save_project(self, project: Project) -> Project:
        original_version = project.state_version
        expected = expected_project_version() or original_version or 1
        row = self._connection.execute(
            "SELECT state_version FROM projects WHERE id = ?", (project.id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Project {project.id} not found")
        current = int(row["state_version"])
        if current != expected:
            raise ProjectVersionConflict(project.id, expected, current)
        project.state_version = expected + 1
        project.updated_at = datetime.now(UTC).isoformat()
        payload, projection = self.repository._payloads(project)
        cursor = self._connection.execute(
            "UPDATE projects SET payload=?, projection=?, updated_at=?, state_version=? WHERE id=? AND state_version=?",
            (payload, projection, project.updated_at, project.state_version, project.id, expected),
        )
        if cursor.rowcount == 0:
            latest = self._connection.execute(
                "SELECT state_version FROM projects WHERE id = ?", (project.id,)
            ).fetchone()
            if latest is None:
                raise KeyError(f"Project {project.id} not found")
            raise ProjectVersionConflict(project.id, expected, int(latest["state_version"]))
        return project

    def commit(
        self,
        project: Project,
        before: Project,
        label: str,
        change_set_id: str | None = None,
        receipt: dict | None = None,
    ) -> Project:
        original_version = project.state_version
        original_updated_at = project.updated_at
        try:
            with self._lock, self._connection:
                cursor = self._cursor(project.id)
                self._connection.execute(
                    "DELETE FROM plan_changes WHERE project_id = ? AND ordinal > ?",
                    (project.id, cursor),
                )
                saved = self._save_project(project)
                if receipt is not None:
                    if not change_set_id:
                        raise ValueError("A receipt requires a change set id")
                    payload = {**receipt, "state_version": saved.state_version,
                               "plan_version": saved.plan.version if saved.plan else None,
                               "committed_at": datetime.now(UTC).isoformat()}
                    self._connection.execute(
                        "INSERT INTO plan_change_receipts VALUES (?, ?, ?, 'applied')",
                        (project.id, change_set_id, json.dumps(payload, ensure_ascii=False, allow_nan=False)))
                ordinal = cursor + 1
                self._connection.execute(
                    """
                    INSERT INTO plan_changes (
                        project_id, ordinal, change_set_id, label,
                        before_payload, after_payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project.id,
                        ordinal,
                        change_set_id or str(uuid4()),
                        label,
                        self._snapshot_payload(before),
                        self._snapshot_payload(saved),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO plan_history_cursors (project_id, cursor) VALUES (?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET cursor=excluded.cursor
                    """,
                    (project.id, ordinal),
                )
                floor = max(0, ordinal - self.limit)
                if floor:
                    self._connection.execute(
                        "DELETE FROM plan_changes WHERE project_id = ? AND ordinal <= ?",
                        (project.id, floor),
                    )
            advance_expected_project_version(saved.state_version)
            return saved
        except Exception:
            project.state_version = original_version
            project.updated_at = original_updated_at
            raise

    def clear(self, project_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM plan_changes WHERE project_id = ?", (project_id,))
            self._connection.execute("DELETE FROM plan_history_cursors WHERE project_id = ?", (project_id,))

    def rebase_planting_zones(self, project_id: str, zones: list[PlantingZoneAssignment]) -> None:
        serialized = [zone.model_dump(mode="json") for zone in zones]
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT ordinal, before_payload, after_payload FROM plan_changes WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            for row in rows:
                before = json.loads(str(row["before_payload"]))
                after = json.loads(str(row["after_payload"]))
                before["planting_zones"] = serialized
                after["planting_zones"] = serialized
                self._connection.execute(
                    "UPDATE plan_changes SET before_payload = ?, after_payload = ? WHERE project_id = ? AND ordinal = ?",
                    (
                        json.dumps(before, ensure_ascii=False, separators=(",", ":")),
                        json.dumps(after, ensure_ascii=False, separators=(",", ":")),
                        project_id,
                        int(row["ordinal"]),
                    ),
                )

    def record(self, project: Project, label: str) -> PlanHistoryState:
        del project, label
        raise RuntimeError("SqliteProjectHistory.record must be committed through commit()")

    def state(self, project_id: str) -> PlanHistoryState:
        with self._lock:
            cursor = self._cursor(project_id)
            undo = self._connection.execute(
                "SELECT label FROM plan_changes WHERE project_id = ? AND ordinal = ?",
                (project_id, cursor),
            ).fetchone()
            redo = self._connection.execute(
                "SELECT label FROM plan_changes WHERE project_id = ? AND ordinal = ?",
                (project_id, cursor + 1),
            ).fetchone()
            rows = self._connection.execute(
                """
                SELECT ordinal, change_set_id, label, created_at
                FROM plan_changes
                WHERE project_id = ?
                ORDER BY ordinal DESC
                LIMIT 20
                """,
                (project_id,),
            ).fetchall()
        return PlanHistoryState(
            can_undo=undo is not None,
            can_redo=redo is not None,
            undo_label=str(undo["label"]) if undo is not None else None,
            redo_label=str(redo["label"]) if redo is not None else None,
            entries=[
                PlanHistoryEntry(
                    id=str(row["change_set_id"]),
                    ordinal=int(row["ordinal"]),
                    label=str(row["label"]),
                    created_at=str(row["created_at"]),
                    applied=int(row["ordinal"]) <= cursor,
                )
                for row in rows
            ],
        )

    def undo(self, project: Project) -> Project:
        original_version = project.state_version
        original_updated_at = project.updated_at
        try:
            with self._lock, self._connection:
                cursor = self._cursor(project.id)
                row = self._connection.execute(
                    "SELECT before_payload FROM plan_changes WHERE project_id = ? AND ordinal = ?",
                    (project.id, cursor),
                ).fetchone()
                if row is None:
                    raise ValueError("Нет изменений для отмены")
                self._restore(project, str(row["before_payload"]))
                saved = self._save_project(project)
                self._connection.execute(
                    "UPDATE plan_change_receipts SET status='undone' WHERE project_id=? AND change_set_id IN (SELECT change_set_id FROM plan_changes WHERE project_id=? AND ordinal=?)",
                    (project.id, project.id, cursor))
                self._connection.execute(
                    "UPDATE plan_history_cursors SET cursor = ? WHERE project_id = ?",
                    (cursor - 1, project.id),
                )
            advance_expected_project_version(saved.state_version)
            return saved
        except Exception:
            project.state_version = original_version
            project.updated_at = original_updated_at
            raise

    def redo(self, project: Project) -> Project:
        original_version = project.state_version
        original_updated_at = project.updated_at
        try:
            with self._lock, self._connection:
                cursor = self._cursor(project.id)
                row = self._connection.execute(
                    "SELECT after_payload FROM plan_changes WHERE project_id = ? AND ordinal = ?",
                    (project.id, cursor + 1),
                ).fetchone()
                if row is None:
                    raise ValueError("Нет изменений для повтора")
                self._restore(project, str(row["after_payload"]))
                saved = self._save_project(project)
                self._connection.execute(
                    "UPDATE plan_change_receipts SET status='applied' WHERE project_id=? AND change_set_id IN (SELECT change_set_id FROM plan_changes WHERE project_id=? AND ordinal=?)",
                    (project.id, project.id, cursor + 1))
                self._connection.execute(
                    "UPDATE plan_history_cursors SET cursor = ? WHERE project_id = ?",
                    (cursor + 1, project.id),
                )
            advance_expected_project_version(saved.state_version)
            return saved
        except Exception:
            project.state_version = original_version
            project.updated_at = original_updated_at
            raise
