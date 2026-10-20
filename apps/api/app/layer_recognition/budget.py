"""Постоянный общий бюджет распознавания между проектами и процессами; при нарушении
учёта запрос не разрешается.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from app.shared.error_messages import message
from app.shared.failures import DomainFailure, FailureParams, FailureReason

LIMIT_MICRO_USD = 2_000_000


class RecognitionUnavailable(DomainFailure):
    """Известный отказ распознавания с сообщением из локального каталога."""

    def __init__(
        self,
        reason: FailureReason,
        params: FailureParams | None = None,
        *,
        retryable: bool = False,
    ) -> None:
        if not isinstance(reason, FailureReason):
            raise TypeError("Recognition failure requires a typed reason")
        if (
            reason
            in {
                FailureReason.RECOGNITION_OPENAI_HTTP_FAILURE,
                FailureReason.RECOGNITION_RELAY_HTTP_FAILURE,
            }
            and type((params or {}).get("http_status")) is not int
        ):
            raise TypeError("Recognition HTTP failure requires an integer status")
        super().__init__(reason, params)
        self.retryable = retryable
        self.args = (message(reason, self.params),)


class RecognitionBudget:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        """Явная подготовка журнала; запрос не воссоздаёт потерянный учёт расходов."""
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

    def _connect(self) -> sqlite3.Connection:
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
                FailureReason.RECOGNITION_LEDGER_UNAVAILABLE
            ) from None

    @staticmethod
    def _validate(db: sqlite3.Connection) -> None:
        if db.execute("SELECT limit_usd FROM allowance WHERE id=1").fetchone() != (
            LIMIT_MICRO_USD,
        ):
            raise RecognitionUnavailable(FailureReason.RECOGNITION_BUDGET_CORRUPT)

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
                        FailureReason.RECOGNITION_BUDGET_EXHAUSTED
                    )
                request_id = uuid4().hex
                db.execute(
                    "INSERT INTO requests (id,reserved) VALUES (?,?)",
                    (request_id, micro_usd),
                )
                return request_id
        except sqlite3.Error:
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_LEDGER_UNAVAILABLE
            ) from None

    def record_usage(self, request_id: str, usage: dict) -> None:
        # Резерв не возвращается при ошибке или неоднозначном ответе. Сообщённый
        # расход является справочным и не увеличивает доступный бюджет.
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
                FailureReason.RECOGNITION_USAGE_SAVE_FAILED
            ) from None
