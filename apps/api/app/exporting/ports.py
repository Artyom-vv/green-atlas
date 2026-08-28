from typing import Protocol

from app.contracts import ExportArtifact, Project


class DxfWriterPort(Protocol):
    def create(self, project: Project, source_content: bytes) -> tuple[ExportArtifact, bytes]: ...
