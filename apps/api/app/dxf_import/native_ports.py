from typing import Protocol

from app.projects.ports import ProjectReader
from app.projects.source_contracts import SourceContentInfo


class NativeDxfRepository(ProjectReader, Protocol):
    def get_source(self, project_id: str) -> bytes | None: ...

    def get_source_info(
        self, project_id: str, *, prefix_bytes: int
    ) -> SourceContentInfo | None: ...
