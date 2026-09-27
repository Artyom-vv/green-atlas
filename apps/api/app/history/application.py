from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from app.history.contracts import PlanHistoryState
from app.history.ports import ProjectHistoryPort
from app.projects.contracts import Project
from app.projects.ports import (
    ProjectSnapshotRepository,
)


def history_basis(project: Project) -> Project:
    """Capture only the mutable manual state before a durable edit.

    A full ``model_copy(deep=True)`` would copy the DXF geometry on every
    tree click. The history adapter already needs only plan, areas and
    status, so keep geometry by reference and clone just those values.
    """
    return project.model_copy(
        update={
            "planting_zones": [
                zone.model_copy(deep=True) for zone in project.planting_zones
            ],
            "plan": project.plan.model_copy(deep=True) if project.plan else None,
            "status": project.status,
        }
    )


class PlanHistoryApplication:
    def __init__(
        self,
        repository: ProjectSnapshotRepository,
        history: ProjectHistoryPort,
        edit_lock: RLock,
        invalidate_spacing: Callable[[str], None],
    ) -> None:
        self.repository = repository
        self.history = history
        self.edit_lock = edit_lock
        self.invalidate_spacing = invalidate_spacing

    def commit(
        self,
        project: Project,
        before: Project,
        label: str,
        change_set_id: str | None = None,
        receipt: dict | None = None,
    ) -> Project:
        commit = getattr(self.history, "commit", None)
        if callable(commit):
            return commit(
                project,
                before,
                label,
                change_set_id,
                **({"receipt": receipt} if receipt is not None else {}),
            )
        saved = self.repository.save(project)
        self.history.record(before, label)
        return saved

    def get_plan_history(self, project_id: str) -> PlanHistoryState:
        # Validate existence/version without loading the immutable CAD graph.
        self.repository.get(project_id, lightweight=True)
        return self.history.state(project_id)

    def undo_plan_change(self, project_id: str) -> Project:
        with self.edit_lock:
            return self._undo_plan_change(project_id)

    def _undo_plan_change(self, project_id: str) -> Project:
        project = self.repository.get(project_id)
        # A zone write can survive a crash before its history maintenance.
        # Repair from the authoritative project before restoring any plan;
        # failed maintenance must prevent undo rather than restore old zones.
        self.history.rebase_planting_zones(project.id, project.planting_zones)
        if getattr(self.history, "durable", False):
            saved = self.history.undo(project)
            self.invalidate_spacing(project.id)
            return saved
        restored = self.history.undo(project)
        try:
            saved = self.repository.save(restored)
            self.invalidate_spacing(project.id)
            return saved
        except Exception:
            # The durable project survived a conflict. Reapply the local
            # history transition so undo/redo buttons still describe that
            # surviving project instead of a phantom state.
            self.history.redo(restored)
            raise

    def redo_plan_change(self, project_id: str) -> Project:
        with self.edit_lock:
            return self._redo_plan_change(project_id)

    def _redo_plan_change(self, project_id: str) -> Project:
        project = self.repository.get(project_id)
        self.history.rebase_planting_zones(project.id, project.planting_zones)
        if getattr(self.history, "durable", False):
            saved = self.history.redo(project)
            self.invalidate_spacing(project.id)
            return saved
        restored = self.history.redo(project)
        try:
            saved = self.repository.save(restored)
            self.invalidate_spacing(project.id)
            return saved
        except Exception:
            self.history.undo(restored)
            raise
