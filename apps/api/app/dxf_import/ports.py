"""Порты прежнего DXF-чтения и атомарного сохранения импортированного проекта с
исходником и компонентами.
"""

from typing import Protocol

from app.dxf_import.contracts import DxfImportResult
from app.projects.ports import (
    ProjectMappingRepository,
    ProjectNativeSessionRepository,
    ProjectSnapshotRepository,
)


class DxfReaderPort(Protocol):
    def read(self, filename: str, content: bytes | bytearray) -> DxfImportResult: ...


class ImportProjectRepository(
    ProjectSnapshotRepository,
    ProjectMappingRepository,
    ProjectNativeSessionRepository,
    Protocol,
):
    """Атомарная запись исходника и назначений в ревизии проекта."""

    def get_source(self, project_id: str) -> bytes | None: ...
