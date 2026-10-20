"""Журналируемая передача native-ticket в локальный проект; путь допускается только
через приватный desktop-канал.
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock
from typing import TYPE_CHECKING, TypedDict
from uuid import uuid4

from app.desktop.handoff_store import HandoffRecord, HandoffStatus, HandoffStore
from app.desktop.tickets import (
    ArchivedTicket,
    CapturedTicket,
    LiveQueryTicket,
    SessionTicket,
    copy_verified,
    direct_path,
    load_ticket,
    ticket_files,
)
from app.native_query.capture.capture_store import NativeCaptureStore
from app.native_query.live.live_client import LiveQueryClient
from app.native_query.live.live_inventory import load_inventory
from app.operations.contracts import OperationStatus
from app.projects.resource_scope import (
    project_resource_scope,
    register_project_resource,
)
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason
from app.shared.messages import message

if TYPE_CHECKING:
    from app.composition import Runtime

_LOGGER = logging.getLogger(__name__)
HANDOFF_WORKERS = 1


class HandoffReceipt(TypedDict):
    id: str
    entry: str
    status: str
    stage: str
    message: str | None
    project_path: str | None
    autocad_required: bool
    revision: int


class LocalHandoff:
    def __init__(self, runtime: Runtime, ticket_root: Path) -> None:
        self.runtime, self.ticket_root = runtime, ticket_root.absolute()
        direct_path(self.ticket_root, self.ticket_root)
        self.storage = runtime.cad_assets.config.storage
        self.storage.mkdir(parents=True, exist_ok=True)
        self.native_captures = NativeCaptureStore(
            Path(runtime.database_path + ".cad-archives")
        )
        self.database = self.storage / "desktop-handoffs.sqlite3"
        self.journal = HandoffStore(self.database)
        self._closed = False
        self.lock = RLock()
        self.pool = ThreadPoolExecutor(max_workers=HANDOFF_WORKERS)
        self.futures: dict[str, Future[None]] = {}
        runtime.project_repository.resource_lifecycle.configure_root(
            "transfer", self.ticket_root
        )
        runtime.application.projects.add_delete_listener(self.cancel_project)
        # Ранее прерванная очистка Transfers возобновляется только после явного
        # подключения этого приватного корня, а не по пути исходного DWG.
        runtime.project_repository.resource_lifecycle.drain()

    def cancel_project(self, project_id: str) -> None:
        with self.lock:
            for row in self.journal.records():
                if row.project_id == project_id:
                    self.journal.cancel(row.id)

    @staticmethod
    def _receipt(row: HandoffRecord) -> HandoffReceipt:
        return {
            "id": row.id,
            "entry": row.entry,
            "status": row.status.value,
            "stage": row.stage,
            "message": row.message,
            "autocad_required": row.autocad_required,
            "revision": row.revision,
            "project_path": (
                f"/projects/{row.project_id}/setup"
                if row.entry == "Drawing.autocad.json"
                else f"/projects/{row.project_id}/import?source=cad"
            )
            if row.status == HandoffStatus.NEEDS_REVIEW
            else None,
        }

    def get(self, identifier: str) -> HandoffReceipt:
        return self._receipt(self.journal.get(identifier))

    def wait(self, identifier: str, after: int, stop: Event) -> HandoffReceipt:
        return self._receipt(self.journal.wait(identifier, after, stop))

    def cancel_wait(self, stop: Event) -> None:
        self.journal.cancel_wait(stop)

    def list(self) -> list[HandoffReceipt]:
        return [self._receipt(record) for record in self.journal.records()]

    def resume(self, identifier: str) -> HandoffReceipt:
        record = self.journal.get(identifier)
        return self.submit(Path(record.path), resume_interrupted=True)

    def submit(self, path: Path, *, resume_interrupted: bool = False) -> HandoffReceipt:
        try:
            path, ticket, digest = load_ticket(path, self.ticket_root)
        except FileNotFoundError:
            checked = direct_path(path.absolute(), self.ticket_root)
            known = self.journal.find(sha256(str(checked).encode()).hexdigest())
            if known is not None:
                # После удаления файлы передачи уже очищены; сохраняем адресную
                # ошибку удалённого проекта вместо повторного импорта.
                self.runtime.application.get(known.project_id, lightweight=True)
            raise
        identifier = sha256(str(path).encode()).hexdigest()
        with self.lock:
            if self._closed:
                raise InterruptedError("Приложение закрывается")
            row = self.journal.find(identifier)
            if row and row.digest != digest:
                raise DomainFailure(FailureReason.HANDOFF_CAPTURE_CHANGED)
            if (
                row
                and row.status in {HandoffStatus.INTERRUPTED, HandoffStatus.CANCELLED}
                and not resume_interrupted
            ):
                return self.get(identifier)
            if row and row.status == HandoffStatus.NEEDS_REVIEW:
                for file in ticket_files(ticket):
                    copy_verified(
                        direct_path(path.parent / file.name, self.ticket_root),
                        None,
                        file.bytes,
                        file.sha256,
                        lambda: None,
                    )
                # Известная квитанция не должна заново создать проект, удалённый пользователем.
                project = self.runtime.application.get(row.project_id, lightweight=True)
                if (
                    project.source_file is None
                    or project.source_file.content_sha256
                    != ticket.manifest.files[0].sha256
                ):
                    raise DomainFailure(FailureReason.HANDOFF_SNAPSHOT_MISMATCH)
                return self.get(identifier)
            future = self.futures.get(identifier)
            if future is not None and not future.done():
                return self.get(identifier)
            if row and row.status == HandoffStatus.PROCESSING:
                return self.get(identifier)
            if row is None:
                self.journal.reserve(
                    identifier,
                    path,
                    digest,
                    str(uuid4()),
                    ticket.manifest.entry,
                    autocad_required=isinstance(ticket, LiveQueryTicket),
                )
            else:
                self.journal.queue(identifier)
            self.futures[identifier] = self.pool.submit(self.run, identifier)
        return self.get(identifier)

    def check(self, identifier: str) -> None:
        if self.journal.get(identifier).status not in {
            HandoffStatus.QUEUED,
            HandoffStatus.PROCESSING,
        }:
            raise InterruptedError("Подготовка прервана или отменена")

    def cancel(self, identifier: str) -> HandoffReceipt:
        with self.lock:
            self.journal.cancel(identifier)
        return self.get(identifier)

    def stage(self, identifier: str, text: str) -> None:
        self.check(identifier)
        self.journal.stage(identifier, text)
        self.check(identifier)

    def run_live(
        self, identifier: str, row: HandoffRecord, path: Path, ticket: CapturedTicket
    ) -> None:
        """Использовать native-захват напрямую без промежуточного DXF и повторного
        чтения XREF.
        """
        capture = ticket.manifest.files[0]
        source = direct_path(path.parent / capture.name, self.ticket_root)
        self.stage(identifier, "Открываем геометрию текущего чертежа")
        self.check(identifier)
        session = None
        native_capture_root = None
        if isinstance(ticket, ArchivedTicket):
            self.stage(identifier, "Сохраняем чертёж и подосновы проекта")
            native_capture_root = self.native_captures.retain(
                row.project_id,
                capture.sha256,
                ticket.plugin_version,
                path.parent,
                self.ticket_root,
                ticket.native_session,
                lambda: self.check(identifier),
            )
            session = self.native_captures.retained_session(
                row.project_id, ticket.live_session
            )
        elif isinstance(ticket, LiveQueryTicket):
            client = LiveQueryClient(pid=ticket.live_session.pid)
            session = client.reconnect(ticket.live_session.session_id)
            if (
                session != ticket.live_session
                or session.snapshot_sha256 != capture.sha256
            ):
                raise DomainFailure(FailureReason.HANDOFF_SESSION_MISMATCH)
            load_inventory(session)
        if isinstance(ticket, SessionTicket):
            self.stage(identifier, "Сохраняем native-комплект AutoCAD")
            native_capture_root = self.native_captures.retain(
                row.project_id,
                capture.sha256,
                ticket.plugin_version,
                path.parent,
                self.ticket_root,
                ticket.native_session,
                lambda: self.check(identifier),
            )
        self.check(identifier)
        project = self.runtime.application.ensure_import_project(
            row.project_id, Path(ticket.source_name).stem
        )
        relative = path.parent.relative_to(self.ticket_root)
        if len(relative.parts) == 1:
            register_project_resource("transfer", relative.name)
        if project.source_file is None:
            self.stage(identifier, message("handoff_reading_objects"))
            project = self.runtime.application.import_autocad_live_file(
                project.id,
                ticket.source_name,
                source,
                capture.sha256,
                autocad_version=ticket.producer.autocad_version,
                target=ticket.producer.target,
                check_cancelled=lambda: self.check(identifier),
                native_capture_root=native_capture_root,
            )
        elif project.source_file.content_sha256 != capture.sha256:
            raise DomainFailure(FailureReason.HANDOFF_SOURCE_CHANGED)
        self.stage(identifier, message("handoff_saving_project"))
        if session is not None:
            if not isinstance(ticket, ArchivedTicket):
                client.inspect(session)
            # Карта уже опубликована дочерним процессом. Привязка сеанса меняет
            # только его поле в той же CAS-ревизии и не перечитывает CAD-граф.
            project = self.runtime.application.get(project.id, lightweight=True)
            source_file = project.source_file
            if source_file is None:
                raise DomainFailure(FailureReason.HANDOFF_SNAPSHOT_MISSING)
            if source_file.content_sha256 != session.snapshot_sha256:
                raise DomainFailure(FailureReason.HANDOFF_SOURCE_CHANGED)
            if source_file.native_session != session:
                project = self.runtime.application.bind_native_session(project, session)
        with self.lock:
            self.check(identifier)
            self.journal.publish(identifier, "Геометрия открыта")

    def run(self, identifier: str) -> None:
        if not self.journal.claim(identifier):
            return
        try:
            self.stage(identifier, "Проверяем снимок AutoCAD")
            row = self.journal.get(identifier)
            path, ticket, digest = load_ticket(Path(row.path), self.ticket_root)
            if digest != row.digest:
                raise DomainFailure(FailureReason.HANDOFF_CHANGED)
            # Проверяем оригиналы и при повторе: старая квитанция не скрывает изменение файлов.
            files = ticket_files(ticket)
            for index, file in enumerate(files, start=1):
                self.stage(
                    identifier,
                    message(
                        "handoff_verifying_files",
                        {"current": index, "total": len(files)},
                    ),
                )
                copy_verified(
                    direct_path(path.parent / file.name, self.ticket_root),
                    None,
                    file.bytes,
                    file.sha256,
                    lambda: self.check(identifier),
                )
            with project_resource_scope(
                self.runtime.project_repository, row.project_id, reserved=True
            ):
                self.run_live(identifier, row, path, ticket)
        except Exception as error:
            if self.journal.get(identifier).status in {
                HandoffStatus.INTERRUPTED,
                HandoffStatus.CANCELLED,
            }:
                return
            # Стадия и трассировка сбоя сохраняются в приватном desktop-журнале для диагностики.
            # Исходник, содержимое ticket и параметры сеанса в журнал не записываются.
            _LOGGER.exception("AutoCAD handoff %s failed", identifier)
            self.journal.fail(
                identifier,
                public_failure_message(error, "handoff_failed"),
            )

    def close(self) -> None:
        with self.lock:
            if self._closed:
                return
            self._closed = True
            self.runtime.application.projects.remove_delete_listener(
                self.cancel_project
            )
            self.journal.interrupt_active("Приложение закрыто")
            self.journal.close()
            operations = self.journal.records()
        for row in operations:
            if row.operation_id is None:
                continue
            try:
                operation = self.runtime.application.operations.lifecycle.get(
                    row.project_id, row.operation_id
                )
                if operation.status in {
                    OperationStatus.QUEUED,
                    OperationStatus.RUNNING,
                }:
                    self.runtime.application.operations.lifecycle.update(
                        operation.id,
                        status=OperationStatus.INTERRUPTED,
                        progress=operation.progress,
                        stage="Приложение закрыто",
                        progress_mode=operation.progress_mode,
                    )
            except KeyError:
                pass
        self.pool.shutdown(wait=True, cancel_futures=True)
