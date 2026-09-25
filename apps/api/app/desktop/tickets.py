"""Read a native ticket only inside the private AutoCAD staging directory."""

import hashlib
import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from app.cad_delivery.contracts import PublicationRequest, TransferManifest
from app.native_query.capture_contracts import SessionTransfer
from app.native_query.live_client import LiveSession


class NativeTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    format: Literal["green-atlas.transfer/1"] = Field(alias="schema")
    plugin_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=32)
    manifest: TransferManifest
    producer: PublicationRequest


class LiveCaptureFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Literal["Drawing.autocad.json"]
    kind: Literal["live_capture"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0, le=768 * 1024**2)


class LiveCaptureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry: Literal["Drawing.autocad.json"]
    files: tuple[LiveCaptureFile]


class LiveTicket(BaseModel):
    """One native capture; legacy DXF pairs remain read-only compatibility."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    format: Literal["green-atlas.transfer/2"] = Field(alias="schema")
    plugin_version: Literal["0.1.37", "0.1.38", "0.1.40"]
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


class SessionTicket(LiveTicket):
    """Display snapshot and exact native package from one synchronous command."""

    format: Literal["green-atlas.transfer/3"] = Field(alias="schema")
    plugin_version: Literal["0.1.39"]
    native_session: SessionTransfer


class LiveQueryTicket(LiveTicket):
    format: Literal["green-atlas.transfer/4"] = Field(alias="schema")
    plugin_version: Literal["0.1.40"]
    live_session: LiveSession

    @model_validator(mode="after")
    def same_capture(self):
        session = self.live_session
        if (session.plugin_version != self.plugin_version
                or session.snapshot_sha256 != self.manifest.files[0].sha256
                or not session.inventory_path or not session.inventory_sha256):
            raise ValueError("Расчётный сеанс не соответствует передаваемому захвату")
        return self


_TICKET = TypeAdapter(NativeTicket | LiveTicket | SessionTicket | LiveQueryTicket)


def ticket_files(ticket: NativeTicket | LiveTicket):
    return (*ticket.manifest.files, *(
        ticket.native_session.files if isinstance(ticket, SessionTicket) else ()
    ))


def direct_path(path: Path, root: Path) -> Path:
    path = path.absolute()
    if not path.is_relative_to(root) or any(
        p.is_symlink() for p in (path, *path.parents)
    ):
        raise ValueError("Снимок должен находиться в каталоге передачи AutoCAD")
    return path


def read_regular(path: Path):
    # Pin the open inode and reject links/devices before reading. Parent paths
    # are confined to the user's private staging directory, never browser roots.
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError("Снимок содержит не обычный файл")
    return os.fdopen(descriptor, "rb")


def load_ticket(path: Path, root: Path) -> tuple[Path, NativeTicket | LiveTicket, str]:
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


def copy_verified(path: Path, target: Path | None, size: int, digest: str, check):
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
