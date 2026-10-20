"""Точный ранний отказ по сохранённым посадкам без проверки геометрии CAD."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from app.geometry.ports import GeometryEnginePort, GeometrySearchTraceSummaryPort
from app.planning.contracts import PlanObject, PlanObjectCreate
from app.planning.domain import PlantSpacingIndex
from app.planning.rules import default_layout_radius
from app.projects.contracts import Project
from app.regulations.trace_contracts import PlantingRuleTrace
from app.shared.failures import DomainFailure
from app.species.catalog import get_species, growth_forecasts


def search_trace_code_bound(
    geometry: GeometryEnginePort, project: Project, plant_kinds: frozenset[str]
) -> frozenset[str] | None:
    """Только объявленный провайдером предел кодов разрешает ранний путь.

    Динамический CAD-роутер не должен синтезировать capability чужого движка.
    Прежние провайдеры без этой возможности сохраняют полный путь поиска.
    """
    method = getattr(type(geometry), "search_trace_assumption_codes", None)
    if not callable(method):
        return None
    return cast(GeometrySearchTraceSummaryPort, geometry).search_trace_assumption_codes(
        project, plant_kinds
    )


def utility_assumption_codes(trace: PlantingRuleTrace | None) -> set[str]:
    """Тот же набор, который объединяет network_review_reason для предпросмотра."""
    if trace is None:
        return set()
    return {
        entry.code
        for entry in trace.entries
        if entry.obstacle_kind == "utility" and entry.status == "not_checked"
    }


@dataclass(frozen=True)
class _TemplateKey:
    kind: str
    radius: float
    species_revision_id: str | None
    size_class: str
    group_ids: tuple[str, ...]
    spacing_policy: str


def spacing_template(payload: PlanObjectCreate, radius: float) -> PlanObject | None:
    """Тот же вид, габарит, группа и прогноз, что у кандидата общего оценщика."""
    candidate = PlanObject(
        id="search-spacing-template",
        **payload.model_dump(exclude={"radius", "layout_radius_m"}),
        radius=radius,
        layout_radius_m=radius,
    )
    if payload.species_revision_id:
        try:
            revision = get_species(payload.species_revision_id)
            if revision.kind != payload.kind:
                return None
            candidate.canopy_forecast, candidate.root_forecast = growth_forecasts(
                revision, payload.size_class
            )
        except DomainFailure:
            return None
    return candidate


class SavedPlanSpacingPrecheck:
    """Индекс исходного плана и максимум два шаблона для обычной смеси видов.

    Новые принятые кандидаты в этот индекс не входят. Их по-прежнему проверяют
    очередь и общий оценщик. Шаблоны никогда не становятся объектами результата.
    """

    def __init__(self, project: Project) -> None:
        assert project.plan is not None
        self.horizon = project.plan.planning_horizon_year
        self.index = PlantSpacingIndex(
            project.plan.objects, planning_horizon_year=self.horizon
        )
        self.templates: dict[_TemplateKey, PlanObject | None] = {}

    def violates(self, payload: PlanObjectCreate) -> bool:
        if not self.index.cells:
            return False
        radius = (
            payload.layout_radius_m
            or payload.radius
            or default_layout_radius(payload.kind)
        )
        key = _TemplateKey(
            payload.kind,
            radius,
            payload.species_revision_id,
            payload.size_class,
            tuple(payload.group_ids),
            payload.spacing_policy,
        )
        if key not in self.templates:
            self.templates[key] = spacing_template(payload, radius)
        template = self.templates[key]
        if template is None:
            # Ошибку вида или прогноза обрабатывает прежний общий оценщик.
            return False
        candidate = template.model_copy(update={"x": payload.x, "y": payload.y})
        return not self.index.respects(candidate)
