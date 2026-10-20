"""Приватные контрольные точки поиска привязаны к захвату и не заменяют проверку
native-идентичности.
"""

import json
import os
from collections.abc import Callable, Iterable
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic

from pydantic import BaseModel, ConfigDict

from app.operations.progress import (
    PROGRESS_REPORT_INTERVAL_S,
    check_operation_cancelled,
)
from app.projects.resource_lifecycle import ProjectResourceLifecycle
from app.projects.resource_scope import register_project_resource

CHECKPOINT_IO_BYTES = 1024 * 1024


class _CheckpointEnvelope(BaseModel):
    """Читать прежний JSON-конверт сразу в UTF-8 без большой Unicode-копии."""

    model_config = ConfigDict(extra="ignore", val_json_bytes="utf8")
    payload: bytes
    sha256: str


class _DigestWriter:
    """Сверять записанные UTF-8 байты без второй большой строки в памяти."""

    def __init__(self, write: Callable[[str], int]) -> None:
        self._write = write
        self.digest = sha256()
        self._next_check = 0.0

    def write(self, value: str) -> int:
        for start in range(0, len(value), CHECKPOINT_IO_BYTES):
            now = monotonic()
            if now >= self._next_check:
                check_operation_cancelled()
                self._next_check = now + PROGRESS_REPORT_INTERVAL_S
            chunk = value[start : start + CHECKPOINT_IO_BYTES]
            self._write(chunk)
            self.digest.update(chunk.encode("utf-8"))
        return len(value)


class DomainCheckpointStore:
    def __init__(
        self,
        directory: Path,
        *,
        resource_lifecycle: ProjectResourceLifecycle | None = None,
        project_id: str | None = None,
        resource_kind: str | None = None,
    ):
        self.directory = directory
        self.resource_lifecycle = resource_lifecycle
        self.project_id = project_id
        self.resource_kind = resource_kind

    def load(self, key: str) -> dict | None:
        payload = self.load_payload(key)
        if payload is None:
            return None
        try:
            return json.loads(payload)
        except (ValueError, TypeError) as error:
            raise ValueError(
                "Не удалось прочитать сохранённый ход проверки области"
            ) from error

    def load_payload(self, key: str) -> bytes | None:
        """Возвращать проверенный JSON для прямого разбора типизированным кодеком."""
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        try:
            check_operation_cancelled()
            envelope = _CheckpointEnvelope.model_validate_json(path.read_bytes())
            payload = envelope.payload
            digest = sha256()
            for start in range(0, len(payload), CHECKPOINT_IO_BYTES):
                check_operation_cancelled()
                digest.update(payload[start : start + CHECKPOINT_IO_BYTES])
            if digest.hexdigest() != envelope.sha256:
                raise ValueError("checksum")
            check_operation_cancelled()
            return payload
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(
                "Не удалось прочитать сохранённый ход проверки области"
            ) from error

    def matches_saved(self, key: str, expected_sha256: str) -> bool:
        """Проверять точные записанные байты без разбора расчётного снимка."""
        path = self.directory / f"{key}.json"
        if path.is_symlink() or not path.is_file():
            return False
        with path.open("rb") as stream:
            digest = sha256()
            while chunk := stream.read(CHECKPOINT_IO_BYTES):
                check_operation_cancelled()
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256

    def save(self, key: str, value: dict) -> str:
        return self.save_chunks(
            key, [json.dumps(value, separators=(",", ":"), allow_nan=False)]
        )

    def save_chunks(self, key: str, chunks: Iterable[str]) -> str:
        if (
            self.resource_lifecycle is not None
            and self.project_id is not None
            and self.resource_kind is not None
        ):
            with self.resource_lifecycle.use(self.project_id):
                self.resource_lifecycle.register(
                    self.project_id, self.resource_kind, key
                )
                return self._save_chunks(key, chunks)
        if self.resource_kind is not None:
            register_project_resource(self.resource_kind, key)
        return self._save_chunks(key, chunks)

    def _save_chunks(self, key: str, chunks: Iterable[str]) -> str:
        """Атомарно записывать готовый JSON по частям в прежнем SHA-конверте."""
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload_digest = sha256()
        temporary = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.directory,
                prefix=f".{key}.",
                suffix=".pending",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                writer = _DigestWriter(stream.write)
                writer.write('{"payload": "')
                for chunk in chunks:
                    payload_digest.update(chunk.encode("utf-8"))
                    writer.write(json.dumps(chunk)[1:-1])
                writer.write('", "sha256": "' + payload_digest.hexdigest() + '"}')
                stream.flush()
                os.fsync(stream.fileno())
            check_operation_cancelled()
            temporary.replace(self.directory / f"{key}.json")
            if os.name == "posix":
                descriptor = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return writer.digest.hexdigest()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
