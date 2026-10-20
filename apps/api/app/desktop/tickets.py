"""Проверка native-ticket и файлов одного захвата внутри приватного staging-каталога
AutoCAD.
"""

import hashlib
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.cad_delivery.contracts import PublicationRequest
from app.native_query.capture.capture_contracts import (
    MAX_CAPTURE_DISPLAY_BYTES,
    SessionTransfer,
    SessionTransferFile,
)
from app.native_query.live.live_client import LiveSession


class LiveCaptureFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["Drawing.autocad.json"]
    kind: Literal["live_capture"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0, le=MAX_CAPTURE_DISPLAY_BYTES)


class LiveCaptureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry: Literal["Drawing.autocad.json"]
    files: tuple[LiveCaptureFile]


class CaptureTicketFields(BaseModel):
    """One native capture; legacy DXF pairs remain read-only compatibility."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    manifest: LiveCaptureManifest
    producer: PublicationRequest
    source_name: str

    @field_validator("source_name")
    @classmethod
    def safe_source_name(cls, name: str) -> str:
        if (
            not 1 <= len(name) <= 240
            or any(ord(char) < 32 or char in "/\\:" for char in name)
            or not name.lower().endswith((".dwg", ".dxf"))
        ):
            raise ValueError("Имя исходного чертежа недопустимо")
        return name


class LiveTicket(CaptureTicketFields):
    format: Literal["green-atlas.transfer/2"] = Field(alias="schema")
    plugin_version: Literal[
        "0.1.37", "0.1.38", "0.1.40", "0.1.41", "0.1.42", "0.1.43", "0.1.44", "0.1.45"
    ]


class SessionTicket(CaptureTicketFields):
    """Display snapshot and exact native package from one synchronous command."""

    format: Literal["green-atlas.transfer/3"] = Field(alias="schema")
    plugin_version: Literal["0.1.39"]
    native_session: SessionTransfer


class SessionTicketFields(CaptureTicketFields):
    plugin_version: str
    live_session: LiveSession

    @model_validator(mode="after")
    def same_capture(self) -> Self:
        session = self.live_session
        if (
            session.plugin_version != self.plugin_version
            or session.snapshot_sha256 != self.manifest.files[0].sha256
            or not session.inventory_path
            or not session.inventory_sha256
        ):
            raise ValueError("Расчётный сеанс не соответствует передаваемому захвату")
        return self


class LiveQueryTicket(SessionTicketFields):
    format: Literal["green-atlas.transfer/4"] = Field(alias="schema")
    plugin_version: Literal["0.1.40", "0.1.41", "0.1.42", "0.1.43", "0.1.44", "0.1.45"]


class ArchivedTicket(SessionTicketFields):
    """A complete same-capture package; the editor PID is provenance only."""

    format: Literal["green-atlas.transfer/5"] = Field(alias="schema")
    plugin_version: Literal["0.1.46", "0.1.47", "0.1.48"]
    native_session: SessionTransfer

    @model_validator(mode="after")
    def retained_inputs(self) -> Self:
        files = {entry.name: entry for entry in self.native_session.files}
        display = files.get("Drawing.autocad.json")
        inventory = files.get("Drawing.autocad.json.inventory.json")
        capture = self.manifest.files[0]
        if (
            display is None
            or inventory is None
            or (display.sha256, display.bytes) != (capture.sha256, capture.bytes)
            or inventory.sha256 != self.live_session.inventory_sha256
        ):
            raise ValueError("Архив не содержит исходные сведения того же захвата")
        return self


CapturedTicket = LiveTicket | SessionTicket | LiveQueryTicket | ArchivedTicket
Ticket = CapturedTicket
_TICKET: TypeAdapter[Ticket] = TypeAdapter(Ticket)


def ticket_files(
    ticket: Ticket,
) -> tuple[LiveCaptureFile | SessionTransferFile, ...]:
    return (
        *ticket.manifest.files,
        *(
            (
                entry
                for entry in ticket.native_session.files
                if entry.name != ticket.manifest.entry
            )
            if isinstance(ticket, (SessionTicket, ArchivedTicket))
            else ()
        ),
    )


def direct_path(path: Path, root: Path) -> Path:
    path = path.absolute()
    if not path.is_relative_to(root) or any(
        p.is_symlink() for p in (path, *path.parents)
    ):
        raise ValueError("Снимок должен находиться в каталоге передачи AutoCAD")
    if sys.platform == "win32":
        from app.native_query.transport import job_files

        if path == root:
            job_files.validate_directory(root)
            return path
        return job_files.checked_path(root, path.relative_to(root).as_posix())
    return path


def read_regular(path: Path) -> BinaryIO:
    if sys.platform == "win32":
        from app.native_query.transport import job_files

        return job_files.open_regular(path)
    # До чтения фиксируем открытый inode и отвергаем ссылки и устройства. Родительские
    # пути ограничены приватным staging-каталогом пользователя, а не корнями браузера.
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("Снимок содержит не обычный файл")
    return os.fdopen(descriptor, "rb")


def load_ticket(path: Path, root: Path) -> tuple[Path, Ticket, str]:
    path = direct_path(path, root)
    if path.name != "transfer.gatransfer":
        raise ValueError("Ожидается снимок, подготовленный плагином AutoCAD")
    with read_regular(path) as source:
        payload = source.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("Файл передачи слишком велик")
    return (
        path,
        _TICKET.validate_json(payload),
        hashlib.sha256(payload).hexdigest(),
    )


def copy_verified(
    path: Path, target: Path | None, size: int, digest: str, check: Callable[[], None]
) -> None:
    actual, count = hashlib.sha256(), 0
    with read_regular(path) as source:
        destination = target.open("xb") if target else None
        try:
            while chunk := source.read(1024 * 1024):
                check()
                count += len(chunk)
                if count > size:
                    raise ValueError(
                        "Снимок изменился. Подготовьте его заново в AutoCAD"
                    )
                actual.update(chunk)
                if destination:
                    destination.write(chunk)
        finally:
            if destination:
                destination.close()
    if count != size or actual.hexdigest() != digest:
        raise ValueError("Снимок изменился. Подготовьте его заново в AutoCAD")
