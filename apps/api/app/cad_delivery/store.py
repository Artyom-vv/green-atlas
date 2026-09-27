"""Durable, transfer-scoped consent. Never grants general project/API access."""

import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from app.cad_delivery.contracts import (
    TransferCreate,
    TransferDecision,
    TransferManifest,
    TransferStatus,
)


class TransferError(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


class TransferStore:
    def __init__(self, path: Path, *, now=time.time):
        self.path, self.now = path, now
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cad_transfers (
                id TEXT PRIMARY KEY, secret_hash TEXT NOT NULL,
                confirmation_code TEXT NOT NULL, plugin_version TEXT NOT NULL,
                manifest TEXT NOT NULL, manifest_hash TEXT NOT NULL,
                status TEXT NOT NULL, owner TEXT, expires_at INTEGER NOT NULL,
                last_poll REAL NOT NULL DEFAULT 0, failed_codes INTEGER NOT NULL DEFAULT 0
            )""")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _get(self, db, transfer_id: str):
        row = db.execute(
            "SELECT * FROM cad_transfers WHERE id=?", (transfer_id,)
        ).fetchone()
        if row is None:
            raise TransferError("TRANSFER_NOT_FOUND", "Передача не найдена", 404)
        return row

    def _status(self, row) -> TransferStatus:
        manifest = TransferManifest.model_validate_json(row["manifest"])
        status = row["status"]
        if status not in {"denied", "cancelled"} and row["expires_at"] <= self.now():
            status = "expired"
        return TransferStatus(
            id=row["id"],
            status=status,
            entry=manifest.entry,
            manifest_sha256=row["manifest_hash"],
            file_count=len(manifest.files),
            total_bytes=sum(item.bytes for item in manifest.files),
            expires_at=row["expires_at"],
        )

    @staticmethod
    def _authenticate(row, secret: str):
        if not hmac.compare_digest(
            row["secret_hash"], sha256(secret.encode()).hexdigest()
        ):
            raise TransferError("TRANSFER_NOT_FOUND", "Передача не найдена", 404)

    def create(self, request: TransferCreate):
        transfer_id = str(request.request_id)
        with self.connection() as db:
            # Only short-lived consent records exist here, not project data.
            # Keep expired receipts for a day for retries, then bound retention.
            publication_exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cad_publications'"
            ).fetchone()
            keep_receipts = (
                " AND id NOT IN (SELECT id FROM cad_publications)"
                if publication_exists
                else ""
            )
            db.execute(
                "DELETE FROM cad_transfers WHERE expires_at < ?" + keep_receipts,
                (self.now() - 86400,),
            )
            existing = db.execute(
                "SELECT * FROM cad_transfers WHERE id=?", (transfer_id,)
            ).fetchone()
            if existing:
                self._authenticate(existing, request.client_secret.get_secret_value())
                if (
                    existing["manifest_hash"] != request.manifest.digest()
                    or existing["plugin_version"] != request.plugin_version
                ):
                    raise TransferError(
                        "TRANSFER_CHANGED",
                        "Создайте новую передачу для изменённого комплекта",
                    )
            else:
                pending = db.execute(
                    "SELECT count(*) FROM cad_transfers WHERE expires_at>? AND status IN ('awaiting_approval','approved')",
                    (self.now(),),
                ).fetchone()[0]
                if pending >= 128:
                    raise TransferError(
                        "TRANSFER_BUSY",
                        "Слишком много ожидающих передач. Повторите позже",
                        429,
                    )
                db.execute(
                    "INSERT INTO cad_transfers (id,secret_hash,confirmation_code,plugin_version,manifest,manifest_hash,status,expires_at) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        transfer_id,
                        sha256(
                            request.client_secret.get_secret_value().encode()
                        ).hexdigest(),
                        secrets.token_hex(4).upper(),
                        request.plugin_version,
                        request.manifest.model_dump_json(),
                        request.manifest.digest(),
                        "awaiting_approval",
                        int(self.now()) + 600,
                    ),
                )
            row = self._get(db, transfer_id)
            return self._status(row), row["confirmation_code"]

    def device_status(self, transfer_id: str, secret: str, *, poll=True):
        with self.connection() as db:
            row = self._get(db, transfer_id)
            self._authenticate(row, secret)
            if poll:
                if row["last_poll"] and self.now() - row["last_poll"] < 5:
                    raise TransferError(
                        "SLOW_DOWN",
                        "Проверяйте состояние не чаще раза в пять секунд",
                        429,
                    )
                db.execute(
                    "UPDATE cad_transfers SET last_poll=? WHERE id=?",
                    (self.now(), transfer_id),
                )
            return self._status(row)

    def review(self, transfer_id: str, owner: str):
        with self.connection() as db:
            row = self._get(db, transfer_id)
            if row["owner"] is not None and row["owner"] != owner:
                raise TransferError("TRANSFER_NOT_FOUND", "Передача не найдена", 404)
            return (
                self._status(row),
                TransferManifest.model_validate_json(row["manifest"]),
                row["plugin_version"],
            )

    def decide(self, transfer_id: str, owner: str, decision: TransferDecision):
        error = None
        with self.connection() as db:
            row = self._get(db, transfer_id)
            if row["owner"] is not None and row["owner"] != owner:
                raise TransferError("TRANSFER_NOT_FOUND", "Передача не найдена", 404)
            if self._status(row).status == "expired":
                raise TransferError(
                    "TRANSFER_EXPIRED",
                    "Подтверждение истекло. Начните передачу заново",
                    410,
                )
            if row["manifest_hash"] != decision.manifest_sha256:
                raise TransferError(
                    "TRANSFER_CHANGED",
                    "Состав комплекта изменился. Проверьте его заново",
                )
            desired = "approved" if decision.decision == "approve" else "denied"
            if row["status"] not in {"awaiting_approval", desired}:
                raise TransferError(
                    "TRANSFER_TERMINAL", "Решение по передаче уже принято"
                )
            if decision.decision == "approve" and not hmac.compare_digest(
                row["confirmation_code"], decision.confirmation_code
            ):
                attempts = row["failed_codes"] + 1
                db.execute(
                    "UPDATE cad_transfers SET failed_codes=?, status=? WHERE id=?",
                    (
                        attempts,
                        "denied" if attempts >= 5 else row["status"],
                        transfer_id,
                    ),
                )
                error = TransferError(
                    "CODE_MISMATCH", "Код не совпадает с кодом в AutoCAD", 400
                )
            else:
                db.execute(
                    "UPDATE cad_transfers SET owner=?,status=? WHERE id=?",
                    (owner, desired, transfer_id),
                )
            result = self._status(self._get(db, transfer_id))
        if error:
            raise error  # Commit attempt counter before returning the rejection.
        return result

    def cancel(self, transfer_id: str, secret: str):
        with self.connection() as db:
            row = self._get(db, transfer_id)
            self._authenticate(row, secret)
            if row["status"] not in {"denied", "cancelled"}:
                db.execute(
                    "UPDATE cad_transfers SET status='cancelled' WHERE id=?",
                    (transfer_id,),
                )
            return self._status(self._get(db, transfer_id))

    def require_approved(self, transfer_id: str, secret: str) -> TransferManifest:
        with self.connection() as db:
            row = self._get(db, transfer_id)
            self._authenticate(row, secret)
            if self._status(row).status != "approved" or not row["owner"]:
                raise TransferError(
                    "TRANSFER_NOT_APPROVED", "Подтвердите передачу в браузере", 403
                )
            return TransferManifest.model_validate_json(row["manifest"])
