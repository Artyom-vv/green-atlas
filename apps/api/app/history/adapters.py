from dataclasses import dataclass
from threading import RLock

from app.contracts import Plan, PlanHistoryState, PlantingZoneAssignment, Project, ProjectStatus


@dataclass
class _Snapshot:
    label: str
    planting_zones: list[PlantingZoneAssignment]
    plan: Plan | None
    status: ProjectStatus


class InMemoryProjectHistory:
    """Bounded, per-project history of the editable project state."""

    def __init__(self, limit: int = 50) -> None:
        self.limit = limit
        self._undo: dict[str, list[_Snapshot]] = {}
        self._redo: dict[str, list[_Snapshot]] = {}
        self._lock = RLock()

    def _snapshot(self, project: Project, label: str) -> _Snapshot:
        # History begins only after the manual plan has been opened and is
        # cleared before every operation that can replace geometry (import,
        # recalculation or a new set of planting zones). Re-copying a large
        # DXF-derived feature collection for each tree click therefore adds
        # memory pressure without making any undo state more correct.
        return _Snapshot(
            label=label,
            planting_zones=[zone.model_copy(deep=True) for zone in project.planting_zones],
            plan=project.plan.model_copy(deep=True) if project.plan else None,
            status=project.status,
        )

    def _restore(self, project: Project, snapshot: _Snapshot) -> Project:
        project.planting_zones = [zone.model_copy(deep=True) for zone in snapshot.planting_zones]
        project.plan = snapshot.plan.model_copy(deep=True) if snapshot.plan else None
        project.status = snapshot.status
        return project

    def clear(self, project_id: str) -> None:
        with self._lock:
            self._undo.pop(project_id, None)
            self._redo.pop(project_id, None)

    def record(self, project: Project, label: str) -> PlanHistoryState:
        with self._lock:
            undo = self._undo.setdefault(project.id, [])
            undo.append(self._snapshot(project, label))
            if len(undo) > self.limit:
                del undo[:-self.limit]
            self._redo.pop(project.id, None)
            return self.state(project.id)

    def state(self, project_id: str) -> PlanHistoryState:
        with self._lock:
            undo = self._undo.get(project_id, [])
            redo = self._redo.get(project_id, [])
            return PlanHistoryState(
                can_undo=bool(undo),
                can_redo=bool(redo),
                undo_label=undo[-1].label if undo else None,
                redo_label=redo[-1].label if redo else None,
            )

    def undo(self, project: Project) -> Project:
        with self._lock:
            undo = self._undo.get(project.id, [])
            if not undo:
                raise ValueError("Нет изменений для отмены")
            snapshot = undo.pop()
            redo = self._redo.setdefault(project.id, [])
            redo.append(self._snapshot(project, snapshot.label))
            return self._restore(project, snapshot)

    def redo(self, project: Project) -> Project:
        with self._lock:
            redo = self._redo.get(project.id, [])
            if not redo:
                raise ValueError("Нет изменений для повтора")
            snapshot = redo.pop()
            undo = self._undo.setdefault(project.id, [])
            undo.append(self._snapshot(project, snapshot.label))
            return self._restore(project, snapshot)
