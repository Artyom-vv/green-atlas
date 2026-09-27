from typing import Protocol

from app.dxf_import.contracts import DxfImportResult
from app.projects.contracts import Project
from app.projects.ports import ProjectReader


class DxfReaderPort(Protocol):
    def read(self, filename: str, content: bytes | bytearray) -> DxfImportResult: ...


class ImportProjectRepository(ProjectReader, Protocol):
    """Commit a source and its project metadata as one revision."""

    def save(
        self,
        project: Project,
        *,
        source: bytes | bytearray | None = None,
        source_components: dict[str, bytes] | None = None,
    ) -> Project: ...

    def get_source(self, project_id: str) -> bytes | None: ...
