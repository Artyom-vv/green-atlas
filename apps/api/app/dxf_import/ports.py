from typing import Protocol

from app.contracts import DxfImportResult


class DxfReaderPort(Protocol):
    def read(self, filename: str, content: bytes | bytearray) -> DxfImportResult: ...
