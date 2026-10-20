"""Холодный согласованный поиск роста по одной области и одному запасу точек.

Общий оценщик определяет срок пригодности каждой точки относительно исходного
плана. Семья раскладок меняет только расстояния между новыми растениями.
Ни маска, ни запас предложений не зависят от запрошенного горизонта.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from shapely.geometry import Polygon
from shapely.ops import unary_union

from app.geometry.preparation import calculation_scope, prepare_positions
from app.operations.progress import check_operation_cancelled
from app.planning.changes.contracts import PlanObjectAddOperation
from app.planning.changes.evaluation import (
    CandidateIssue,
    CandidateRejected,
    PlanEvaluation,
    informational_source_warning,
)
from app.planning.config import MAX_PATTERN_CANDIDATES, MAX_PLANNING_HORIZON_YEAR
from app.planning.contracts import PlanObject
from app.planning.domain import PlantSpacingIndex, planning_radius
from app.planning.patterns.contracts import FillPatternRequest, PatternSkippedCandidate
from app.planning.patterns.generators import usable_zone_geometry
from app.planning.ports import PatternCandidate
from app.planning.rules import (
    compiled_planting_zones,
    default_layout_radius,
    effective_pattern_spacing,
    planting_zone_at,
)
from app.planning.scenario import scenario_project
from app.planning.search.candidate_queue import SearchStopReason
from app.planning.search.candidate_search import (
    SEARCH_TIME_LIMIT_S,
    CandidateSearchResult,
    search_candidates,
)
from app.planning.search.horizon_candidates import generate_horizon_candidates
from app.planning.search.horizon_family import (
    HorizonCandidate,
    HorizonFamilyTimeout,
    select_horizon_family,
)
from app.planning.search.occupied_centers import unoccupied_centers
from app.planning.search.sampling import polygon_components
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project

OperationFactory = Callable[[list[PatternCandidate], int], list[PlanObjectAddOperation]]
MAX_NUMERIC_WORK = 5_000_000
POINT_BATCH_SIZE = 32


@dataclass
class HorizonSearchResult:
    candidates: list[PatternCandidate]
    skipped: list[PatternSkippedCandidate]
    operations: list[PlanObjectAddOperation]
    search: CandidateSearchResult


class HorizonSearchUnconfirmed(ValueError):
    """Итоговая проверка не подтвердила целиком согласованный вариант."""


class _OrderedQueue:
    """Повторная проверка целого варианта без перестановки и отсечения соседей."""

    stop_reason: SearchStopReason = "domain_exhausted"

    def __init__(self, length: int) -> None:
        self.length = length
        self.cursor = 0

    def __bool__(self) -> bool:
        return self.cursor < self.length

    def batch(self, limit: int) -> list[int]:
        end = min(self.length, self.cursor + limit)
        result = list(range(self.cursor, end))
        self.cursor = end
        return result

    def accept(self, x: float, y: float, *, zone_id: str | None = None) -> int:
        return 0


def search_horizon_placement(
    project: Project,
    request: FillPatternRequest,
    generation_zones: list[PlantingZoneAssignment],
    evaluation: PlanEvaluation,
    zone_targets: dict[str, int],
    operation_factory: OperationFactory,
    *,
    time_limit_s: float = SEARCH_TIME_LIMIT_S,
) -> HorizonSearchResult:
    """Вернуть согласованный вариант либо явный незавершённый расчёт.

    Wall-clock не выбирает меньшую частичную раскладку. Геометрический запас и
    численный work budget одинаковы во всех независимых запусках одного запроса.
    """
    started = monotonic()

    def checkpoint() -> None:
        check_operation_cancelled()
        if monotonic() - started >= time_limit_s:
            raise HorizonFamilyTimeout("Согласованный поиск не завершён вовремя")

    checkpoint()
    base = scenario_project(project, 0)
    assert base.plan is not None
    # Старый сохранённый прогноз также участвует в допуске точки. Плохая
    # убывающая крона не должна превращаться в ложное предположение о сроке.
    for saved in base.plan.objects:
        checkpoint()
        previous = planning_radius(saved, 0)
        for year in range(1, MAX_PLANNING_HORIZON_YEAR + 1):
            current = planning_radius(saved, year)
            if current < previous:
                raise HorizonSearchUnconfirmed("Сохранённый прогноз роста убывает")
            previous = current
    spacing = [
        effective_pattern_spacing(request, year)
        for year in range(MAX_PLANNING_HORIZON_YEAR + 1)
    ]
    radius = request.layout_radius_m or default_layout_radius(request.plant_kind)
    work_zones = {zone.id: zone.geometry for zone in project.planting_zones}
    prototype = operation_factory([PatternCandidate(0, 0)], 0)[0].object
    parts: list[Polygon] = []
    for zone in generation_zones:
        checkpoint()
        centers = zone.model_copy(
            update={
                "geometry": {
                    **zone.geometry,
                    "ga_work_zone": zone.geometry.get(
                        "ga_work_zone", work_zones[zone.id]
                    ),
                }
            }
        )
        usable = usable_zone_geometry(centers, max(request.edge_offset_m, radius))
        if request.composition != "mixed":
            unoccupied = unoccupied_centers(base, prototype, usable)
            if unoccupied is not None:
                usable = unoccupied
        parts.extend(polygon_components(usable))
    geometry = unary_union(parts) if parts else Polygon()
    checkpoint()
    target = (
        request.target_count
        if request.placement_mode == "count"
        else MAX_PATTERN_CANDIDATES
    )
    pool = generate_horizon_candidates(
        geometry,
        spacing[0],
        spacing[-1],
        request.target_count if request.placement_mode == "count" else None,
        request.seed,
    )
    checkpoint()
    operations = operation_factory(pool, 0)
    selected_zones = set(request.zone_ids)
    compiled = compiled_planting_zones(base)
    scenarios = [scenario_project(project, year) for year in range(41)]
    saved_spacing = [
        PlantSpacingIndex(base.plan.objects, planning_horizon_year=year)
        for year in range(41)
    ]
    templates: list[PlanObject] = []
    template_keys: dict[tuple, int] = {}
    points: list[HorizonCandidate] = []
    point_to_pool: list[int] = []
    full_checks = 0
    base_candidate: PlanObject | None = None
    base_issue: CandidateIssue | None = None

    def admitted(index: int, year: int) -> bool:
        nonlocal full_checks, base_candidate, base_issue
        checkpoint()
        scenario = scenarios[year]
        assert scenario.plan is not None
        full_checks += 1
        try:
            if year == 0:
                base_candidate, base_issue = evaluation.preview_addition(
                    scenario,
                    scenario.plan,
                    operations[index].object,
                    saved_spacing[year],
                    compiled,
                )
                issue = base_issue
            else:
                assert base_candidate is not None
                issue = evaluation.preview_horizon(
                    scenario,
                    scenario.plan,
                    base_candidate,
                    saved_spacing[year],
                    base_issue=base_issue,
                )
        except CandidateRejected:
            return False
        return issue is None or informational_source_warning(
            scenario, status=issue.status, code=issue.code
        )

    # Порция сохраняет проверку файлов при выходе, включая отрицательный ответ.
    # Измерения одной точки переиспользуются для всех проверяемых годов.
    with calculation_scope(evaluation.geometry, base):
        for start in range(0, len(pool), POINT_BATCH_SIZE):
            checkpoint()
            end = min(len(pool), start + POINT_BATCH_SIZE)
            batch = []
            for index in range(start, end):
                payload = operations[index].object
                candidate_radius = payload.layout_radius_m or default_layout_radius(
                    payload.kind
                )
                candidate_zone = planting_zone_at(
                    base, payload.x, payload.y, candidate_radius, compiled
                )
                if candidate_zone is not None and candidate_zone.id in selected_zones:
                    batch.append((index, candidate_zone.id, candidate_radius))
            prepare_positions(
                evaluation.geometry, base, [(pool[i].x, pool[i].y) for i, _, _ in batch]
            )
            for index, zone_id, candidate_radius in batch:
                if not admitted(index, 0):
                    continue
                low, high = 0, MAX_PLANNING_HORIZON_YEAR
                if admitted(index, high):
                    low = high
                else:
                    # Габариты, отступы и расстояния текущих правил монотонны.
                    # Итоговый выбранный год всё равно проверяется отдельно ниже.
                    while high - low > 1:
                        middle = (low + high) // 2
                        if admitted(index, middle):
                            low = middle
                        else:
                            high = middle
                payload = operations[index].object
                key = (
                    payload.kind,
                    payload.species_revision_id,
                    payload.size_class,
                    candidate_radius,
                    payload.spacing_policy,
                    tuple(payload.group_ids),
                )
                if key not in template_keys:
                    assert base_candidate is not None
                    template_keys[key] = len(templates)
                    templates.append(base_candidate)
                points.append(
                    HorizonCandidate(
                        pool[index].x, pool[index].y, low, zone_id, template_keys[key]
                    )
                )
                point_to_pool.append(index)
        checkpoint()
        if not points:
            return HorizonSearchResult(
                [],
                [],
                [],
                CandidateSearchResult(
                    [],
                    "candidate_limit",
                    monotonic() - started,
                    considered_count=len(pool),
                    full_checked_count=full_checks,
                ),
            )
        family = select_horizon_family(
            points,
            templates,
            request.scenario_horizon_year,
            spacing,
            target,
            zone_targets,
            maximum_checks=MAX_NUMERIC_WORK,
            time_limit_s=max(0.0, time_limit_s - (monotonic() - started)),
        )
        chosen = [point_to_pool[index] for index in family.selected_indices]
        candidates = [pool[index] for index in chosen]
        accepted_operations = [operations[index] for index in chosen]
        checkpoint()
        verified = (
            search_candidates(
                project,
                accepted_operations,
                evaluation,
                len(chosen),
                zone_targets,
                spacing[request.scenario_horizon_year],
                queue=_OrderedQueue(len(chosen)),
                time_limit_s=max(0.0, time_limit_s - (monotonic() - started)),
                plant_kinds=frozenset(op.object.kind for op in accepted_operations),
                retain_rejected_traces=False,
            )
            if chosen
            else CandidateSearchResult([], "candidate_limit", 0)
        )
        checkpoint()
        if len(verified.candidate_results) != len(chosen) or any(
            item.status != "allowed"
            and not informational_source_warning(
                project, status=item.status, code=item.code
            )
            for item in verified.candidate_results
        ):
            raise HorizonSearchUnconfirmed(
                "Общий оценщик не подтвердил раскладку целиком"
            )
        verified.considered_count = len(pool)
        verified.full_checked_count += full_checks
        verified.elapsed_s = monotonic() - started
        verified.stop_reason = (
            "target_reached" if len(chosen) >= target else "candidate_limit"
        )
        return HorizonSearchResult(candidates, [], accepted_operations, verified)
