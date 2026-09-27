"""Bounded, resumable transfer staging. No geometry parsing or project mutation."""

import logging
import os
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from app.cad_delivery.contracts import (
    TransferManifest,
    UploadFileProgress,
    UploadProgress,
)
from app.cad_delivery.store import TransferError, TransferStore

CHUNK_BYTES = 4 * 1024**2
UPLOAD_TTL = 24 * 3600
STAGING_BUDGET = 8 * 1024**3
logger = logging.getLogger(__name__)


class UploadStore:
    def __init__(self, consent: TransferStore):
        self.consent = consent
        self.root = consent.path.parent / "transfer-files"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise TransferError(
                "STAGING_UNAVAILABLE", "Хранилище передачи недоступно", 503
            )
        with consent.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cad_uploads (
                id TEXT PRIMARY KEY, expires_at INTEGER NOT NULL,
                reserved_bytes INTEGER NOT NULL, ready INTEGER NOT NULL DEFAULT 0
            )""")
            if "pinned" not in {
                row["name"] for row in db.execute("PRAGMA table_info(cad_uploads)")
            }:
                db.execute(
                    "ALTER TABLE cad_uploads ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
                )
            db.execute("""CREATE TABLE IF NOT EXISTS cad_upload_files (
                id TEXT NOT NULL, file_index INTEGER NOT NULL, received INTEGER NOT NULL,
                PRIMARY KEY(id,file_index)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS cad_upload_chunks (
                id TEXT NOT NULL, file_index INTEGER NOT NULL, offset INTEGER NOT NULL,
                bytes INTEGER NOT NULL, hash TEXT NOT NULL,
                PRIMARY KEY(id,file_index,offset)
            )""")

    def _directory(self, transfer_id: str) -> Path:
        result = self.root / str(UUID(transfer_id))
        if result.is_symlink():
            raise TransferError(
                "STAGING_UNAVAILABLE", "Хранилище передачи недоступно", 503
            )
        return result

    def _file(self, transfer_id: str, index: int) -> Path:
        path = self._directory(transfer_id) / f"{index}.part"
        if path.is_symlink():
            raise TransferError(
                "STAGING_UNAVAILABLE", "Хранилище передачи недоступно", 503
            )
        return path

    def _authorize(self, db, transfer_id: str, secret: str):
        row = self.consent._get(db, transfer_id)
        self.consent._authenticate(row, secret)
        if row["status"] != "approved" or not row["owner"]:
            raise TransferError(
                "TRANSFER_NOT_APPROVED", "Подтвердите передачу в браузере", 403
            )
        lease = db.execute(
            "SELECT * FROM cad_uploads WHERE id=?", (transfer_id,)
        ).fetchone()
        deadline = lease["expires_at"] if lease else row["expires_at"]
        if deadline <= self.consent.now():
            raise TransferError(
                "UPLOAD_EXPIRED", "Время передачи истекло. Начните заново", 410
            )
        return row, lease, TransferManifest.model_validate_json(row["manifest"])

    def _progress(self, db, transfer_id: str, lease, manifest):
        received = dict(
            db.execute(
                "SELECT file_index,received FROM cad_upload_files WHERE id=?",
                (transfer_id,),
            ).fetchall()
        )
        return UploadProgress(
            id=transfer_id,
            status="ready" if lease["ready"] else "uploading",
            expires_at=lease["expires_at"],
            chunk_bytes=CHUNK_BYTES,
            files=[
                UploadFileProgress(
                    index=i,
                    name=f.name,
                    bytes=f.bytes,
                    received_bytes=received.get(i, 0),
                )
                for i, f in enumerate(manifest.files)
            ],
        )

    def begin(self, transfer_id: str, secret: str) -> UploadProgress:
        self.collect_expired()
        with self.consent.connection() as db:
            _, lease, manifest = self._authorize(db, transfer_id, secret)
            if lease is None:
                reserved = sum(f.bytes for f in manifest.files)
                used = db.execute(
                    "SELECT coalesce(sum(reserved_bytes),0) FROM cad_uploads"
                ).fetchone()[0]
                if used + reserved > STAGING_BUDGET:
                    raise TransferError(
                        "STAGING_FULL", "Нет места для передачи. Повторите позже", 507
                    )
                db.execute(
                    "INSERT INTO cad_uploads(id,expires_at,reserved_bytes) VALUES(?,?,?)",
                    (transfer_id, int(self.consent.now()) + UPLOAD_TTL, reserved),
                )
                db.executemany(
                    "INSERT INTO cad_upload_files VALUES(?,?,0)",
                    [(transfer_id, i) for i in range(len(manifest.files))],
                )
                lease = db.execute(
                    "SELECT * FROM cad_uploads WHERE id=?", (transfer_id,)
                ).fetchone()
            return self._progress(db, transfer_id, lease, manifest)

    def progress(self, transfer_id: str, secret: str) -> UploadProgress:
        with self.consent.connection() as db:
            _, lease, manifest = self._authorize(db, transfer_id, secret)
            if lease is None:
                raise TransferError("UPLOAD_NOT_STARTED", "Начните передачу файлов")
            return self._progress(db, transfer_id, lease, manifest)

    def append(
        self,
        transfer_id: str,
        secret: str,
        index: int,
        offset: int,
        data: bytes,
        digest: str,
    ) -> UploadProgress:
        if not data or len(data) > CHUNK_BYTES:
            raise TransferError("CHUNK_SIZE", "Недопустимый размер части файла", 413)
        if sha256(data).hexdigest() != digest:
            raise TransferError(
                "CHUNK_HASH", "Часть файла повреждена. Повторите отправку", 422
            )
        with self.consent.connection() as db:
            _, lease, manifest = self._authorize(db, transfer_id, secret)
            if lease is None:
                raise TransferError("UPLOAD_NOT_STARTED", "Начните передачу файлов")
            if index < 0 or index >= len(manifest.files):
                raise TransferError(
                    "FILE_NOT_LISTED",
                    "Файл отсутствует в подтверждённом комплекте",
                    404,
                )
            previous = db.execute(
                "SELECT * FROM cad_upload_chunks WHERE id=? AND file_index=? AND offset=?",
                (transfer_id, index, offset),
            ).fetchone()
            if previous:
                if previous["hash"] != digest or previous["bytes"] != len(data):
                    raise TransferError(
                        "CHUNK_CHANGED", "Повтор содержит другие данные"
                    )
                return self._progress(db, transfer_id, lease, manifest)
            received = db.execute(
                "SELECT received FROM cad_upload_files WHERE id=? AND file_index=?",
                (transfer_id, index),
            ).fetchone()[0]
            if lease["ready"] or offset != received:
                raise TransferError(
                    "UPLOAD_OFFSET",
                    "Обновите состояние передачи и продолжите с принятого места",
                )
            if received + len(data) > manifest.files[index].bytes:
                raise TransferError(
                    "FILE_SIZE", "Размер файла превышает подтверждённый", 413
                )
            if len(data) != min(CHUNK_BYTES, manifest.files[index].bytes - received):
                raise TransferError(
                    "CHUNK_SIZE", "Отправьте целую часть файла указанного размера", 422
                )
            self._directory(transfer_id).mkdir(mode=0o700, exist_ok=True)
            path = self._file(transfer_id, index)
            # Write+fsync before committing offset. A crash tail is truncated on retry.
            try:
                with path.open("r+b" if path.exists() else "w+b") as output:
                    if output.seek(0, 2) < received:
                        raise TransferError(
                            "STAGING_DAMAGED",
                            "Принятый файл утрачен. Начните новую передачу",
                            409,
                        )
                    output.truncate(received)
                    output.seek(received)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                db.execute(
                    "INSERT INTO cad_upload_chunks VALUES(?,?,?,?,?)",
                    (transfer_id, index, offset, len(data), digest),
                )
                db.execute(
                    "UPDATE cad_upload_files SET received=? WHERE id=? AND file_index=?",
                    (received + len(data), transfer_id, index),
                )
            except OSError as error:
                raise TransferError(
                    "STAGING_WRITE_FAILED",
                    "Не удалось сохранить часть файла. Повторите передачу",
                    507,
                ) from error
            return self._progress(db, transfer_id, lease, manifest)

    def finish(self, transfer_id: str, secret: str) -> UploadProgress:
        with self.consent.connection() as db:
            _, lease, manifest = self._authorize(db, transfer_id, secret)
            if lease is None:
                raise TransferError("UPLOAD_NOT_STARTED", "Начните передачу файлов")
            progress = self._progress(db, transfer_id, lease, manifest)
            if any(f.received_bytes != f.bytes for f in progress.files):
                raise TransferError(
                    "UPLOAD_INCOMPLETE", "Не все файлы переданы. Продолжите загрузку"
                )
        # Full files cannot be appended further. Hash outside the database lock.
        for index, expected in enumerate(manifest.files):
            digest = sha256()
            size = 0
            try:
                with self._file(transfer_id, index).open("rb") as source:
                    while part := source.read(CHUNK_BYTES):
                        digest.update(part)
                        size += len(part)
            except OSError as error:
                raise TransferError(
                    "STAGING_DAMAGED",
                    "Принятый файл утрачен. Начните новую передачу",
                    409,
                ) from error
            if size != expected.bytes or digest.hexdigest() != expected.sha256:
                raise TransferError(
                    "FILE_HASH",
                    "Файл отличается от подтверждённого. Начните новую передачу",
                    422,
                )
        with self.consent.connection() as db:
            # Cancellation/expiry during hashing must win over successful verification.
            _, lease, manifest = self._authorize(db, transfer_id, secret)
            db.execute("UPDATE cad_uploads SET ready=1 WHERE id=?", (transfer_id,))
            lease = db.execute(
                "SELECT * FROM cad_uploads WHERE id=?", (transfer_id,)
            ).fetchone()
            return self._progress(db, transfer_id, lease, manifest)

    def collect_expired(self):
        """Only this module's indexed scratch files; never original/project files."""
        with self.consent.connection() as db:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cad_publications'"
            ).fetchone():
                db.execute(
                    "UPDATE cad_uploads SET pinned=0 WHERE id IN (SELECT id FROM cad_publications WHERE deadline<=? AND status='processing')",
                    (self.consent.now(),),
                )
            rows = db.execute(
                """SELECT u.id FROM cad_uploads u LEFT JOIN cad_transfers t ON t.id=u.id
                WHERE u.pinned=0 AND (u.expires_at<=? OR t.id IS NULL OR t.status IN ('cancelled','denied'))""",
                (self.consent.now(),),
            ).fetchall()
            for row in rows:
                try:
                    indices = db.execute(
                        "SELECT file_index FROM cad_upload_files WHERE id=?",
                        (row["id"],),
                    ).fetchall()
                    for item in indices:
                        self._file(row["id"], item["file_index"]).unlink(
                            missing_ok=True
                        )
                    directory = self._directory(row["id"])
                    if directory.exists():
                        directory.rmdir()  # Refuse unknown files; no recursive deletion.
                except (OSError, TransferError):
                    # Keep reservation until an operator resolves the leftover;
                    # an unrelated expired package must not break every new upload.
                    logger.warning(
                        "AutoCAD staging cleanup requires attention: %s", row["id"]
                    )
                    continue
                db.execute("DELETE FROM cad_upload_chunks WHERE id=?", (row["id"],))
                db.execute("DELETE FROM cad_upload_files WHERE id=?", (row["id"],))
                db.execute("DELETE FROM cad_uploads WHERE id=?", (row["id"],))
