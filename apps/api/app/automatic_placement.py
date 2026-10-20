"""Детерминированный подбор вида и мест посадок через общий планировщик; отдельного
расчёта геометрии нет.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app import building_screen
from app.application import ProjectApplication
from app.geometry.building_contracts import BuildingScreenRequest
from app.planning.changes.contracts import ChangeSetPreview
from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR, PlanningHorizonYear
from app.planning.domain import PlanVersionConflict
from app.planning.patterns.contracts import (
    FillPatternRequest,
    PatternPreviewRequest,
    PlacementMaskRequest,
)
from app.planning.search.contracts import SearchDomain
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason
from app.species.contracts import SpeciesShortlistItem

PlantKind = Literal["tree", "shrub"]
SelectedKind = Literal["tree", "shrub", "mixed"]
MIXED_LAYOUT_SAMPLE_LIMIT = 8
MAX_AUTOMATIC_PLACEMENT_COUNT = 100
AUTOMATIC_SELECTION_BASIS = (
    "Выбрано больше проверенных мест; при равенстве у зданий предпочтены "
    "кустарники, у дорог и по площади деревья. Это не оценка условий роста."
)
EXPLICIT_SELECTION_BASIS = (
    "Тип растения выбран пользователем; позиции проверены по правилам проекта."
)


class AutomaticPlacementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_horizon_year: PlanningHorizonYear = DEFAULT_PLANNING_HORIZON_YEAR
    base_plan_version: int = Field(ge=1)
    zone_id: str = Field(min_length=1)
    near: Literal["building", "road", "area"] = "area"
    plant_kind: Literal["auto", "tree", "shrub", "mixed"] = "auto"
    target_count: int = Field(default=8, ge=1, le=MAX_AUTOMATIC_PLACEMENT_COUNT)


class AutomaticPlacementPreview(BaseModel):
    planning_horizon_year: PlanningHorizonYear = DEFAULT_PLANNING_HORIZON_YEAR
    requested: int
    found: int
    shortfall: int
    selected_kind: SelectedKind
    selection_basis: str
    species_revision_ids: list[str]
    change_set: ChangeSetPreview | None = None
    search_domains: list[SearchDomain] = Field(default_factory=list)


def preview_automatic_placement(
    application: ProjectApplication,
    project_id: str,
    request: AutomaticPlacementRequest,
) -> AutomaticPlacementPreview:
    project = application.get(project_id, lightweight=True)
    if project.plan is None:
        raise DomainFailure(FailureReason.PLAN_MISSING)
    if project.plan.version != request.base_plan_version:
        raise PlanVersionConflict(request.base_plan_version, project.plan.version)
    if request.zone_id not in {zone.id for zone in project.planting_zones}:
        raise DomainFailure(FailureReason.ZONE_MISSING, {"zone_ids": [request.zone_id]})

    kinds: tuple[SelectedKind, ...] = (
        (("shrub", "tree") if request.near == "building" else ("tree", "shrub"))
        if request.plant_kind == "auto"
        else (request.plant_kind,)
    )
    best: tuple[int, SelectedKind, ChangeSetPreview | None] | None = None
    search_domains: list[SearchDomain] = []
    pending_kind: SelectedKind | None = None
    for kind in kinds:
        change, domains = (
            _preview_mixed(application, project, request)
            if kind == "mixed"
            else _preview_single(application, project, request, kind)
        )
        search_domains.extend(domains)
        if any(domain.stop_reason != "resolution" for domain in domains):
            pending_kind = pending_kind or kind
        found = len(change.additions) if change and change.can_apply else 0
        # При равной оценке выигрывает первый вид: порядок задаёт предпочтение контекста.
        if best is None or found > best[0]:
            best = (found, kind, change)
        if found == request.target_count:
            break

    assert best is not None
    found, kind, change = best
    if pending_kind:
        # Незавершённый вид ещё может изменить выбор: готовые альтернативы не публикуются.
        return AutomaticPlacementPreview(
            planning_horizon_year=request.scenario_horizon_year,
            requested=request.target_count,
            found=0,
            shortfall=0,
            selected_kind=pending_kind,
            selection_basis="Проверяем доступные места",
            species_revision_ids=[],
            search_domains=search_domains,
        )
    return AutomaticPlacementPreview(
        planning_horizon_year=request.scenario_horizon_year,
        requested=request.target_count,
        found=found,
        shortfall=request.target_count - found,
        selected_kind=kind,
        selection_basis=AUTOMATIC_SELECTION_BASIS
        if request.plant_kind == "auto"
        else EXPLICIT_SELECTION_BASIS,
        species_revision_ids=sorted(
            {
                item.species_revision_id
                for item in change.additions
                if item.species_revision_id is not None
            }
        )
        if change and found
        else [],
        change_set=change if found else None,
        search_domains=search_domains,
    )


@contextmanager
def _stable_revision(
    application: ProjectApplication, project: Project
) -> Iterator[None]:
    before = application.get(project.id, lightweight=True).state_version
    yield
    after = application.get(project.id, lightweight=True).state_version
    if before != after:
        raise ValueError("Проект изменился во время расчёта. Повторите запрос")
    if before != project.state_version:
        raise ValueError("Проект изменился во время подготовки предложения")


def _shortlist(
    application: ProjectApplication, project: Project, zone_id: str, kind: PlantKind
) -> list[SpeciesShortlistItem]:
    with _stable_revision(application, project):
        return application.shortlist_species(project.id, [], [zone_id], kind)


def _pattern_request(
    request: AutomaticPlacementRequest,
    kind: SelectedKind,
    species_id: str | None = None,
    *,
    tree_id: str | None = None,
    shrub_id: str | None = None,
    seed: int = 1,
) -> PatternPreviewRequest:
    fill = FillPatternRequest(
        scenario_horizon_year=request.scenario_horizon_year,
        base_plan_version=request.base_plan_version,
        zone_ids=[request.zone_id],
        plant_kind="tree" if kind == "mixed" else kind,
        composition="mixed" if kind == "mixed" else None,
        species_revision_id=species_id,
        tree_species_revision_id=tree_id,
        shrub_species_revision_id=shrub_id,
        target_count=request.target_count,
        placement_mode="count",
        layout="natural",
        size_class="standard",
        spacing_policy="balanced",
        seed=seed,
    )
    if request.near == "area":
        return fill
    # Общие значения по умолчанию и проверки принадлежат обеим публичным моделям запроса.
    return PlacementMaskRequest.model_validate(
        fill.model_dump(exclude={"type", "layout"})
        | {
            "type": "mask",
            "mask_id": "road_edges" if request.near == "road" else "building_contour",
        }
    )


def _preview_single(
    application: ProjectApplication,
    project: Project,
    request: AutomaticPlacementRequest,
    kind: PlantKind,
) -> tuple[ChangeSetPreview | None, list[SearchDomain]]:
    candidates = _shortlist(application, project, request.zone_id, kind)
    if not candidates:
        raise ValueError("В каталоге не найдены подходящие породы")
    available = [item for item in candidates if item.status == "available"]
    species_id = (available or candidates)[0].species.id
    with _stable_revision(application, project):
        if request.near == "building" and kind == "tree":
            proposal = building_screen.preview(
                application,
                project.id,
                BuildingScreenRequest(
                    scenario_horizon_year=request.scenario_horizon_year,
                    base_plan_version=request.base_plan_version,
                    zone_ids=[request.zone_id],
                    species_revision_id=species_id,
                    max_sites=request.target_count,
                    arrangement="contour",
                    size_class="standard",
                    spacing_policy="balanced",
                ),
            )
            change = proposal.change_set
            domains = proposal.search_domains
        else:
            result = application.preview_pattern(
                project.id, _pattern_request(request, kind, species_id)
            )
            change, domains = result.change_set, result.search_domains
    if any(domain.stop_reason != "resolution" for domain in domains):
        return None, domains
    additions = change.additions if change else []
    if any(item.species_revision_id != species_id for item in additions):
        raise ValueError("Результат не соответствует выбранной породе")
    if len(additions) > request.target_count:
        raise ValueError("Результат превышает заданное количество")
    if any(item.planting_zone_id != request.zone_id for item in additions):
        raise ValueError("Результат выходит за выбранные участки")
    return change, domains


def _preview_mixed(
    application: ProjectApplication,
    project: Project,
    request: AutomaticPlacementRequest,
) -> tuple[ChangeSetPreview | None, list[SearchDomain]]:
    chosen: dict[PlantKind, str] = {}
    kinds: tuple[PlantKind, ...] = ("tree", "shrub")
    for kind in kinds:
        options = _shortlist(application, project, request.zone_id, kind)
        available = [item for item in options if item.status == "available"]
        if not available:
            raise ValueError(
                "Для смешанного состава не найдены подходящие деревья и кустарники"
            )
        chosen[kind] = available[0].species.id
    best: tuple[int, ChangeSetPreview | None] | None = None
    search_domains: list[SearchDomain] = []
    for seed in range(1, MIXED_LAYOUT_SAMPLE_LIMIT + 1):
        with _stable_revision(application, project):
            result = application.preview_pattern(
                project.id,
                _pattern_request(
                    request,
                    "mixed",
                    tree_id=chosen["tree"],
                    shrub_id=chosen["shrub"],
                    seed=seed,
                ),
            )
        search_domains.extend(result.search_domains)
        if any(domain.stop_reason != "resolution" for domain in result.search_domains):
            return None, search_domains
        change = result.change_set
        additions = change.additions if change else []
        complete = {item.kind for item in additions} == {"tree", "shrub"}
        score = len(additions) if change and change.can_apply and complete else 0
        if best is None or score > best[0]:
            best = score, change
        if score >= request.target_count:
            break
    assert best is not None
    change = best[1]
    additions = change.additions if change else []
    if any(
        item.planting_zone_id != request.zone_id
        or item.species_revision_id != chosen[item.kind]
        for item in additions
    ):
        raise ValueError("Результат не соответствует участку или составу")
    if len(additions) > request.target_count:
        raise ValueError("Результат превышает общее количество растений")
    if additions and {item.kind for item in additions} != {"tree", "shrub"}:
        raise ValueError(
            "В проверенных позициях не удалось разместить оба типа растений"
        )
    return change, search_domains
