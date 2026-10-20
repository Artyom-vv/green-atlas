"""Сценарии истории плана: сохранение правок, отмена и повтор без копирования геометрии
исходника.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from app.history.contracts import PlanHistoryState
from app.history.ports import ProjectHistoryPort
from app.projects.contracts import Project
from app.projects.ports import (
    ProjectPlacementReader,
    ProjectSnapshotRepository,
)


def history_basis(project: Project) -> Project:
    """Снимать только изменяемое ручное состояние перед устойчивой правкой.

    Полный model_copy(deep=True) копировал бы CAD при каждом выборе дерева. История
    требует только план, зоны и статус; геометрия сохраняется по ссылке.
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
        *,
        supports_projection: Callable[[Project], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.history = history
        self.edit_lock = edit_lock
        self.invalidate_spacing = invalidate_spacing
        self.supports_projection = supports_projection

    def commit(
        self,
        project: Project,
        before: Project,
        label: str,
        change_set_id: str | None = None,
        receipt: dict | None = None,
    ) -> Project:
        return self.history.commit(
            project, before, label, change_set_id, receipt=receipt, plan_only=True
        )

    def get_plan_history(self, project_id: str) -> PlanHistoryState:
        # Проверяем наличие и ревизию без загрузки неизменяемого CAD-графа.
        self.repository.get(project_id, lightweight=True)
        return self.history.state(project_id)

    def undo_plan_change(self, project_id: str) -> Project:
        with self.edit_lock:
            return self._undo_plan_change(project_id)

    def _undo_plan_change(self, project_id: str) -> Project:
        project = self._read_history_project(project_id)
        # Запись зоны может пережить сбой до обслуживания истории. Перед восстановлением
        # плана синхронизируем историю по действующему проекту; ошибка обслуживания
        # запрещает отмену, чтобы не восстановить прежние зоны.
        self.history.rebase_planting_zones(project.id, project.planting_zones)
        saved = self.history.undo(project)
        self.invalidate_spacing(project.id)
        return saved

    def redo_plan_change(self, project_id: str) -> Project:
        with self.edit_lock:
            return self._redo_plan_change(project_id)

    def _redo_plan_change(self, project_id: str) -> Project:
        project = self._read_history_project(project_id)
        self.history.rebase_planting_zones(project.id, project.planting_zones)
        saved = self.history.redo(project)
        self.invalidate_spacing(project.id)
        return saved

    def _read_history_project(self, project_id: str) -> Project:
        # Только подготовленный проверяющий модуль явно допускает отсутствие
        # координат отображения. Совместимый провайдер может читать их для basis.
        if self.supports_projection is not None and isinstance(
            self.repository, ProjectPlacementReader
        ):
            project = self.repository.get(project_id, lightweight=True)
            if self.supports_projection(project):
                projected = self.repository.get_placement_projection(
                    project_id, expected_version=project.state_version
                )
                if projected is not None:
                    return projected
        # Inline и смешанные старые записи сохраняют полную проверку геометрии.
        return self.repository.get(project_id)
