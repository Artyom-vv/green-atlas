"""Bounded free-layout search using the ordinary placement evaluator.

Sampling is not packing: rejected and unverified probes never become neighbours.
Only accepted candidates reserve space. The immutable project and source guards
are shared with manual placement; this module makes no CAD membership decisions.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import floor, hypot
from time import monotonic

from app.geometry.preparation import prepare_positions
from app.planning.candidate_queue import CandidateQueue
from app.planning.change_contracts import (
    ChangeSetCandidateResult,
    PlanObjectAddOperation,
)
from app.planning.domain import PlantSpacingIndex
from app.planning.evaluation import (
    CandidateIssue,
    CandidateRejected,
    PlanEvaluation,
    accepted_partial_source_warning,
    candidate_result,
)
from app.planning.rules import (
    compiled_planting_zones,
    default_layout_radius,
    planting_zone_at,
)
from app.planning.trace import operation_rule_trace
from app.regulations.placement_config import PLACEMENT_CONFIG

CONFIG = PLACEMENT_CONFIG.candidate_search
MIN_SEARCH_PROBES = CONFIG.minimum_probes
MAX_SEARCH_PROBES = CONFIG.maximum_probes
PROBES_PER_REQUESTED_PLANT = CONFIG.probes_per_requested_plant
SEARCH_BATCH_SIZE = CONFIG.batch_size
SEARCH_TIME_LIMIT_S = CONFIG.time_limit_s  # Checked between batches, not a CAD timeout


def search_budget(target: int) -> int:
    return min(
        MAX_SEARCH_PROBES, max(MIN_SEARCH_PROBES, target * PROBES_PER_REQUESTED_PLANT)
    )


@dataclass
class CandidateSearchResult:
    candidate_results: list[ChangeSetCandidateResult]
    stop_reason: str
    elapsed_s: float
    spacing_pruned: int = 0


def search_candidates(
    project,
    operations: list[PlanObjectAddOperation],
    evaluation: PlanEvaluation,
    target: int,
    zone_targets: dict[str, int],
    spacing_m: float,
    *,
    time_limit_s: float = SEARCH_TIME_LIMIT_S,
) -> CandidateSearchResult:
    start = monotonic()
    working = project.plan.model_copy(update={"objects": list(project.plan.objects)})
    spacing = PlantSpacingIndex(working.objects)
    compiled = compiled_planting_zones(project)
    results = []
    accepted = Counter()
    # The user's minimum step also applies without a selected species.
    cells = defaultdict(list)
    cell_size = max(spacing_m, CONFIG.minimum_grid_cell_m)
    stop = "candidate_limit"

    def candidate_zone(payload):
        radius = (
            payload.layout_radius_m
            or payload.radius
            or default_layout_radius(payload.kind)
        )
        return planting_zone_at(project, payload.x, payload.y, radius, compiled)

    def close_to_accepted(payload):
        key = (floor(payload.x / cell_size), floor(payload.y / cell_size))
        return any(
            hypot(payload.x - x, payload.y - y) < spacing_m
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for x, y in cells[(key[0] + dx, key[1] + dy)]
        )

    queue = CandidateQueue(operations, spacing_m)
    spacing_pruned = 0
    started = False
    while queue:
        if started and monotonic() - start >= time_limit_s:
            stop = "time_limit"
            break
        started = True
        batch = []
        remaining = target - sum(accepted.values())
        batch_size = min(SEARCH_BATCH_SIZE, max(
            CONFIG.minimum_batch_size, remaining * CONFIG.probes_per_missing_plant,
        ))
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
        if not batch:
            continue
        # Already accepted plants are a sufficient rejection reason. Do not
        # spend CAD queries on their occupied spacing neighbourhoods; rejected
        # or unknown probes never enter this index.
        to_measure = [i for i in batch if not close_to_accepted(operations[i].object)]
        if to_measure:
            prepare_positions(
                evaluation.geometry, project,
                [(operations[i].object.x, operations[i].object.y) for i in to_measure],
            )
        for index in batch:
            operation = operations[index]
            payload = operation.object
            zone = candidate_zone(payload)
            if (
                zone_targets
                and zone
                and accepted[zone.id] >= zone_targets.get(zone.id, 0)
            ):
                continue
            if close_to_accepted(payload):
                results.append(candidate_result(index, "add", CandidateIssue(
                    status="blocked", code="PLANT_SPACING", category="spacing",
                    message="Слишком близко к принятой посадке", required_distance_m=spacing_m,
                    zone_id=zone.id if zone else None,
                    suggested_action="Выбрать позицию дальше от принятых посадок",
                ), None, ""))
                continue
            try:
                candidate, issue = evaluation.preview_addition(
                    project, working, payload, spacing, compiled
                )
                key = (floor(candidate.x / cell_size), floor(candidate.y / cell_size))
                if any(
                    hypot(candidate.x - x, candidate.y - y) < spacing_m
                    for dx in (-1, 0, 1)
                    for dy in (-1, 0, 1)
                    for x, y in cells[(key[0] + dx, key[1] + dy)]
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
                allowed = issue is None or accepted_partial_source_warning(
                    project, status=issue.status, code=issue.code
                )
                if allowed:
                    accepted[candidate.planting_zone_id] += 1
                    working.objects.append(candidate)
                    spacing.add(candidate)
                    cells[key].append((candidate.x, candidate.y))
                    spacing_pruned += queue.accept(candidate.x, candidate.y)
            except CandidateRejected as error:
                result = candidate_result(index, "add", error.issue, None, "")
            result.rule_trace = operation_rule_trace(
                evaluation.geometry, project, operation
            )
            results.append(result)
            if sum(accepted.values()) >= target:
                stop = "target_reached"
                break
        if stop == "target_reached":
            break
    return CandidateSearchResult(results, stop, monotonic() - start, spacing_pruned)
