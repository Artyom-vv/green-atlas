from typing import Literal, Protocol

from app.geometry.contracts import GeometrySnapshot
from app.geometry.domain import PositionAdvisory, PositionViolation
from app.operations.progress import ProgressReporter
from app.projects.contracts import Project
from app.regulations.trace_contracts import PlantingRuleTrace


class GeometryEnginePort(Protocol):
    def calculate(self, project: Project, progress: ProgressReporter | None = None) -> GeometrySnapshot: ...

    def validate_position(self, project: Project, x: float, y: float, radius: float, plant_kind: str = "tree") -> None: ...

    def position_violation(self, project: Project, x: float, y: float, radius: float, plant_kind: str = "tree") -> PositionViolation | None: ...

    def automatic_safe_geometry(
        self,
        project: Project,
        geometry: dict,
        radius: float,
        plant_kind: str = "tree",
        growth_canopy_radius: float | None = None,
        growth_root_radius: float | None = None,
    ) -> dict: ...

    def placement_advisory(self, project: Project, x: float, y: float, radius: float) -> str | None: ...

    def placement_advisory_detail(self, project: Project, x: float, y: float, radius: float) -> PositionAdvisory | None: ...

    def position_rule_trace(self, project: Project, x: float, y: float, plant_kind: Literal["tree", "shrub"] = "tree", mature_crown_diameter_m: float | None = None) -> PlantingRuleTrace: ...

    def future_growth_advisory(self, project: Project, x: float, y: float, canopy_radius: float, root_radius: float) -> str | None: ...

    def future_growth_advisory_detail(self, project: Project, x: float, y: float, canopy_radius: float, root_radius: float) -> PositionAdvisory | None: ...


class GeometryQueryPort(Protocol):
    def query(self, project: Project, extent: tuple[float, float, float, float], resolution: float) -> GeometrySnapshot: ...

    def query_cached(self, project: Project, extent: tuple[float, float, float, float], resolution: float) -> GeometrySnapshot | None: ...

    def discard(self, project_id: str) -> None: ...
