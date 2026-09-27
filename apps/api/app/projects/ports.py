from __future__ import annotations

import builtins
from typing import Protocol

from app.projects.contracts import Project
from app.projects.source_contracts import SourceContentInfo


class ProjectReader(Protocol):
    """Read the authoritative project snapshot without requiring write access."""

    def get(self, project_id: str, *, lightweight: bool = False) -> Project: ...


class ProjectSnapshotRepository(ProjectReader, Protocol):
    def save(
        self,
        project: Project,
        *,
        source: bytes | bytearray | None = None,
        source_components: dict[str, bytes] | None = None,
    ) -> Project: ...


class ProjectCatalogRepository(ProjectReader, Protocol):
    def create(self, project: Project) -> Project: ...
    def list(self, *, lightweight: bool = False) -> list[Project]: ...
    def delete(
        self, project_id: str, *, expected_version: int | None = None
    ) -> None: ...


class ProjectRepository(ProjectSnapshotRepository, ProjectCatalogRepository, Protocol):
    def save_with_receipt(
        self, project: Project, kind: str, mutation_id: str, receipt: dict
    ) -> Project: ...
    def mutation_receipt(
        self, project_id: str, kind: str, mutation_id: str
    ) -> dict | None: ...
    def save_many(self, projects: builtins.list[Project]) -> builtins.list[Project]: ...
    def delete(
        self, project_id: str, *, expected_version: int | None = None
    ) -> None: ...
    def save_source(self, project_id: str, content: bytes | bytearray) -> None: ...
    def get_source(self, project_id: str) -> bytes | None: ...
    def get_source_components(self, project_id: str) -> dict[str, bytes]: ...
    def get_source_info(
        self, project_id: str, *, prefix_bytes: int
    ) -> SourceContentInfo | None: ...
    def save_export(
        self, project_id: str, artifact_id: str, content: bytes
    ) -> None: ...
    def publish_export(
        self, project: Project, artifact_id: str, content: bytes
    ) -> Project: ...
    def get_export(self, project_id: str, artifact_id: str) -> bytes | None: ...
    def publish_release(
        self,
        project: Project,
        release_id: str,
        payload: str,
        artifacts: dict[str, bytes],
    ) -> Project: ...
    def get_release(self, project_id: str, release_id: str) -> str | None: ...
