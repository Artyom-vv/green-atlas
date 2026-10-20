"""Порт транзакционной истории: изменения плана, квитанции операций, перебазирование
зон, отмена и повтор.
"""

from typing import Protocol

from app.history.contracts import PlanHistoryState
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project


class ProjectHistoryResetPort(Protocol):
    def clear(self, project_id: str) -> None: ...


class ProjectHistoryPort(ProjectHistoryResetPort, Protocol):
    def commit(
        self,
        project: Project,
        before: Project,
        label: str,
        change_set_id: str | None = None,
        receipt: dict | None = None,
        *,
        plan_only: bool = False,
    ) -> Project: ...
    def receipt(self, project_id: str, change_set_id: str) -> dict | None: ...
    def state(self, project_id: str) -> PlanHistoryState: ...
    def rebase_planting_zones(
        self, project_id: str, zones: list[PlantingZoneAssignment]
    ) -> None: ...
    def undo(self, project: Project) -> Project: ...
    def redo(self, project: Project) -> Project: ...
