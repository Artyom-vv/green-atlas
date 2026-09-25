from collections.abc import Callable
from datetime import datetime
from threading import RLock
from uuid import UUID

from app.data_passport import build_data_passport
from app.dxf_import.evidence_contracts import DataPassport
from app.history.ports import ProjectHistoryResetPort
from app.projects.contracts import Project, ProjectSummary
from app.projects.ports import ProjectCatalogRepository


class ProjectCatalogApplication:
    def __init__(
        self,
        *,
        repository: ProjectCatalogRepository,
        history: ProjectHistoryResetPort,
        commit_lock: RLock,
        cancel_active: Callable[[str], None],
        invalidate_spatial: Callable[[str], None],
        now: Callable[[], datetime],
        new_id: Callable[[], str],
        geometry=None,
    ) -> None:
        self.repository = repository
        self.geometry = geometry
        self.history = history
        self._operation_commit_lock = commit_lock
        self._cancel_active = cancel_active
        self._discard_spatial_indexes = invalidate_spatial
        self._now = now
        self._new_id = new_id

    def create_project(self, name: str) -> Project:
        return self.repository.create(
            Project(
                id=self._new_id(),
                name=name,
                created_at=self._now().isoformat(),
                updated_at=self._now().isoformat(),
            )
        )

    def ensure_import_project(self, project_id: str, name: str) -> Project:
        """Internal durable intake intent: recover creation without overwriting edits.

        Callers must reserve a random UUID in their own persistent intake journal.
        This is not exposed as a browser create/update endpoint.
        """
        if str(UUID(project_id)) != project_id:
            raise ValueError("Expected a canonical project UUID")
        with self._operation_commit_lock:
            try:
                return self.repository.get(project_id, lightweight=True)
            except KeyError:
                return self.repository.create(Project(
                    id=project_id, name=name, created_at=self._now().isoformat(),
                    updated_at=self._now().isoformat(),
                ))

    def get(self, project_id: str, *, lightweight: bool = False) -> Project:
        return self.repository.get(project_id, lightweight=lightweight)

    def list_projects(self) -> list[ProjectSummary]:
        return [
            ProjectSummary(
                id=project.id,
                name=project.name,
                status=project.status,
                source_name=project.source_file.name if project.source_file else None,
                source_size=project.source_file.size if project.source_file else None,
                has_geometry=project.geometry is not None,
                planting_zone_count=len(project.planting_zones),
                plan_object_count=len(project.plan.objects) if project.plan else 0,
                plan_version=project.plan.version if project.plan else None,
                import_status=project.import_status.model_copy(deep=True),
                state_version=project.state_version,
                created_at=project.created_at,
                updated_at=project.updated_at,
            )
            for project in self.repository.list(lightweight=True)
        ]

    def get_data_passport(self, project_id: str) -> DataPassport:
        """Return the source-data evidence for the current project revision."""

        return build_data_passport(self.get(project_id), geometry=self.geometry)

    def delete_project(self, project_id: str) -> None:
        # A geometry worker can be between two progress callbacks while a
        # user deletes its project. Hold the same commit boundary used by the
        # worker: after the durable delete, mark active work cancelled before
        # it may publish another progress update or a late map snapshot.
        with self._operation_commit_lock:
            self.get(project_id)
            self.repository.delete(project_id)
            self._cancel_active(project_id)
        # The repository call is the durability boundary. Do not evict a map
        # index on a stale delete: the surviving project must keep both its
        # viewport cache and undo history intact.
        self._discard_spatial_indexes(project_id)
        # A concurrent version conflict means the project still exists. Only
        # discard its volatile undo history after the durable deletion has
        # actually committed.
        self.history.clear(project_id)
