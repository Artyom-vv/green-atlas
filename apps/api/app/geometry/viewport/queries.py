"""Пространственные сценарии карты, выбор ближайших объектов и проверка расстояний
посадок через общие порты.
"""

from collections import OrderedDict
from math import isfinite
from threading import RLock

from app.geometry.contracts import GeometrySnapshot
from app.geometry.ports import GeometryQueryPort
from app.geometry.viewport.cancellation import check_viewport_cancelled
from app.operations.progress import cancellable_lock
from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR, MAX_SPACING_INDEXES
from app.planning.domain import PlantSpacingIndex
from app.projects.contracts import Project
from app.projects.ports import ProjectGeometryReader, ProjectGeometryStreamReader
from app.validation.ports import PlanValidatorPort


class SpatialQueries:
    def __init__(
        self,
        repository: ProjectGeometryReader,
        geometry_query: GeometryQueryPort,
        validator: PlanValidatorPort,
    ) -> None:
        self.repository = repository
        self.geometry_query = geometry_query
        self.validator = validator
        self._spacing_indexes: OrderedDict[str, tuple[int, PlantSpacingIndex]] = (
            OrderedDict()
        )
        self._spacing_index_lock = RLock()
        self._geometry_load_lock = RLock()

    def invalidate(self, project_id: str) -> None:
        """Освободить производное пространственное состояние после устойчивой правки
        геометрии.

        Индексы связаны с проектом, а исходник, роли слоёв, расчёт и зоны меняют их
        основание. Инвалидируем после commit; устаревший запрос сохраняет действующую
        карту, проверки и историю.
        """
        self.geometry_query.discard(project_id)
        self.validator.discard(project_id)
        self.invalidate_spacing(project_id)

    def invalidate_spacing(self, project_id: str) -> None:
        with self._spacing_index_lock:
            self._spacing_indexes.pop(project_id, None)

    def spacing_index(self, project: Project) -> PlantSpacingIndex | None:
        if project.plan is None:
            return None
        with self._spacing_index_lock:
            cached = self._spacing_indexes.get(project.id)
            if cached is not None and cached[0] == project.plan.version:
                self._spacing_indexes.move_to_end(project.id)
                return cached[1]
            index = PlantSpacingIndex(
                project.plan.objects,
                planning_horizon_year=DEFAULT_PLANNING_HORIZON_YEAR,
            )
            self._spacing_indexes[project.id] = (project.plan.version, index)
            self._spacing_indexes.move_to_end(project.id)
            while len(self._spacing_indexes) > MAX_SPACING_INDEXES:
                self._spacing_indexes.popitem(last=False)
            return index

    def query_geometry(
        self,
        project_id: str,
        extent: tuple[float, float, float, float],
        resolution: float,
    ) -> GeometrySnapshot:
        if not all(isfinite(value) for value in (*extent, resolution)):
            raise ValueError("Координаты и масштаб карты должны быть конечными числами")
        if resolution <= 0 or extent[0] >= extent[2] or extent[1] >= extent[3]:
            raise ValueError("Некорректная область карты")
        check_viewport_cancelled()
        # Viewport меняется при каждом заметном сдвиге и масштабе. После построения
        # индекса компактная проекция уже определяет точный снимок; не читаем
        # крупный DXF GeoJSON из SQLite заново для каждого события карты.
        projection = self.repository.get(project_id, lightweight=True)
        cached = self.geometry_query.query_cached(projection, extent, resolution)
        if cached is not None:
            return cached
        # Несколько событий карты могут одновременно промахнуться в холодный кеш.
        # Полный источник загружаем единственным владельцем, затем повторяем
        # проверку версии: остальные запросы используют уже построенный индекс.
        with cancellable_lock(self._geometry_load_lock, check=check_viewport_cancelled):
            projection = self.repository.get(project_id, lightweight=True)
            cached = self.geometry_query.query_cached(projection, extent, resolution)
            if cached is not None:
                return cached
            if isinstance(self.repository, ProjectGeometryStreamReader):
                return self.geometry_query.query_streamed(
                    self.repository, project_id, extent, resolution
                )
            return self.geometry_query.query(
                self.repository.get_geometry_view(project_id), extent, resolution
            )
