"""Каталог проектов и чтение рабочей области с паспортом данных и готовностью
сохранённого расчётного основания.
"""

from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import Literal
from uuid import UUID

from app.data_passport import build_data_passport
from app.dxf_import.evidence_contracts import DataPassport
from app.dxf_import.review.passport_policy import DATA_CLASSES
from app.dxf_import.source.editor_source import is_native_source_editor
from app.geometry.ports import GeometryCoveragePort
from app.history.ports import ProjectHistoryResetPort
from app.projects.contracts import (
    PreparedAvailability,
    Project,
    ProjectRead,
    ProjectSummary,
)
from app.projects.ports import ProjectCatalogRepository
from app.projects.resume_policy import resume_kind


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
        geometry: GeometryCoveragePort | None = None,
        prepared_status: Callable[[Project], PreparedAvailability] | None = None,
        prepared_read_status: Callable[[Project], PreparedAvailability] | None = None,
    ) -> None:
        self.repository = repository
        self.geometry = geometry
        self._prepared_status = prepared_status
        self._prepared_read_status = prepared_read_status
        self.history = history
        self._operation_commit_lock = commit_lock
        self._cancel_active = cancel_active
        self._discard_spatial_indexes = invalidate_spatial
        self._now = now
        self._new_id = new_id
        self._delete_listeners: list[Callable[[str], None]] = []

    def add_delete_listener(self, listener: Callable[[str], None]) -> None:
        """Отмена принадлежащей runtime фоновой работы после фиксации удаления."""
        self._delete_listeners.append(listener)

    def remove_delete_listener(self, listener: Callable[[str], None]) -> None:
        self._delete_listeners.remove(listener)

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
        """Восстанавливает создание проекта по сохранённому намерению импорта, сохраняя
        правки.

        Вызывающий код заранее резервирует случайный UUID в собственном постоянном
        журнале импорта. Метод не выставлен как браузерный эндпоинт создания или
        обновления проекта.
        """
        if str(UUID(project_id)) != project_id:
            raise ValueError("Expected a canonical project UUID")
        with self._operation_commit_lock:
            try:
                return self.repository.get(project_id, lightweight=True)
            except KeyError:
                return self.repository.create(
                    Project(
                        id=project_id,
                        name=name,
                        created_at=self._now().isoformat(),
                        updated_at=self._now().isoformat(),
                    )
                )

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
                resume_kind=resume_kind(project),
                prepared_state=self.prepared_status(project),
            )
            for project in self.repository.list(lightweight=True)
        ]

    def prepared_status(self, project: Project) -> PreparedAvailability:
        if self._prepared_status is not None:
            return self._prepared_status(project)
        return (
            "missing"
            if project.source_file and project.source_file.native_session
            else "not_required"
        )

    def read_workspace(
        self, project: Project, *, lightweight: bool = False
    ) -> ProjectRead:
        state = (
            self._prepared_read_status(project)
            if self._prepared_read_status and not lightweight
            else self.prepared_status(project)
        )
        return ProjectRead(
            **project.__dict__,
            resume_kind=resume_kind(project),
            prepared_state=state,
        )

    def get_data_passport(self, project_id: str) -> DataPassport:
        """Возвращает свидетельства об исходных данных для текущей ревизии проекта."""

        project = self.get(project_id, lightweight=True)
        if is_native_source_editor(project):
            # Паспорт редактора описывает слои и адресные пропуски. Миллион
            # объектов карты не нужен для чтения этих уже сохранённых сведений.
            return build_data_passport(
                project, geometry=self.geometry, metadata_only=True
            )
        if (
            project.source_file
            and project.source_file.native_session
            and (project.geometry is not None or project.source_geometry is not None)
        ):
            # Паспорт сохраняет точные счётчики отображения и расчётное покрытие,
            # но не загружает оба CAD-графа параллельно с открытием карты.
            field: Literal["geometry", "source_geometry"] = (
                "geometry" if project.geometry is not None else "source_geometry"
            )
            counts = self.repository.read_display_counts(
                project_id,
                expected_version=project.state_version,
                geometry_field=field,
                kinds=set(DATA_CLASSES),
            )
            return build_data_passport(
                project, geometry=self.geometry, display_counts=counts
            )
        return build_data_passport(self.get(project_id), geometry=self.geometry)

    def delete_project(self, project_id: str) -> None:
        # Удаление использует ту же границу фиксации, что геометрический работник.
        # После устойчивого удаления отменяем активную работу до следующей
        # публикации прогресса или позднего снимка.
        with self._operation_commit_lock:
            self.get(project_id, lightweight=True)
            self.repository.delete(project_id)
            self._cancel_active(project_id)
            for listener in tuple(self._delete_listeners):
                listener(project_id)
        # Запись репозитория определяет устойчивость удаления. При устаревшей ревизии
        # не удаляем индекс карты: сохранившийся проект сохраняет viewport-кеш
        # и историю отмены.
        self._discard_spatial_indexes(project_id)
        # Конфликт ревизии означает, что проект ещё существует. Временная история
        # отмены удаляется лишь после фактической фиксации удаления.
        self.history.clear(project_id)
        lifecycle = getattr(self.repository, "resource_lifecycle", None)
        if lifecycle is not None:
            lifecycle.drain(project_id)
