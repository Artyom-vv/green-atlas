"""Persistent, fail-closed allowance shared by projects and runtime processes."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

LIMIT_MICRO_USD = 2_000_000


class RecognitionUnavailable(ValueError):
    """A public, credential-free explanation suitable for the UI."""


class RecognitionBudget:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        """Explicit setup only; request handling must never recreate a lost ledger."""
        if self.path.exists():
            self.total()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.open("xb"):
            pass
        self.path.chmod(0o600)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "CREATE TABLE allowance (id INTEGER PRIMARY KEY CHECK(id=1), limit_usd INTEGER NOT NULL)"
            )
            db.execute("INSERT INTO allowance VALUES (1, ?)", (LIMIT_MICRO_USD,))
            db.execute(
                "CREATE TABLE requests (id TEXT PRIMARY KEY, reserved INTEGER NOT NULL CHECK(reserved>0), input_tokens INTEGER, output_tokens INTEGER)"
            )

    def _connect(self):
        return sqlite3.connect(
            self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10
        )

    def total(self) -> int:
        try:
            with closing(self._connect()) as db:
                self._validate(db)
                return db.execute(
                    "SELECT COALESCE(SUM(reserved),0) FROM requests"
                ).fetchone()[0]
        except sqlite3.Error:
            raise RecognitionUnavailable(
                "Журнал расходов распознавания недоступен. Запросы остановлены"
            ) from None

    @staticmethod
    def _validate(db):
        if db.execute("SELECT limit_usd FROM allowance WHERE id=1").fetchone() != (
            LIMIT_MICRO_USD,
        ):
            raise RecognitionUnavailable(
                "Настройки общего бюджета распознавания повреждены"
            )

    def reserve(self, micro_usd: int) -> str:
        if type(micro_usd) is not int or micro_usd <= 0:
            raise ValueError("Invalid reservation")
        try:
            with closing(self._connect()) as db, db:
                db.execute("BEGIN IMMEDIATE")
                self._validate(db)
                spent = db.execute(
                    "SELECT COALESCE(SUM(reserved),0) FROM requests"
                ).fetchone()[0]
                if spent + micro_usd > LIMIT_MICRO_USD:
                    raise RecognitionUnavailable(
                        "Общий бюджет распознавания $2 исчерпан. Сохранённые предложения доступны"
                    )
                request_id = uuid4().hex
                db.execute(
                    "INSERT INTO requests (id,reserved) VALUES (?,?)",
                    (request_id, micro_usd),
                )
                return request_id
        except sqlite3.Error:
            raise RecognitionUnavailable(
                "Журнал расходов распознавания недоступен. Запросы остановлены"
            ) from None

    def record_usage(self, request_id: str, usage: dict) -> None:
        # Reservations are never refunded, even for failed or ambiguous requests.
        # Reported usage is informational and cannot increase the allowance.
        values = [usage.get("input_tokens"), usage.get("output_tokens")]
        if any(type(v) is not int or v < 0 for v in values):
            return
        try:
            with closing(self._connect()) as db, db:
                db.execute(
                    "UPDATE requests SET input_tokens=?, output_tokens=? WHERE id=?",
                    (*values, request_id),
                )
        except sqlite3.Error:
            raise RecognitionUnavailable(
                "Не удалось сохранить расход распознавания"
            ) from None
