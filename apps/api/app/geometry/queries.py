from collections import OrderedDict
from math import isfinite
from threading import RLock

from app.geometry.contracts import GeometrySnapshot
from app.geometry.ports import GeometryQueryPort
from app.planning.config import MAX_SPACING_INDEXES
from app.planning.domain import PlantSpacingIndex
from app.projects.contracts import Project
from app.projects.ports import ProjectReader
from app.validation.ports import PlanValidatorPort


class SpatialQueries:
    def __init__(
        self,
        repository: ProjectReader,
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

    def invalidate(self, project_id: str) -> None:
        """Release all derived spatial state after a durable geometry change.

        Both indexes are keyed by project id. A new source DXF, layer meaning,
        calculated map or selected working area invalidates their physical
        basis. Do this only after the repository commits; a stale concurrent
        request must leave the surviving map, checker and undo state intact.
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
            index = PlantSpacingIndex(project.plan.objects)
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
        # A viewport event arrives on every meaningful pan/zoom. Once the
        # spatial index exists, its compact project projection has everything
        # needed to identify the exact snapshot; avoid deserialising a large
        # DXF GeoJSON from SQLite for every such request.
        projection = self.repository.get(project_id, lightweight=True)
        cached = self.geometry_query.query_cached(projection, extent, resolution)
        if cached is not None:
            return cached
        return self.geometry_query.query(
            self.repository.get(project_id), extent, resolution
        )
