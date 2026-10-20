"""Порты расчёта, проверки, покрытия и объяснений геометрии; отклонённая позиция
сохраняет результат общего проверяющего модуля.
"""

from contextlib import AbstractContextManager
from typing import Literal, Protocol

from app.geometry.checking.domain import PositionAdvisory, PositionViolation
from app.geometry.contracts import GeometrySnapshot
from app.geometry.coverage_contracts import GeometryCoverage
from app.operations.progress import ProgressReporter
from app.projects.contracts import Project
from app.projects.ports import ProjectGeometryStreamReader, ProjectGeometryView
from app.regulations.trace_contracts import PlantingRuleTrace


class PositionRejected(ValueError):
    """A geometric refusal retains the checker result, rather than exception text."""

    def __init__(self, violation: PositionViolation) -> None:
        super().__init__()
        self.violation = violation


class GeometryCoveragePort(Protocol):
    """Optional current calculation evidence, never inferred from map counts."""

    def source_coverage(self, project: Project) -> GeometryCoverage | None: ...


class GeometryRuleTracePort(Protocol):
    """Read planting evidence without promising preparation or placement."""

    def position_rule_trace(
        self,
        project: Project,
        x: float,
        y: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
        mature_crown_diameter_m: float | None = None,
    ) -> PlantingRuleTrace: ...


class GeometryEnginePort(GeometryRuleTracePort, Protocol):
    def calculate(
        self, project: Project, progress: ProgressReporter | None = None
    ) -> GeometrySnapshot: ...

    def validate_position(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: str = "tree",
    ) -> None: ...

    def position_violation(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: str = "tree",
    ) -> PositionViolation | None: ...

    def automatic_safe_geometry(
        self,
        project: Project,
        geometry: dict,
        radius: float,
        plant_kind: str = "tree",
        growth_canopy_radius: float | None = None,
        growth_root_radius: float | None = None,
    ) -> dict: ...

    def placement_advisory(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> str | None: ...

    def placement_advisory_detail(
        self,
        project: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> PositionAdvisory | None: ...

    def future_growth_advisory(
        self,
        project: Project,
        x: float,
        y: float,
        canopy_radius: float,
        root_radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> str | None: ...

    def future_growth_advisory_detail(
        self,
        project: Project,
        x: float,
        y: float,
        canopy_radius: float,
        root_radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> PositionAdvisory | None: ...


class GeometryPreparationPort(Protocol):
    """Optional batch preparation; it does not replace per-position rules."""

    def prepare_positions(
        self, project: Project, points: list[tuple[float, float]]
    ) -> None: ...


class GeometryCalculationScopePort(Protocol):
    """Необязательное время жизни проверенных ресурсов одной порции поиска."""

    def calculation_scope(self, project: Project) -> AbstractContextManager[None]: ...


class GeometrySearchTraceSummaryPort(Protocol):
    """Верхняя граница сетевых кодов, а не найденные пространственные замечания."""

    def search_trace_assumption_codes(
        self, project: Project, plant_kinds: frozenset[str]
    ) -> frozenset[str] | None: ...


class PlacementProjectionGeometryPort(Protocol):
    """Явный допуск компактного проекта к локальному проверяющему модулю."""

    def supports_placement_projection(self, project: Project) -> bool: ...


class GeometryQueryPort(Protocol):
    def query_streamed(
        self,
        reader: ProjectGeometryStreamReader,
        project_id: str,
        extent: tuple[float, float, float, float],
        resolution: float,
    ) -> GeometrySnapshot: ...

    def query(
        self,
        project: Project | ProjectGeometryView,
        extent: tuple[float, float, float, float],
        resolution: float,
    ) -> GeometrySnapshot: ...

    def query_cached(
        self,
        project: Project | ProjectGeometryView,
        extent: tuple[float, float, float, float],
        resolution: float,
    ) -> GeometrySnapshot | None: ...

    def discard(self, project_id: str) -> None: ...
