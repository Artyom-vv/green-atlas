"""Постоянный журнал намерений и квитанций native-передачи; исполнение импорта остаётся
в LocalHandoff.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Condition, Event, RLock

JOURNAL_BUSY_TIMEOUT_SECONDS = 20
HANDOFF_WAIT_SECONDS = 25


class HandoffStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class HandoffRecord:
    id: str
    path: str
    digest: str
    project_id: str
    entry: str
    status: HandoffStatus
    stage: str
    message: str | None = None
    operation_id: str | None = None
    autocad_required: bool = True
    revision: int = 0


def _record(row: sqlite3.Row) -> HandoffRecord:
    return HandoffRecord(
        **{
            **dict(row),
            "status": HandoffStatus(row["status"]),
            "autocad_required": bool(row["autocad_required"]),
        }
    )


class HandoffStore:
    def __init__(self, database: Path) -> None:
        self.database = database
        self._changes = Condition(RLock())
        self._revisions: dict[str, int] = {}
        self._closed = False
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS local_handoffs(
                id TEXT PRIMARY KEY, path TEXT NOT NULL, digest TEXT NOT NULL,
                project_id TEXT NOT NULL, entry TEXT NOT NULL, status TEXT NOT NULL,
                stage TEXT NOT NULL, message TEXT, operation_id TEXT,
                autocad_required INTEGER NOT NULL DEFAULT 1
            )""")
            columns = {
                row["name"] for row in db.execute("PRAGMA table_info(local_handoffs)")
            }
            if "autocad_required" not in columns:
                backup = database.with_name(database.name + ".before-autocad-required")
                if not backup.exists():
                    with TemporaryDirectory(
                        prefix=".handoff-backup-", dir=database.parent
                    ) as scratch:
                        staged = Path(scratch) / "journal.sqlite3"
                        target = sqlite3.connect(staged)
                        try:
                            db.backup(target)
                        finally:
                            target.close()
                        staged.chmod(0o600)
                        staged.replace(backup)
                db.execute(
                    "ALTER TABLE local_handoffs ADD COLUMN autocad_required "
                    "INTEGER NOT NULL DEFAULT 1"
                )
        self.interrupt_active("Подготовка прервана")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=JOURNAL_BUSY_TIMEOUT_SECONDS)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, identifier: str) -> HandoffRecord:
        with self._changes, self.connect() as db:
            row = db.execute(
                "SELECT * FROM local_handoffs WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise KeyError("Передача не найдена")
            return replace(_record(row), revision=self._revisions.get(identifier, 0))

    def wait(
        self,
        identifier: str,
        after: int,
        stop: Event,
        *,
        timeout: float = HANDOFF_WAIT_SECONDS,
    ) -> HandoffRecord:
        """Вернуть изменение после commit либо текущую квитанцию по сроку ожидания.

        Журнал принадлежит одному LocalHandoff. Ревизия относится к его запуску;
        новый движок сначала читает актуальную квитанцию без ожидания.
        """
        with self._changes:
            record = self.get(identifier)
            if record.status in {HandoffStatus.QUEUED, HandoffStatus.PROCESSING}:
                self._changes.wait_for(
                    lambda: (
                        self._revisions.get(identifier, 0) != after
                        or stop.is_set()
                        or self._closed
                    ),
                    timeout=min(timeout, HANDOFF_WAIT_SECONDS),
                )
            return (
                self.get(identifier)
                if self._revisions.get(identifier, 0) != record.revision
                else record
            )

    def cancel_wait(self, stop: Event) -> None:
        with self._changes:
            stop.set()
            self._changes.notify_all()

    def close(self) -> None:
        with self._changes:
            self._closed = True
            self._changes.notify_all()

    def _notify(self, identifier: str) -> None:
        self._revisions[identifier] = self._revisions.get(identifier, 0) + 1
        self._changes.notify_all()

    def find(self, identifier: str) -> HandoffRecord | None:
        try:
            return self.get(identifier)
        except KeyError:
            return None

    def records(self) -> list[HandoffRecord]:
        with self._changes, self.connect() as db:
            rows = db.execute(
                "SELECT * FROM local_handoffs ORDER BY rowid DESC"
            ).fetchall()
            return [
                replace(_record(row), revision=self._revisions.get(row["id"], 0))
                for row in rows
            ]

    def reserve(
        self,
        identifier: str,
        path: Path,
        digest: str,
        project_id: str,
        entry: str,
        *,
        autocad_required: bool = True,
    ) -> HandoffRecord:
        with self._changes:
            with self.connect() as db:
                updated = db.execute(
                    "INSERT OR IGNORE INTO local_handoffs "
                    "(id,path,digest,project_id,entry,status,stage,autocad_required) "
                    "VALUES(?,?,?,?,?,'queued','Ожидаем подготовку',?)",
                    (
                        identifier,
                        str(path),
                        digest,
                        project_id,
                        entry,
                        int(autocad_required),
                    ),
                ).rowcount
            if updated:
                self._notify(identifier)
            record = self.get(identifier)
            if record.digest != digest or record.path != str(path):
                raise ValueError("Состав сохранённой передачи изменился")
            return record

    def queue(self, identifier: str) -> None:
        with self._changes:
            with self.connect() as db:
                updated = db.execute(
                    "UPDATE local_handoffs SET status='queued', "
                    "stage='Повторяем подготовку',message=NULL WHERE id=? "
                    "AND status IN ('failed','interrupted','cancelled','needs_review')",
                    (identifier,),
                ).rowcount
            if updated:
                self._notify(identifier)

    def claim(self, identifier: str) -> bool:
        with self._changes:
            with self.connect() as db:
                updated = db.execute(
                    "UPDATE local_handoffs SET status='processing', "
                    "stage='Проверяем снимок AutoCAD' WHERE id=? AND status='queued'",
                    (identifier,),
                ).rowcount
            if updated:
                self._notify(identifier)
            return bool(updated)

    def stage(self, identifier: str, text: str) -> None:
        with self._changes:
            with self.connect() as db:
                updated = db.execute(
                    "UPDATE local_handoffs SET stage=? WHERE id=? AND status='processing'",
                    (text, identifier),
                ).rowcount
            if updated:
                self._notify(identifier)
            if not updated:
                raise InterruptedError("Подготовка прервана или отменена")

    def publish(
        self, identifier: str, text: str, operation_id: str | None = None
    ) -> None:
        with self._changes:
            with self.connect() as db:
                updated = db.execute(
                    "UPDATE local_handoffs SET status='needs_review',stage=?, "
                    "operation_id=?,message=NULL WHERE id=? AND status='processing'",
                    (text, operation_id, identifier),
                ).rowcount
            if updated:
                self._notify(identifier)
            if not updated:
                raise InterruptedError("Подготовка прервана или отменена")

    def fail(self, identifier: str, message: str) -> None:
        with self._changes:
            with self.connect() as db:
                updated = db.execute(
                    "UPDATE local_handoffs SET status='failed', "
                    "stage='Подготовка остановлена',message=? "
                    "WHERE id=? AND status IN ('queued','processing')",
                    (message, identifier),
                ).rowcount
            if updated:
                self._notify(identifier)

    def cancel(self, identifier: str) -> None:
        self.get(identifier)
        with self._changes:
            with self.connect() as db:
                updated = db.execute(
                    "UPDATE local_handoffs SET status='cancelled',stage='Подготовка отменена' "
                    "WHERE id=? AND status!='needs_review'",
                    (identifier,),
                ).rowcount
            if updated:
                self._notify(identifier)

    def interrupt_active(self, stage: str) -> None:
        with self._changes:
            with self.connect() as db:
                identifiers = [
                    row["id"]
                    for row in db.execute(
                        "SELECT id FROM local_handoffs WHERE status IN ('queued','processing')"
                    )
                ]
                db.execute(
                    "UPDATE local_handoffs SET status='interrupted',stage=? "
                    "WHERE status IN ('queued','processing')",
                    (stage,),
                )
            for identifier in identifiers:
                self._notify(identifier)
