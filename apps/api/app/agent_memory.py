"""Durable conversation aggregates. No geometry or plan mutation is performed here.

Messages are immutable. Tool events and task revisions are separate records so a
declined preview never erases either the user's intent or the assistant's answer.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["place", "edit", "delete", "inspect", "zones", "release"] | None = None
    scope: Literal["project", "zones", "objects", "selection"] | None = None
    zone_ids: list[str] | None = Field(default=None, max_length=80)
    object_ids: list[str] | None = Field(default=None, max_length=5000)
    quantity: int | None = Field(default=None, ge=1, le=5000)
    quantity_mode: Literal["target", "maximum", "fill_available"] | None = None
    # Density is a soft layout preference. Statutory setbacks remain owned by
    # the domain validator and must never be inferred from this field.
    spacing_policy: Literal["open", "balanced", "canopy"] | None = None
    arrangement: Literal["area", "row", "building_contour", "building_groves", "road_edges", "grid", "groves", "brush", "individual"] | None = None
    # A spatial delegation is different from an unresolved scope: the user
    # explicitly permits the agent to choose one safe existing zone.
    spatial_anchor: Literal["edge"] | None = None
    alignment_target: Literal["road", "building"] | None = None
    plant_kind: Literal["tree", "shrub", "mixed"] | None = None
    species_mode: Literal["automatic", "specified"] | None = None
    species_revision_ids: list[str] | None = Field(default=None, max_length=100)
    constraints: list[str] | None = Field(default=None, max_length=100)
    exclusions: list[str] | None = Field(default=None, max_length=100)
    desired_result: str | None = Field(default=None, max_length=2000)
    post_action: Literal["focus_map"] | None = None
    edit_action: Literal["species", "move", "lock", "unlock"] | None = None
    move_dx_m: float | None = Field(default=None, allow_inf_nan=False)
    move_dy_m: float | None = Field(default=None, allow_inf_nan=False)


class TaskState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: TaskPatch = Field(default_factory=TaskPatch)
    # message id, not model reasoning; each accepted field has traceable origin.
    provenance: dict[str, str] = Field(default_factory=dict)

    def amended(self, patch: TaskPatch, message_id: str) -> "TaskState":
        changes = patch.model_dump(exclude_unset=True)
        # A new edit action cannot inherit a displacement from an older move.
        # Sparse distance amendments within the same move still retain the
        # other axis. Explicit values in this message always take precedence.
        if "edit_action" in changes and changes["edit_action"] != self.values.edit_action:
            for field in ("move_dx_m", "move_dy_m"):
                if field not in changes and getattr(self.values, field) is not None:
                    changes[field] = None
        values = self.values.model_dump()
        values.update(changes)
        provenance = {**self.provenance, **{key: message_id for key in changes if key not in self.provenance or getattr(self.values, key) != changes[key]}}
        if changes.get("species_mode") == "automatic":
            values["species_revision_ids"] = None
            if self.values.species_revision_ids is not None or "species_revision_ids" not in provenance:
                provenance["species_revision_ids"] = message_id
        if values["species_mode"] == "specified" and not values["species_revision_ids"]:
            raise ValueError("Specified species requires a catalog selection")
        return TaskState(values=TaskPatch.model_validate(values), provenance=provenance)


class ConversationConflict(ValueError):
    pass


class ConversationStore:
    """Project-scoped SQLite store with optimistic concurrency across workers.

    The HTTP boundary must verify project access before using this repository.
    A separate connection participates in the same SQLite DB as the project.
    """

    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(str(path), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = RLock()
        with self.connection:
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS agent_conversations (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
                    revision INTEGER NOT NULL, task TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS agent_conversations_project
                    ON agent_conversations(project_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS agent_records (
                    conversation_id TEXT NOT NULL, record_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(conversation_id, record_id),
                    UNIQUE(conversation_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS agent_create_requests (
                    project_id TEXT NOT NULL, request_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, title TEXT NOT NULL,
                    PRIMARY KEY(project_id, request_id)
                );
            """)

    def close(self):
        self.connection.close()

    def create(self, project_id: str, title: str = "Новый диалог", *, request_id: str | None = None) -> dict:
        if not project_id or not title.strip() or len(title) > 160:
            raise ValueError("Invalid conversation identity")
        if request_id is not None and (not request_id or len(request_id) > 200):
            raise ValueError("Invalid creation request")
        identity, now = str(uuid4()), datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if request_id is not None:
                previous = self.connection.execute("SELECT conversation_id, title FROM agent_create_requests WHERE project_id=? AND request_id=?", (project_id, request_id)).fetchone()
                if previous:
                    if previous["title"] != title.strip():
                        raise ConversationConflict("Creation request already used with another title")
                    return self.get(project_id, previous["conversation_id"])
            self.connection.execute("INSERT INTO agent_conversations VALUES (?, ?, ?, 1, ?, ?, ?)",
                                    (identity, project_id, title.strip(), TaskState().model_dump_json(), now, now))
            if request_id is not None:
                self.connection.execute("INSERT INTO agent_create_requests VALUES (?, ?, ?, ?)", (project_id, request_id, identity, title.strip()))
        return self.get(project_id, identity)

    def list(self, project_id: str) -> list[dict]:
        with self.lock:
            return [dict(row) for row in self.connection.execute(
                "SELECT id, title, revision, created_at, updated_at FROM agent_conversations WHERE project_id=? ORDER BY updated_at DESC, id",
                (project_id,))]

    def import_legacy(self, project_id: str, source_id: str, messages: list[dict]) -> dict:
        """Atomic, repeatable import; retains source text AND legacy status.

        Never overwrites an existing conversation, even when a browser retries
        with newer local messages. The source browser must retain its backup.
        """
        identity = str(uuid5(NAMESPACE_URL, json.dumps([project_id, source_id])))
        validated = []
        for index, message in enumerate(messages):
            if message.get("role") not in {"user", "assistant"} or not isinstance(message.get("text"), str) or not 0 < len(message["text"]) <= 2000:
                raise ValueError("Invalid legacy message")
            if message.get("result") is not None and not isinstance(message["result"], str):
                raise ValueError("Invalid legacy status")
            validated.append((f"legacy-{index}", "message", {"role": message["role"], "text": message["text"], "legacy_id": message.get("id")}))
            if message.get("result"):
                validated.append((f"legacy-status-{index}", "tool_event", {"message_id": f"legacy-{index}", "status": "legacy", "text": message["result"]}))
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute("SELECT id FROM agent_conversations WHERE id=? AND project_id=?", (identity, project_id)).fetchone()
            if existing:
                records = self.get(project_id, identity)["records"]
                imported = [(record["record_id"], record["kind"], record["payload"]["content"]) for record in records if record["record_id"].startswith("legacy-")]
                if imported != validated:
                    raise ConversationConflict("Legacy history changed; retain browser data and import with a new source id")
                return self.get(project_id, identity)
            self.connection.execute("INSERT INTO agent_conversations VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (identity, project_id, "Предыдущая переписка", len(validated) + 1, TaskState().model_dump_json(), now, now))
            for sequence, (record_id, kind, payload) in enumerate(validated, 2):
                envelope = json.dumps({"content": payload, "task_patch": None}, ensure_ascii=False, sort_keys=True)
                self.connection.execute("INSERT INTO agent_records VALUES (?, ?, ?, ?, ?, ?)", (identity, record_id, sequence, kind, envelope, now))
        return self.get(project_id, identity)

    def get(self, project_id: str, conversation_id: str) -> dict:
        with self.lock:
            row = self.connection.execute("SELECT * FROM agent_conversations WHERE project_id=? AND id=?", (project_id, conversation_id)).fetchone()
            if row is None:
                raise KeyError("Conversation not found")
            records = [dict(record) for record in self.connection.execute(
                "SELECT record_id, sequence, kind, payload, created_at FROM agent_records WHERE conversation_id=? ORDER BY sequence", (conversation_id,))]
        return {**dict(row), "task": json.loads(row["task"]), "records": [{**record, "payload": json.loads(record["payload"])} for record in records]}

    def append(self, project_id: str, conversation_id: str, *, expected_revision: int,
               record_id: str, kind: Literal["message", "tool_event", "annotation"],
               payload: dict, patch: TaskPatch | None = None,
               assistant_text: str | None = None, reset_task: bool = False,
               tool_result: dict | None = None) -> dict:
        if not record_id or len(record_id) > 200 or kind not in {"message", "tool_event", "annotation"}:
            raise ValueError("Invalid record")
        if kind == "message" and (payload.get("role") not in {"user", "assistant"} or not isinstance(payload.get("text"), str) or not payload["text"].strip()):
            raise ValueError("Invalid message")
        if patch is not None and (kind != "message" or payload.get("role") != "user"):
            raise ValueError("Task changes require a user message")
        if (assistant_text is not None or reset_task) and (kind != "message" or payload.get("role") != "user"):
            raise ValueError("A completed turn requires a user message")
        if assistant_text is not None and not 0 < len(assistant_text) <= 2000:
            raise ValueError("Invalid assistant message")
        if tool_result is not None:
            if assistant_text is None or kind != "message" or payload.get("role") != "user":
                raise ValueError("Tool evidence requires a completed user turn")
            evidence_json = json.dumps({"content": tool_result, "task_patch": None}, ensure_ascii=False, sort_keys=True, allow_nan=False)
            if len(evidence_json.encode("utf-8")) > 1_000_000:
                raise ValueError("Tool evidence too large")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > (16_000_000 if kind == "tool_event" else 100_000):
            raise ValueError("Record too large")
        # The interpreted amendment is persisted with the immutable source text.
        envelope_data = {"content": payload, "task_patch": patch.model_dump(exclude_unset=True) if patch is not None else None}
        if assistant_text is not None or reset_task:
            envelope_data.update({"assistant_text": assistant_text, "reset_task": reset_task})
        if tool_result is not None:
            # Include evidence identity in idempotency without duplicating the
            # potentially large tool output in the source message.
            from hashlib import sha256
            envelope_data["tool_result_digest"] = sha256(evidence_json.encode("utf-8")).hexdigest()
        envelope = json.dumps(envelope_data, ensure_ascii=False, sort_keys=True)
        with self.lock, self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            current = self.get(project_id, conversation_id)
            previous = self.connection.execute("SELECT kind, payload FROM agent_records WHERE conversation_id=? AND record_id=?", (conversation_id, record_id)).fetchone()
            if previous is not None:
                if previous["kind"] != kind or previous["payload"] != envelope:
                    raise ConversationConflict("Record id already used with different content")
                return current  # Safe retry after a lost HTTP response.
            if current["revision"] != expected_revision:
                raise ConversationConflict("Conversation changed; reload before amending")
            task = TaskState() if reset_task else TaskState.model_validate(current["task"])
            if patch is not None:
                task = task.amended(patch, record_id)
            now = datetime.now(timezone.utc).isoformat()
            revision = expected_revision + 1
            self.connection.execute("INSERT INTO agent_records VALUES (?, ?, ?, ?, ?, ?)", (conversation_id, record_id, revision, kind, envelope, now))
            if tool_result is not None:
                revision += 1
                self.connection.execute("INSERT INTO agent_records VALUES (?, ?, ?, ?, ?, ?)", (conversation_id, f"tools:{record_id}", revision, "tool_event", evidence_json, now))
            if assistant_text is not None:
                revision += 1
                answer = json.dumps({"content": {"role": "assistant", "text": assistant_text}, "task_patch": None}, ensure_ascii=False, sort_keys=True)
                self.connection.execute("INSERT INTO agent_records VALUES (?, ?, ?, ?, ?, ?)", (conversation_id, f"answer:{record_id}", revision, "message", answer, now))
            self.connection.execute("UPDATE agent_conversations SET revision=?, task=?, updated_at=? WHERE id=? AND project_id=?", (revision, task.model_dump_json(), now, conversation_id, project_id))
        return self.get(project_id, conversation_id)
