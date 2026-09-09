"""Durable run state and event log for agent execution."""

import json
import sqlite3
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.contracts import AgentIntent, AgentRunState, ResolvedScope, ToolResult, utc_now


class RunConflict(ValueError):
    pass


class AgentRunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=80)
    payload: dict = Field(default_factory=dict)
    created_at: str


class AgentRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: AgentRunState
    revision: int = Field(ge=1)
    created_at: str
    updated_at: str
    events: list[AgentRunEvent] = Field(default_factory=list)


class AgentRunStore:
    """SQLite-backed checkpoint store with optimistic concurrency."""

    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = RLock()
        with self.connection:
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    conversation_id TEXT,
                    revision INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_runs_project
                    ON agent_runs(project_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS agent_run_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence)
                );
                """
            )

    def close(self):
        self.connection.close()

    def create(self, project_id: str, intent: AgentIntent, *,
               conversation_id: str | None = None, snapshot_version: int = 1,
               plan_version: int | None = None,
               run_id: str | None = None) -> AgentRunRecord:
        identity = run_id or str(uuid4())
        now = utc_now()
        state = AgentRunState(
            run_id=identity,
            project_id=project_id,
            conversation_id=conversation_id,
            intent=intent,
            snapshot_version=snapshot_version,
            plan_version=plan_version,
        )
        if intent.scope_mode == "explicit":
            state.resolved_scope = ResolvedScope(
                project_id=project_id,
                zone_ids=intent.explicit_zone_ids,
                object_ids=intent.explicit_object_ids,
                basis="user",
                criteria=["explicit_user_scope"],
                source_revision=snapshot_version,
            )
        with self.lock, self.connection:
            try:
                self.connection.execute(
                    "INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (identity, project_id, conversation_id, 1, state.model_dump_json(), now, now),
                )
            except sqlite3.IntegrityError as error:
                raise RunConflict("Run id already exists") from error
        return self.get(project_id, identity)

    def get(self, project_id: str, run_id: str) -> AgentRunRecord:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM agent_runs WHERE run_id=? AND project_id=?", (run_id, project_id)
            ).fetchone()
            if row is None:
                raise KeyError("Run not found")
            events = self.connection.execute(
                "SELECT sequence, kind, payload, created_at FROM agent_run_events WHERE run_id=? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return AgentRunRecord(
            state=AgentRunState.model_validate_json(row["state"]),
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            events=[AgentRunEvent(sequence=item["sequence"], kind=item["kind"],
                                  payload=json.loads(item["payload"]), created_at=item["created_at"])
                    for item in events],
        )

    def checkpoint(self, project_id: str, run_id: str, *, expected_revision: int,
                   state: AgentRunState, kind: str, payload: dict) -> AgentRunRecord:
        if state.project_id != project_id or state.run_id != run_id:
            raise RunConflict("Checkpoint scope does not match the run")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > 1_000_000:
            raise ValueError("Run event is too large")
        now = utc_now()
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.connection.execute(
                "SELECT revision FROM agent_runs WHERE run_id=? AND project_id=?", (run_id, project_id)
            ).fetchone()
            if current is None:
                raise KeyError("Run not found")
            if current["revision"] != expected_revision:
                raise RunConflict("Run checkpoint is stale")
            revision = expected_revision + 1
            self.connection.execute(
                "INSERT INTO agent_run_events VALUES (?, ?, ?, ?, ?)",
                (run_id, revision, kind, encoded, now),
            )
            self.connection.execute(
                "UPDATE agent_runs SET revision=?, state=?, updated_at=? WHERE run_id=? AND project_id=?",
                (revision, state.model_dump_json(), now, run_id, project_id),
            )
        return self.get(project_id, run_id)
