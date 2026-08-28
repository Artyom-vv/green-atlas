from typing import Protocol

from app.contracts import GeometrySnapshot, Project
from app.operations.progress import ProgressReporter


class GeometryEnginePort(Protocol):
    def calculate(self, project: Project, progress: ProgressReporter | None = None) -> GeometrySnapshot: ...

    def validate_position(self, project: Project, x: float, y: float, radius: float, plant_kind: str = "tree") -> None: ...

    def placement_advisory(self, project: Project, x: float, y: float, radius: float) -> str | None: ...


class GeometryQueryPort(Protocol):
    def query(self, project: Project, extent: tuple[float, float, float, float], resolution: float) -> GeometrySnapshot: ...

    def query_cached(self, project: Project, extent: tuple[float, float, float, float], resolution: float) -> GeometrySnapshot | None: ...

    def discard(self, project_id: str) -> None: ...
