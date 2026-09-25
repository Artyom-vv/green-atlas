"""Queries the open AutoCAD document; no saved-file worker or CAD fallback.

The snapshot is for display only. Session identity covers the live host and
loaded XREF databases, including unsaved edits, not just the on-disk SHA.
Selected-object replies do not prove complete obstacle coverage.
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field

from app.native_query.contracts import (
    Hex32,
    NativeDto,
    NativeObjectQuery,
    NativeObjectReply,
    Sha256,
)
from app.native_query.protocol import decode_reply, encode_request
from app.native_query.face_contracts import FacePreparation

LIVE_PROTOCOL = "green-atlas.live-query/1"


class LiveSession(NativeDto):
    session_id: Hex32
    pid: int = Field(gt=0)
    plugin_version: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_sha256: Sha256
    snapshot_path: str = Field(min_length=1)
    snapshot_sha256: Sha256
    inventory_path: str | None = None
    inventory_sha256: Sha256 | None = None
    units_code: int = Field(ge=0, le=24)
    watched_databases: int = Field(ge=1)
    unavailable_xrefs: int = Field(ge=0)


class LiveReply(NativeDto):
    schema_: Literal["green-atlas.live-query/1"] = Field(alias="schema")
    request_id: Hex32
    ok: bool
    error: str
    session: LiveSession | None


class LiveQueryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        messages = {
            "live_timeout": "AutoCAD не ответил на запрос",
            "live_process_stopped": "Сеанс расчёта AutoCAD завершён — требуется переподключение",
            "live_session_expired": "Связь с чертежом устарела — обновите исходные данные",
            "live_source_changed_or_inactive": "Чертёж изменён или не активен в AutoCAD",
            "live_source_file_changed": "Исходный файл изменён после загрузки",
            "live_xref_graph_changed": "Состав подоснов изменён после загрузки",
        }
        super().__init__(messages.get(code, "Не удалось проверить геометрию в AutoCAD"))


class LiveQueryClient:
    def __init__(
        self,
        queue: Path | None = None,
        *,
        pid: int | None = None,
        timeout_seconds: float = 90,
    ):
        if queue is None and (pid is None or pid <= 0):
            raise ValueError("A live AutoCAD process must be selected explicitly")
        self.pid = pid
        self.queue = queue or Path(f"/tmp/green-atlas-live-query-{os.getuid()}-{pid}")
        if not 0 < timeout_seconds <= 600:
            raise ValueError("Live query timeout must be within (0, 600]")
        self.timeout_seconds = timeout_seconds

    def _directory(self) -> None:
        self.queue.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.queue.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise ValueError("Live queue must be a private owned directory")

    def _check_process(self) -> None:
        if self.pid is None:
            return  # Explicit queues are also used by transport fixtures.
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            raise LiveQueryError("live_process_stopped") from None

    def _publish(self, path: Path, data: bytes) -> None:
        temporary = path.with_suffix(".pending")
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def _exchange(
        self, operation: str, request_id: str, session_id: str
    ) -> LiveSession:
        self._check_process()
        self._directory()
        request = self.queue / f"request-{request_id}.txt"
        response = self.queue / f"reply-{request_id}.json"
        deadline = int(time.time() + self.timeout_seconds + 1)
        self._publish(
            request,
            f"{LIVE_PROTOCOL}\n{operation}\n{session_id}\n{deadline}\n".encode(),
        )
        end = time.monotonic() + self.timeout_seconds
        while not response.exists():
            self._check_process()
            if time.monotonic() >= end:
                (self.queue / f"cancel-{request_id}").touch(exist_ok=False)
                raise LiveQueryError("live_timeout")
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        if response.stat().st_size > 65536:
            raise ValueError("Live reply exceeds metadata size limit")
        reply = LiveReply.model_validate_json(response.read_bytes())
        if reply.request_id != request_id:
            raise ValueError("Live reply belongs to a different request")
        if not reply.ok:
            raise LiveQueryError(reply.error)
        if (
            reply.error
            or reply.session is None
            or reply.session.session_id != session_id
        ):
            raise ValueError("Live reply has no matching valid session")
        if self.pid is not None and reply.session.pid != self.pid:
            raise ValueError("Live reply belongs to another AutoCAD process")
        response.unlink()
        return reply.session

    def open(self) -> LiveSession:
        identity = uuid4().hex
        session = self._exchange("open", identity, identity)
        return self._verify_capture(session)

    def prepare_faces(self, session: LiveSession, layers: tuple[str, ...]) -> FacePreparation:
        layers = tuple(sorted(set(layers)))
        if len(layers) > 4096 or any(not s or len(s.encode()) > 4096 or any(c in s for c in "\r\n\x00") for s in layers):
            raise ValueError("Недопустимый список площадных слоёв")
        request_id = uuid4().hex
        content = ("\n".join(layers) + ("\n" if layers else "")).encode()
        self._directory()
        self._publish(self.queue / f"layers-{request_id}.txt", content)
        current = self._exchange("prepare_faces", request_id, session.session_id)
        if current != session:
            raise LiveQueryError("live_source_changed_or_inactive")
        output = self.queue / f"faces-{request_id}.json"
        if output.stat().st_size > 96 * 1024**2:
            raise ValueError("Каталог производных областей превышает лимит передачи")
        result = FacePreparation.model_validate_json(output.read_bytes())
        if result.session_id != session.session_id or result.request_sha256 != hashlib.sha256(content).hexdigest() or result.layers != layers:
            raise ValueError("Каталог областей относится к другому запросу")
        return result

    def reconnect(self, session_id: str) -> LiveSession:
        """Resume the same live capture after an interrupted application import."""
        session = self._exchange("inspect", uuid4().hex, session_id)
        return self._verify_capture(session)

    def _verify_capture(self, session: LiveSession) -> LiveSession:
        expected = self.queue / f"map-{session.session_id}.json"
        if Path(session.snapshot_path).resolve() != expected.resolve():
            raise ValueError("Live capture path does not belong to this session")
        with expected.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != session.snapshot_sha256:
            raise ValueError("Live display snapshot changed during transfer")
        return session

    def inspect(self, session: LiveSession) -> None:
        current = self._exchange("inspect", uuid4().hex, session.session_id)
        if current != session:
            raise ValueError("Live session basis changed")

    def measure(
        self, session: LiveSession, query: NativeObjectQuery
    ) -> NativeObjectReply:
        self._check_process()
        if (
            query.source_sha256 != session.source_sha256
            or query.units_code != session.units_code
        ):
            raise ValueError("Query belongs to a different live source")
        self._directory()
        request = self.queue / f"objects-{query.request_id}.txt"
        output = self.queue / f"measurements-{query.request_id}.json"
        if request.exists() or output.exists():
            raise ValueError("Live measurement request ID already used")
        request_bytes = encode_request(query, output.resolve())
        self._publish(request, request_bytes)
        current = self._exchange("query", query.request_id, session.session_id)
        if current != session:
            raise ValueError("Live session changed during measurements")
        if output.stat().st_size > 96 * 1024 * 1024:
            raise ValueError("Live measurements exceed protocol size limit")
        result = decode_reply(output.read_bytes(), query, request_bytes=request_bytes)
        if result.plugin_version != session.plugin_version:
            raise ValueError("Live plugin changed during measurements")
        request.unlink()
        output.unlink()
        return result
