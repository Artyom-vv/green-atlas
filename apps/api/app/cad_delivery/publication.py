"""Idempotent handoff from verified transfer to the existing CAD review workflow."""

import json
import logging
import os
import shutil
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.cad_delivery.contracts import PublicationReceipt, PublicationRequest
from app.cad_delivery.store import TransferError
from app.cad_delivery.uploads import CHUNK_BYTES, UploadStore
from app.cad_import.policy import ConversionPolicy
from app.cad_import.process import run_converter
from app.cad_intake.capacity import inspection_capacity
from app.cad_intake.contracts import CadDrawingEntry, CadIntakeRequest, CadUploadPackage
from app.operations.contracts import OperationKind, OperationStatus
from app.projects.contracts import Project
from app.shared.python_worker import worker_command

logger = logging.getLogger(__name__)


class TransferPublication:
    def __init__(self, uploads: UploadStore, runtime):
        self.uploads, self.runtime = uploads, runtime
        self.consent = uploads.consent
        with self.consent.connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cad_publications (
                id TEXT PRIMARY KEY, producer TEXT NOT NULL, project_id TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL, operation_id TEXT,
                claim TEXT NOT NULL, deadline INTEGER NOT NULL, message TEXT
            )""")

    @staticmethod
    def _receipt(row):
        published = row["status"] == "needs_review"
        return PublicationReceipt(
            id=row["id"],
            status=row["status"],
            stage=row["stage"],
            project_id=row["project_id"] if published else None,
            project_path=f"/projects/{row['project_id']}/import?source=cad"
            if published
            else None,
            operation_id=row["operation_id"],
            message=row["message"],
        )

    def status(self, identifier: str, token: str):
        with self.consent.connection() as db:
            grant = self.consent._get(db, identifier)
            self.consent._authenticate(grant, token)
            row = db.execute(
                "SELECT * FROM cad_publications WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise TransferError(
                    "PUBLICATION_NOT_STARTED", "Создание проекта ещё не запущено", 404
                )
            if row["status"] == "processing" and row["deadline"] <= self.consent.now():
                db.execute(
                    "UPDATE cad_publications SET status='failed',stage=?,message=? WHERE id=?",
                    ("Подготовка прервана", "Повторите создание проекта", identifier),
                )
                db.execute("UPDATE cad_uploads SET pinned=0 WHERE id=?", (identifier,))
                row = db.execute(
                    "SELECT * FROM cad_publications WHERE id=?", (identifier,)
                ).fetchone()
            return self._receipt(row)

    def start(self, identifier: str, token: str, producer: PublicationRequest):
        with self.consent.connection() as db:
            grant = self.consent._get(db, identifier)
            self.consent._authenticate(grant, token)
            row = db.execute(
                "SELECT * FROM cad_publications WHERE id=?", (identifier,)
            ).fetchone()
            if row:
                if row["producer"] != producer.model_dump_json():
                    raise TransferError(
                        "PRODUCER_CHANGED", "Повтор содержит другую версию AutoCAD"
                    )
                if row["status"] == "needs_review":
                    try:
                        self.runtime.project_repository.get(
                            row["project_id"], lightweight=True
                        )
                    except KeyError as error:
                        raise TransferError(
                            "PROJECT_REMOVED",
                            "Этот проект удалён. Для нового начните новую передачу",
                            410,
                        ) from error
                    return self._receipt(row), None
                if (
                    row["status"] == "processing"
                    and row["deadline"] > self.consent.now()
                ):
                    return self._receipt(row), None
            _, lease, _ = self.uploads._authorize(db, identifier, token)
            if lease is None or not lease["ready"]:
                raise TransferError(
                    "UPLOAD_INCOMPLETE", "Сначала завершите передачу файлов"
                )
            claim = uuid4().hex
            if row is None:
                db.execute(
                    "INSERT INTO cad_publications VALUES(?,?,?,'processing',?,NULL,?,?,NULL)",
                    (
                        identifier,
                        producer.model_dump_json(),
                        str(uuid4()),
                        "Подготавливаем AutoCAD-снимок",
                        claim,
                        int(self.consent.now()) + 600,
                    ),
                )
            else:
                db.execute(
                    "UPDATE cad_publications SET status='processing',stage=?,claim=?,deadline=?,message=NULL WHERE id=?",
                    (
                        "Повторяем подготовку проекта",
                        claim,
                        int(self.consent.now()) + 600,
                        identifier,
                    ),
                )
            db.execute("UPDATE cad_uploads SET pinned=1 WHERE id=?", (identifier,))
            return self._receipt(
                db.execute(
                    "SELECT * FROM cad_publications WHERE id=?", (identifier,)
                ).fetchone()
            ), claim

    def _check(self, identifier, token, claim):
        with self.consent.connection() as db:
            grant, _, manifest = self.uploads._authorize(db, identifier, token)
            row = db.execute(
                "SELECT * FROM cad_publications WHERE id=?", (identifier,)
            ).fetchone()
            if (
                row is None
                or row["claim"] != claim
                or row["status"] != "processing"
                or row["deadline"] <= self.consent.now()
            ):
                raise TransferError(
                    "PUBLICATION_INTERRUPTED",
                    "Подготовка прервана. Повторите создание проекта",
                )
            return row, grant, manifest

    def _compile(self, directory, manifest, producer, version, root_id, check):
        task = directory / "compile-request.json"
        task.write_text(
            json.dumps(
                {
                    "directory": str(directory),
                    "manifest": manifest.model_dump(),
                    "producer": producer.model_dump(),
                    "plugin_version": version,
                    "root_id": root_id,
                }
            ),
            encoding="utf-8",
        )
        with inspection_capacity(check):
            result = run_converter(
                worker_command("app.cad_delivery.compile_worker", str(task)),
                directory / "upload.json",
                directory / "compile.log",
                ConversionPolicy(
                    timeout_seconds=240,
                    max_output_bytes=1024 * 1024,
                    # Native snapshots can exceed the generic 2 GiB conversion
                    # budget while raw, validated and compiled representations
                    # coexist. This remains process-tree supervised.
                    max_memory_bytes=4 * 1024**3,
                ),
                environment={
                    **os.environ,
                    "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
                    "PYTHONUTF8": "1",
                },
                check_cancelled=check,
            )
        if result.exit_code != 0:
            raise TransferError(
                "NATIVE_COMPILE_FAILED",
                "Не удалось подготовить снимок. Диагностика сохранена",
            )

    def run(self, identifier: str, token: str, claim: str):
        operation_id = None
        directory = None
        try:
            record, grant, manifest = self._check(identifier, token, claim)
            producer = PublicationRequest.model_validate_json(record["producer"])
            root_id = f"upload-{identifier.replace('-', '')}"
            root = self.runtime.cad_intake.config.storage / "uploads"
            root.mkdir(parents=True, exist_ok=True)
            destination = root / root_id
            if root.is_symlink() or destination.is_symlink():
                raise TransferError(
                    "PUBLICATION_CONFLICT", "Каталог публикации недоступен"
                )
            directory = root / f".bridge-{identifier}-{claim}"
            directory.mkdir(mode=0o700)
            if destination.exists():
                marker = json.loads((destination / "bridge-transfer.json").read_text())
                if marker != {
                    "id": identifier,
                    "manifest": manifest.digest(),
                    "producer": producer.model_dump(),
                }:
                    raise TransferError(
                        "PUBLICATION_CONFLICT",
                        "Сохранённый комплект не соответствует передаче",
                    )
            else:
                for index, item in enumerate(manifest.files):
                    self._check(identifier, token, claim)
                    digest, size = sha256(), 0
                    with (
                        self.uploads._file(identifier, index).open("rb") as source,
                        (directory / item.name).open("xb") as target,
                    ):
                        while part := source.read(CHUNK_BYTES):
                            self._check(identifier, token, claim)
                            size += len(part)
                            if size > item.bytes:
                                raise TransferError(
                                    "FILE_CHANGED", "Принятый комплект изменился"
                                )
                            digest.update(part)
                            target.write(part)
                    if size != item.bytes or digest.hexdigest() != item.sha256:
                        raise TransferError(
                            "FILE_CHANGED", "Принятый комплект изменился"
                        )
                self._compile(
                    directory,
                    manifest,
                    producer,
                    grant["plugin_version"],
                    root_id,
                    lambda: self._check(identifier, token, claim),
                )
                (directory / "bridge-transfer.json").write_text(
                    json.dumps(
                        {
                            "id": identifier,
                            "manifest": manifest.digest(),
                            "producer": producer.model_dump(),
                        }
                    ),
                    encoding="utf-8",
                )
            with self.consent.connection() as db:
                self.uploads._authorize(db, identifier, token)
                current = db.execute(
                    "SELECT * FROM cad_publications WHERE id=?", (identifier,)
                ).fetchone()
                if (
                    current["claim"] != claim
                    or current["deadline"] <= self.consent.now()
                ):
                    raise TransferError(
                        "PUBLICATION_INTERRUPTED", "Подготовка прервана"
                    )
                if not destination.exists():
                    directory.rename(destination)
                package = CadUploadPackage.model_validate_json(
                    (destination / "upload.json").read_bytes()
                )
                project_id = current["project_id"]
                try:
                    self.runtime.project_repository.get(project_id, lightweight=True)
                except KeyError:
                    self.runtime.project_repository.create(
                        Project(id=project_id, name=Path(manifest.entry).stem)
                    )
                intake = self.runtime.cad_intake
                operation = intake.lifecycle.latest(
                    project_id, OperationKind.INSPECT_CAD_PACKAGE
                )
                if operation is not None and (
                    operation.cad_intake is None
                    or operation.cad_intake.request.root_id != root_id
                    or operation.cad_intake.request.entry != manifest.entry
                ):
                    raise TransferError(
                        "PROJECT_CHANGED",
                        "Исходные данные проекта уже изменены. Они сохранены без замены",
                    )
                if operation is None or operation.status in {
                    OperationStatus.FAILED,
                    OperationStatus.INTERRUPTED,
                    OperationStatus.CANCELLED,
                }:
                    entry = next(f for f in package.entries if f.path == manifest.entry)
                    operation = intake.start(
                        project_id,
                        CadIntakeRequest(
                            root_id=root_id,
                            entry=entry.path,
                            entry_sha256=entry.sha256,
                            additional_entries=[
                                CadDrawingEntry(path=f.path, sha256=f.sha256)
                                for f in package.entries
                                if f.path != entry.path
                            ],
                        ),
                    )
                operation_id = operation.id
                db.execute(
                    "UPDATE cad_publications SET status='needs_review',stage=?,operation_id=?,message=NULL WHERE id=?",
                    (
                        "Проект создан. Проверяем исходные данные",
                        operation_id,
                        identifier,
                    ),
                )
                db.execute("UPDATE cad_uploads SET pinned=0 WHERE id=?", (identifier,))
        except Exception as error:
            logger.exception("AutoCAD publication failed: %s", identifier)
            with self.consent.connection() as db:
                db.execute(
                    "UPDATE cad_publications SET status='failed',stage=?,message=? WHERE id=? AND claim=? AND status='processing'",
                    (
                        "Подготовка остановлена",
                        str(error)
                        if isinstance(error, TransferError)
                        else "Повторите создание проекта. Принятые файлы сохранены",
                        identifier,
                        claim,
                    ),
                )
                db.execute(
                    "UPDATE cad_uploads SET pinned=0 WHERE id=? AND EXISTS (SELECT 1 FROM cad_publications WHERE id=? AND claim=?)",
                    (identifier, identifier, claim),
                )
            return
        finally:
            if (
                directory is not None
                and directory.exists()
                and not directory.is_symlink()
            ):
                # Preserve bounded diagnostics, not repeated 2-GB failed copies.
                try:
                    log = directory / "compile.log"
                    if log.is_file():
                        logs = self.runtime.cad_intake.config.storage / "transfer-logs"
                        logs.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(log, logs / f"{identifier}.log")
                    shutil.rmtree(directory)
                except OSError:
                    logger.exception(
                        "AutoCAD publication scratch cleanup failed: %s", identifier
                    )
        if operation_id:
            # Intake owns its own durable progress, cancellation and bounded native worker.
            self.runtime.cad_intake.run(operation_id)

    def resume_review(self, identifier: str, token: str):
        """Repeat POST can recover the commit→worker-dispatch crash gap."""
        receipt = self.status(identifier, token)
        if receipt.status != "needs_review" or not receipt.operation_id:
            return
        intake = self.runtime.cad_intake
        operation = intake.lifecycle.get(str(receipt.project_id), receipt.operation_id)
        if operation.status == OperationStatus.INTERRUPTED:
            project = self.runtime.project_repository.get(
                str(receipt.project_id), lightweight=True
            )
            if project.plan is not None or project.map_ready:
                return  # Never replace a user-edited project while retrying delivery.
            operation = intake.start(
                str(receipt.project_id), operation.cad_intake.request
            )
            with self.consent.connection() as db:
                db.execute(
                    "UPDATE cad_publications SET operation_id=? WHERE id=?",
                    (operation.id, identifier),
                )
        if operation.status == OperationStatus.QUEUED:
            intake.run(operation.id)
