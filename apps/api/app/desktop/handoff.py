"""Durable local ticket→native compiler→existing project/intake orchestration.

No HTTP upload, remote identity, fake approval, or alternate geometry reader.
Only the desktop-native control channel can submit a path to this service.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from threading import RLock
from uuid import uuid4

from app.cad_import.policy import ConversionPolicy
from app.cad_import.process import run_converter
from app.cad_intake.capacity import inspection_capacity
from app.cad_intake.contracts import CadDrawingEntry, CadIntakeRequest
from app.desktop.tickets import copy_verified, direct_path, load_ticket
from app.operations.contracts import OperationKind, OperationStatus
from app.shared.python_worker import worker_command


class LocalHandoff:
    def __init__(self, runtime, ticket_root: Path):
        self.runtime, self.ticket_root = runtime, ticket_root.absolute()
        direct_path(self.ticket_root, self.ticket_root)
        self.storage = runtime.cad_intake.config.storage
        self.storage.mkdir(parents=True, exist_ok=True)
        self.database = self.storage / "desktop-handoffs.sqlite3"
        self.lock, self.pool, self.futures = (
            RLock(),
            ThreadPoolExecutor(max_workers=1),
            {},
        )
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS local_handoffs(
                id TEXT PRIMARY KEY, path TEXT NOT NULL, digest TEXT NOT NULL,
                project_id TEXT NOT NULL, entry TEXT NOT NULL, status TEXT NOT NULL,
                stage TEXT NOT NULL, message TEXT, operation_id TEXT
            )""")
            db.execute(
                "UPDATE local_handoffs SET status='interrupted',stage='Подготовка прервана' WHERE status IN ('queued','processing')"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM local_handoffs WHERE id=?", (identifier,)
            ).fetchone()
        if row is None:
            raise KeyError("Передача не найдена")
        return {
            "id": row["id"],
            "entry": row["entry"],
            "status": row["status"],
            "stage": row["stage"],
            "message": row["message"],
            "project_path": f"/projects/{row['project_id']}/import?source=cad"
            if row["status"] == "needs_review"
            else None,
        }

    def submit(self, path: Path):
        path, ticket, digest = load_ticket(path, self.ticket_root)
        identifier = sha256(str(path).encode()).hexdigest()
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM local_handoffs WHERE id=?", (identifier,)
            ).fetchone()
            if row and row["digest"] != digest:
                raise ValueError(
                    "Состав сохранённой передачи изменился. Создайте новый снимок"
                )
            if row and row["status"] == "needs_review":
                for file in ticket.manifest.files:
                    copy_verified(
                        direct_path(path.parent / file.name, self.ticket_root),
                        None,
                        file.bytes,
                        file.sha256,
                        lambda: None,
                    )
                # A known receipt must not silently recreate a project deleted by the user.
                self.runtime.application.get(row["project_id"], lightweight=True)
                operation = None
                if row["operation_id"]:
                    try:
                        operation = self.runtime.cad_intake.lifecycle.get(
                            row["project_id"], row["operation_id"]
                        )
                    except KeyError:
                        pass
                if operation is not None and operation.status not in {
                    OperationStatus.FAILED,
                    OperationStatus.INTERRUPTED,
                    OperationStatus.CANCELLED,
                }:
                    return self.get(identifier)
                # Repeating GAOPEN is an explicit retry. If the app was closed
                # after publication, reuse the project and prepared immutable
                # package, but create a fresh durable intake operation.
            future = self.futures.get(identifier)
            if future is not None and not future.done():
                return self.get(identifier)
            if row is None:
                db.execute(
                    "INSERT INTO local_handoffs VALUES(?,?,?,?,?,'queued','Ожидаем подготовку',NULL,NULL)",
                    (
                        identifier,
                        str(path),
                        digest,
                        str(uuid4()),
                        ticket.manifest.entry,
                    ),
                )
            else:
                db.execute(
                    "UPDATE local_handoffs SET status='queued',stage='Повторяем подготовку',message=NULL WHERE id=?",
                    (identifier,),
                )
            db.commit()
            self.futures[identifier] = self.pool.submit(self.run, identifier)
        return self.get(identifier)

    def check(self, identifier):
        if self.get(identifier)["status"] == "cancelled":
            raise InterruptedError("Подготовка отменена")

    def cancel(self, identifier):
        with self.lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM local_handoffs WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise KeyError("Передача не найдена")
            if row["status"] != "needs_review":
                db.execute(
                    "UPDATE local_handoffs SET status='cancelled',stage='Подготовка отменена' WHERE id=?",
                    (identifier,),
                )
        return self.get(identifier)

    def stage(self, identifier, text):
        self.check(identifier)
        with self.connect() as db:
            db.execute(
                "UPDATE local_handoffs SET stage=?,status='processing' WHERE id=? AND status!='cancelled'",
                (text, identifier),
            )
        self.check(identifier)

    def run(self, identifier):
        try:
            self.stage(identifier, "Проверяем снимок AutoCAD")
            with self.connect() as db:
                row = db.execute(
                    "SELECT * FROM local_handoffs WHERE id=?", (identifier,)
                ).fetchone()
            path, ticket, digest = load_ticket(Path(row["path"]), self.ticket_root)
            if digest != row["digest"]:
                raise ValueError("Файл передачи изменился")
            root_id = "upload-" + row["project_id"].replace("-", "")
            uploads = self.storage / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            destination = direct_path(uploads / root_id, uploads.absolute())
            marker = {"ticket_sha256": digest, "handoff": identifier}
            # Validate originals even on a repeat; a stale receipt cannot conceal mutation.
            for file in ticket.manifest.files:
                copy_verified(
                    direct_path(path.parent / file.name, self.ticket_root),
                    None,
                    file.bytes,
                    file.sha256,
                    lambda: self.check(identifier),
                )
            if destination.exists():
                if (
                    json.loads((destination / "desktop-ticket.json").read_text())
                    != marker
                ):
                    raise ValueError("Сохранённый комплект принадлежит другой передаче")
            else:
                self.stage(identifier, "Подготавливаем локальный проект")
                with tempfile.TemporaryDirectory(
                    prefix=".desktop-", dir=uploads
                ) as folder:
                    folder = Path(folder)
                    for file in ticket.manifest.files:
                        copy_verified(
                            direct_path(path.parent / file.name, self.ticket_root),
                            folder / file.name,
                            file.bytes,
                            file.sha256,
                            lambda: self.check(identifier),
                        )
                    request = folder / "compile-request.json"
                    request.write_text(
                        json.dumps(
                            {
                                "directory": str(folder),
                                "manifest": ticket.manifest.model_dump(),
                                "producer": ticket.producer.model_dump(),
                                "plugin_version": ticket.plugin_version,
                                "root_id": root_id,
                            }
                        )
                    )
                    with inspection_capacity(lambda: self.check(identifier)):
                        result = run_converter(
                            worker_command(
                                "app.cad_delivery.compile_worker", str(request)
                            ),
                            folder / "upload.json",
                            folder / "compile.log",
                            ConversionPolicy(
                                timeout_seconds=240,
                                max_output_bytes=1024 * 1024,
                                # Large native ledgers temporarily coexist as raw JSON,
                                # validated models and the compiled snapshot. Keep the
                                # same supervised budget as CAD preparation until the
                                # compiler is converted to a streaming pipeline.
                                max_memory_bytes=4 * 1024**3,
                            ),
                            environment={
                                **os.environ,
                                "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                            },
                            check_cancelled=lambda: self.check(identifier),
                        )
                    if result.exit_code:
                        logs = self.storage / "desktop-logs"
                        logs.mkdir(exist_ok=True)
                        (logs / f"{identifier}.log").write_bytes(
                            (folder / "compile.log").read_bytes()
                        )
                        raise ValueError("Снимок не подготовлен. Диагностика сохранена")
                    (folder / "desktop-ticket.json").write_text(json.dumps(marker))
                    self.check(identifier)
                    folder.rename(destination)
            with self.lock:
                self.check(identifier)
                project = self.runtime.application.ensure_import_project(
                    row["project_id"], Path(ticket.manifest.entry).stem
                )
                intake = self.runtime.cad_intake
                operation = intake.lifecycle.latest(
                    project.id, OperationKind.INSPECT_CAD_PACKAGE
                )
                if operation is not None and (
                    operation.cad_intake is None
                    or operation.cad_intake.request.root_id != root_id
                ):
                    raise ValueError("Исходные данные проекта уже изменены")
                if operation is None or operation.status in {
                    OperationStatus.FAILED,
                    OperationStatus.INTERRUPTED,
                    OperationStatus.CANCELLED,
                }:
                    entries = [
                        file for file in ticket.manifest.files if file.kind == "drawing"
                    ]
                    entry = next(
                        file for file in entries if file.name == ticket.manifest.entry
                    )
                    operation = intake.start(
                        project.id,
                        CadIntakeRequest(
                            root_id=root_id,
                            entry=entry.name,
                            entry_sha256=entry.sha256,
                            additional_entries=[
                                CadDrawingEntry(path=file.name, sha256=file.sha256)
                                for file in entries
                                if file.name != entry.name
                            ],
                        ),
                    )
                with self.connect() as db:
                    db.execute(
                        "UPDATE local_handoffs SET status='needs_review',stage='Проект создан',operation_id=? WHERE id=?",
                        (operation.id, identifier),
                    )
            # Existing intake owns its progress/stop/retry once the project is visible.
            intake.run(operation.id)
        except Exception as error:
            with self.connect() as db:
                db.execute(
                    "UPDATE local_handoffs SET status='failed',stage='Подготовка остановлена',message=? WHERE id=? AND status NOT IN ('cancelled','needs_review')",
                    (
                        str(error)
                        if isinstance(error, (ValueError, InterruptedError))
                        else "Не удалось открыть снимок. Повторите подготовку",
                        identifier,
                    ),
                )

    def close(self):
        with self.connect() as db:
            db.execute(
                "UPDATE local_handoffs SET status='cancelled',stage='Приложение закрыто' WHERE status IN ('queued','processing')"
            )
            operations = db.execute(
                "SELECT project_id,operation_id FROM local_handoffs WHERE operation_id IS NOT NULL"
            ).fetchall()
        for row in operations:
            try:
                operation = self.runtime.cad_intake.lifecycle.get(
                    row["project_id"], row["operation_id"]
                )
                if operation.status in {
                    OperationStatus.QUEUED,
                    OperationStatus.RUNNING,
                }:
                    self.runtime.cad_intake.lifecycle.cancel(
                        row["project_id"], row["operation_id"]
                    )
            except KeyError:
                pass
        self.pool.shutdown(wait=True, cancel_futures=True)
