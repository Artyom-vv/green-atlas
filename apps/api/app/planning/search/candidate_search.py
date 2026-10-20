"""Ограниченный свободный поиск через общий оценщик; только принятые кандидаты
резервируют расстояние до соседей.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import floor, hypot
from time import monotonic
from typing import Literal

from app.geometry.preparation import calculation_scope, prepare_positions
from app.operations.progress import check_operation_cancelled
from app.planning.changes.contracts import (
    ChangeSetCandidateResult,
    PlanObjectAddOperation,
)
from app.planning.changes.evaluation import (
    CandidateIssue,
    CandidateRejected,
    PlanEvaluation,
    candidate_result,
    informational_source_warning,
)
from app.planning.changes.trace import operation_rule_trace
from app.planning.contracts import PlanObjectCreate
from app.planning.domain import PlantSpacingIndex
from app.planning.rules import (
    compiled_planting_zones,
    default_layout_radius,
    planting_zone_at,
)
from app.planning.search.candidate_queue import CandidateQueue, CandidateSearchQueue
from app.planning.search.spacing_precheck import (
    SavedPlanSpacingPrecheck,
    search_trace_code_bound,
    utility_assumption_codes,
)
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.regulations.placement_config import PLACEMENT_CONFIG
from app.shared.failures import DomainFailure, FailureReason

CONFIG = PLACEMENT_CONFIG.candidate_search
MIN_SEARCH_PROBES = CONFIG.minimum_probes
MAX_SEARCH_PROBES = CONFIG.maximum_probes
PROBES_PER_REQUESTED_PLANT = CONFIG.probes_per_requested_plant
SEARCH_BATCH_SIZE = CONFIG.batch_size
SEARCH_TIME_LIMIT_S = (
    CONFIG.time_limit_s
)  # Проверяется между порциями; это не таймаут CAD.


def search_budget(target: int) -> int:
    return min(
        MAX_SEARCH_PROBES, max(MIN_SEARCH_PROBES, target * PROBES_PER_REQUESTED_PLANT)
    )


@dataclass
class CandidateSearchResult:
    candidate_results: list[ChangeSetCandidateResult]
    stop_reason: Literal[
        "target_reached", "candidate_limit", "time_limit", "domain_exhausted"
    ]
    elapsed_s: float
    spacing_pruned: int = 0
    saved_spacing_pruned: int = 0
    saved_spacing_full_traces: int = 0
    considered_count: int | None = None
    full_checked_count: int = 0


def search_candidates(
    project: Project,
    operations: list[PlanObjectAddOperation],
    evaluation: PlanEvaluation,
    target: int,
    zone_targets: dict[str, int],
    spacing_m: float,
    *,
    time_limit_s: float = SEARCH_TIME_LIMIT_S,
    queue: CandidateSearchQueue | None = None,
    plant_kinds: frozenset[str] | None = None,
    retain_rejected_traces: bool = True,
) -> CandidateSearchResult:
    start = monotonic()
    if project.plan is None:
        raise DomainFailure(FailureReason.PLAN_MISSING)
    with calculation_scope(evaluation.geometry, project):
        return _search_candidates(
            project,
            operations,
            evaluation,
            target,
            zone_targets,
            spacing_m,
            start=start,
            time_limit_s=time_limit_s,
            queue=queue,
            plant_kinds=plant_kinds,
            retain_rejected_traces=retain_rejected_traces,
        )


def _search_candidates(
    project: Project,
    operations: list[PlanObjectAddOperation],
    evaluation: PlanEvaluation,
    target: int,
    zone_targets: dict[str, int],
    spacing_m: float,
    *,
    start: float,
    time_limit_s: float,
    queue: CandidateSearchQueue | None = None,
    plant_kinds: frozenset[str] | None = None,
    retain_rejected_traces: bool = True,
) -> CandidateSearchResult:
    assert project.plan is not None
    working = project.plan.model_copy(update={"objects": list(project.plan.objects)})
    spacing = PlantSpacingIndex(
        working.objects, planning_horizon_year=working.planning_horizon_year
    )
    compiled = compiled_planting_zones(project)
    trace_bound = search_trace_code_bound(
        evaluation.geometry,
        project,
        plant_kinds
        if plant_kinds is not None
        else frozenset(operation.object.kind for operation in operations),
    )
    saved_spacing = (
        SavedPlanSpacingPrecheck(project) if trace_bound is not None else None
    )
    seen_assumptions: set[str] = set()
    results: list[ChangeSetCandidateResult] = []
    accepted: Counter[str | None] = Counter()
    # Минимальный шаг пользователя действует и без выбранного вида растения.
    cells: defaultdict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    cell_size = max(spacing_m, CONFIG.minimum_grid_cell_m)
    stop: Literal[
        "target_reached", "candidate_limit", "time_limit", "domain_exhausted"
    ] = "candidate_limit"

    def candidate_zone(payload: PlanObjectCreate) -> PlantingZoneAssignment | None:
        radius = (
            payload.layout_radius_m
            or payload.radius
            or default_layout_radius(payload.kind)
        )
        return planting_zone_at(project, payload.x, payload.y, radius, compiled)

    def close_to_accepted(payload: PlanObjectCreate) -> bool:
        key = (floor(payload.x / cell_size), floor(payload.y / cell_size))
        return any(
            hypot(payload.x - x, payload.y - y) < spacing_m
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for x, y in cells.get((key[0] + dx, key[1] + dy), ())
        )

    queue = queue if queue is not None else CandidateQueue(operations, spacing_m)
    spacing_pruned = 0
    saved_spacing_pruned = 0
    saved_spacing_full_traces = 0
    full_checked_count = 0
    started = False
    while queue:
        check_operation_cancelled()
        if started and monotonic() - start >= time_limit_s:
            stop = "time_limit"
            break
        started = True
        batch = []
        batch_zones: dict[int, PlantingZoneAssignment | None] = {}
        remaining = target - sum(accepted.values())
        batch_size = min(
            SEARCH_BATCH_SIZE,
            max(
                CONFIG.minimum_batch_size,
                remaining * CONFIG.probes_per_missing_plant,
            ),
        )
        for index in queue.batch(batch_size):
            payload = operations[index].object
            zone = candidate_zone(payload)
            if (
                zone_targets
                and zone
                and accepted[zone.id] >= zone_targets.get(zone.id, 0)
            ):
                continue
            batch.append(index)
            batch_zones[index] = zone
        if not batch:
            continue
        # Принятые посадки уже достаточны для отказа по расстоянию: не тратим
        # CAD-запросы на их соседство. Отклонённые и неизвестные пробы в индекс не входят.
        saved_rejections = {
            i
            for i in batch
            if saved_spacing is not None
            and not close_to_accepted(operations[i].object)
            and (not project.planting_zones or batch_zones[i] is not None)
            and saved_spacing.violates(operations[i].object)
        }
        to_measure = [
            i
            for i in batch
            if i not in saved_rejections and not close_to_accepted(operations[i].object)
        ]
        if to_measure:
            prepare_positions(
                evaluation.geometry,
                project,
                [(operations[i].object.x, operations[i].object.y) for i in to_measure],
            )
        for index in batch:
            operation = operations[index]
            payload = operation.object
            zone = batch_zones[index]
            if (
                zone_targets
                and zone
                and accepted[zone.id] >= zone_targets.get(zone.id, 0)
            ):
                continue
            if close_to_accepted(payload):
                results.append(
                    candidate_result(
                        index,
                        "add",
                        CandidateIssue(
                            status="blocked",
                            code="PLANT_SPACING",
                            category="spacing",
                            message="Слишком близко к принятой посадке",
                            required_distance_m=spacing_m,
                            zone_id=zone.id if zone else None,
                            suggested_action="Выбрать позицию дальше от принятых посадок",
                        ),
                        None,
                        "",
                    )
                )
                continue
            if index in saved_rejections:
                result = candidate_result(
                    index,
                    "add",
                    CandidateIssue(
                        status="blocked",
                        code="PLANT_SPACING",
                        category="spacing",
                        message="Объект расположен слишком близко к существующим посадкам",
                        rule_id="group-spacing",
                        suggested_action="Увеличить расстояние или изменить политику плотности",
                        zone_id=zone.id if zone else None,
                    ),
                    None,
                    "",
                )
                # Ранний отказ не проверяет CAD. Полная трасса нужна, пока её
                # сетевой код ещё может дополнить сводку. После достижения
                # объявленной верхней границы объединение кодов не изменится.
                assert trace_bound is not None
                if not trace_bound.issubset(seen_assumptions):
                    result.rule_trace = operation_rule_trace(
                        evaluation.geometry, project, operation
                    )
                    seen_assumptions.update(utility_assumption_codes(result.rule_trace))
                    saved_spacing_full_traces += 1
                saved_spacing_pruned += 1
                results.append(result)
                continue
            allowed = False
            try:
                full_checked_count += 1
                candidate, issue = evaluation.preview_addition(
                    project, working, payload, spacing, compiled
                )
                key = (floor(candidate.x / cell_size), floor(candidate.y / cell_size))
                if any(
                    hypot(candidate.x - x, candidate.y - y) < spacing_m
                    for dx in (-1, 0, 1)
                    for dy in (-1, 0, 1)
                    for x, y in cells.get((key[0] + dx, key[1] + dy), ())
                ):
                    raise CandidateRejected(
                        CandidateIssue(
                            status="blocked",
                            code="PLANT_SPACING",
                            category="spacing",
                            message="Слишком близко к принятой посадке",
                            required_distance_m=spacing_m,
                            zone_id=candidate.planting_zone_id,
                            suggested_action="Выбрать позицию дальше от принятых посадок",
                        )
                    )
                result = candidate_result(
                    index,
                    "add",
                    issue,
                    candidate.id,
                    "Позиция проходит текущую проверку",
                    candidate.planting_zone_id,
                )
                allowed = issue is None or informational_source_warning(
                    project, status=issue.status, code=issue.code
                )
                if allowed:
                    accepted[candidate.planting_zone_id] += 1
                    working.objects.append(candidate)
                    spacing.add(candidate)
                    cells[key].append((candidate.x, candidate.y))
                    spacing_pruned += queue.accept(
                        candidate.x, candidate.y, zone_id=candidate.planting_zone_id
                    )
            except CandidateRejected as error:
                result = candidate_result(index, "add", error.issue, None, "")
            # Отказ уже содержит точную причину общего оценщика. В потоковом
            # поиске полные повторные трассы отказов не показываются: для сводки
            # сетей сохраняем свидетеля каждого нового кода, а у принятых позиций
            # оставляем полную трассу. Без объявленной границы всё равно читаем
            # каждую трассу, чтобы не потерять новый сетевой пробел.
            if (
                retain_rejected_traces
                or allowed
                or trace_bound is None
                or not trace_bound.issubset(seen_assumptions)
            ):
                trace = operation_rule_trace(evaluation.geometry, project, operation)
                assumptions = utility_assumption_codes(trace)
                if retain_rejected_traces or allowed or assumptions - seen_assumptions:
                    result.rule_trace = trace
                seen_assumptions.update(assumptions)
            results.append(result)
            if sum(accepted.values()) >= target:
                stop = "target_reached"
                break
        if stop == "target_reached":
            break
    if stop not in {"target_reached", "time_limit"}:
        stop = queue.stop_reason
    return CandidateSearchResult(
        results,
        stop,
        monotonic() - start,
        spacing_pruned,
        saved_spacing_pruned,
        saved_spacing_full_traces,
        full_checked_count=full_checked_count,
    )
