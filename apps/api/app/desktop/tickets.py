"""Read a native ticket only inside the private AutoCAD staging directory."""

import hashlib
import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.cad_delivery.contracts import PublicationRequest, TransferManifest


class NativeTicket(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    format: Literal["green-atlas.transfer/1"] = Field(alias="schema")
    plugin_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=32)
    manifest: TransferManifest
    producer: PublicationRequest


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


def load_ticket(path: Path, root: Path) -> tuple[Path, NativeTicket, str]:
    path = direct_path(path, root)
    if path.name != "transfer.gatransfer":
        raise ValueError("Ожидается снимок, подготовленный плагином AutoCAD")
    with read_regular(path) as source:
        payload = source.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("Файл передачи слишком велик")
    return (
        path,
        NativeTicket.model_validate_json(payload),
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
