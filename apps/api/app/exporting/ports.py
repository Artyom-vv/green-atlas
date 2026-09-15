from typing import Protocol

from app.exporting.contracts import ExportArtifact
from app.projects.contracts import Project
from app.projects.ports import ProjectReader


class DxfWriterPort(Protocol):
    def create(
        self, project: Project, source_content: bytes
    ) -> tuple[ExportArtifact, bytes]: ...


class ExportProjectRepository(ProjectReader, Protocol):
    """Atomic publication and artifact reads; no general project mutation."""

    def get_source(self, project_id: str) -> bytes | None: ...
    def get_export(self, project_id: str, artifact_id: str) -> bytes | None: ...
    def get_release(self, project_id: str, release_id: str) -> str | None: ...
    def publish_export(
        self, project: Project, artifact_id: str, content: bytes
    ) -> Project: ...
    def publish_release(
        self,
        project: Project,
        release_id: str,
        payload: str,
        artifacts: dict[str, bytes],
    ) -> Project: ...
