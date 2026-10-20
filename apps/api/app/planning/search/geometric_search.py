"""Подбирает запрошенное количество по геометрии нескольких раскладок.

Область центров ограничивает предложения, а допуск каждой посадки остаётся
у общего оценщика. Бюджет и достигнутая точность не доказывают вместимость.
"""

from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from fractions import Fraction
from math import hypot, inf, isfinite, nextafter, ulp
from time import monotonic

from shapely.geometry import MultiPolygon, Point, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union
from shapely.prepared import PreparedGeometry

from app.operations.progress import check_operation_cancelled
from app.planning.changes.contracts import PlanObjectAddOperation
from app.planning.changes.evaluation import PlanEvaluation, informational_source_warning
from app.planning.patterns.contracts import FillPatternRequest, PatternSkippedCandidate
from app.planning.patterns.generators import usable_zone_geometry
from app.planning.ports import PatternCandidate
from app.planning.rules import (
    compiled_planting_zones,
    default_layout_radius,
    planting_zone_at,
)
from app.planning.search.candidate_queue import SearchStopReason
from app.planning.search.candidate_search import (
    SEARCH_TIME_LIMIT_S,
    CandidateSearchResult,
    search_candidates,
)
from app.planning.search.geometric_layouts import propose_target_layouts
from app.planning.search.geometry_candidates import GeometryCandidateSearch
from app.planning.search.occupied_centers import unoccupied_centers
from app.planning.search.sampling import polygon_components
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.regulations.placement_config import PLACEMENT_CONFIG
from app.shared.messages import message

OperationFactory = Callable[[list[PatternCandidate], int], list[PlanObjectAddOperation]]
CompiledZone = tuple[PlantingZoneAssignment, BaseGeometry, PreparedGeometry]


class _SearchTimeLimit(Exception):
    """Прерывает подготовку раскладок по общему времени запроса."""


@dataclass
class GeometricSearchResult:
    """Содержит только выбранную раскладку и суммарный объём работы поиска."""

    candidates: list[PatternCandidate]
    skipped: list[PatternSkippedCandidate]
    operations: list[PlanObjectAddOperation]
    search: CandidateSearchResult
    trials: int


def search_geometric_placement(
    project: Project,
    request: FillPatternRequest,
    generation_zones: list[PlantingZoneAssignment],
    evaluation: PlanEvaluation,
    effective_spacing_m: float,
    zone_targets: dict[str, int],
    operation_factory: OperationFactory,
    *,
    time_limit_s: float = SEARCH_TIME_LIMIT_S,
) -> GeometricSearchResult:
    """Ищет N проверенных позиций с общим временем для всех новых раскладок."""
    started = monotonic()
    config = PLACEMENT_CONFIG.geometry_search
    selected = set(request.zone_ids)
    compiled = compiled_planting_zones(project)
    radius = request.layout_radius_m or default_layout_radius(request.plant_kind)
    edge_offset = max(request.edge_offset_m, radius)
    payload = operation_factory([PatternCandidate(0, 0)], 0)[0].object
    work_zones = {zone.id: zone.geometry for zone in project.planting_zones}
    parts: list[BaseGeometry] = []
    for zone in generation_zones:
        check_operation_cancelled()
        # Адаптер уже вернул центры с учётом ограничений. Рабочий отступ
        # применяем к исходной границе, не сужая повторно новые отверстия.
        center_zone = zone.model_copy(
            update={
                "geometry": {
                    **zone.geometry,
                    "ga_work_zone": zone.geometry.get(
                        "ga_work_zone", work_zones[zone.id]
                    ),
                }
            }
        )
        usable = usable_zone_geometry(center_zone, edge_offset)
        if request.composition != "mixed":
            remaining = unoccupied_centers(project, payload, usable)
            if remaining is not None:
                usable = remaining
        for part in polygon_components(usable):
            if not _entirely_owned_elsewhere(part, radius, selected, compiled):
                parts.append(part)
    geometry = unary_union(parts)
    best = GeometricSearchResult(
        [], [], [], CandidateSearchResult([], "domain_exhausted", 0), 0
    )
    if geometry.is_empty:
        best.search.considered_count = 0
        best.search.elapsed_s = monotonic() - started
        return best

    def layout_checkpoint() -> None:
        check_operation_cancelled()
        if monotonic() - started >= time_limit_s:
            raise _SearchTimeLimit()

    starts = _trial_starts(
        geometry, effective_spacing_m, request.target_count, layout_checkpoint
    )
    single_position_region = _prepared_region_fits_one(geometry, effective_spacing_m)
    total_considered = 0
    total_full_checked = 0
    remaining_cells = config.maximum_cells
    trials = 0
    final_stop: SearchStopReason = "domain_exhausted"
    for _ in range(config.maximum_trials):
        check_operation_cancelled()
        try:
            initial_points, refine = next(starts)
        except StopIteration:
            break
        except _SearchTimeLimit:
            final_stop = "time_limit"
            break
        remaining_time = time_limit_s - (monotonic() - started)
        if remaining_time <= 0:
            final_stop = "time_limit"
            break
        if remaining_cells <= 0:
            final_stop = "candidate_limit"
            break
        source = GeometryCandidateSearch(
            geometry,
            effective_spacing_m,
            request.target_count,
            request.seed,
            resolution_m=config.resolution_m,
            max_cells=remaining_cells,
            max_frontier=config.maximum_frontier,
            time_limit_s=remaining_time,
            check_cancelled=check_operation_cancelled,
            initial_points=initial_points,
            refine=refine,
        )
        queue = _GeometryQueue(
            source,
            project,
            selected,
            compiled,
            radius,
            operation_factory,
            zone_targets,
            mixed=request.composition == "mixed",
        )
        result = search_candidates(
            project,
            queue.operations,
            evaluation,
            request.target_count,
            zone_targets,
            effective_spacing_m,
            time_limit_s=remaining_time,
            queue=queue,
            plant_kinds=frozenset({"tree", "shrub"})
            if request.composition == "mixed"
            else frozenset({request.plant_kind}),
            retain_rejected_traces=False,
        )
        remaining_cells -= int(source.metrics["cells_evaluated"])
        trials += 1
        total_considered += queue.considered_count
        total_full_checked += result.full_checked_count
        current = GeometricSearchResult(
            queue.candidates, queue.skipped, queue.operations, result, trials
        )
        if _accepted_count(current, project) > _accepted_count(best, project):
            best = current
        elif not best.trials:
            best = current
        if result.stop_reason == "target_reached":
            best = current
            final_stop = "target_reached"
            break
        # После одной допущенной посадки другая раскладка уже не улучшит число
        # в этой области центров. Это не граница вместимости всего исходного CAD.
        if single_position_region and _accepted_count(best, project) == 1:
            final_stop = "domain_exhausted"
            break
        if result.stop_reason == "time_limit":
            final_stop = "time_limit"
            break
        if result.stop_reason == "candidate_limit":
            final_stop = "candidate_limit"
        # Не удерживаем ещё одну невыбранную раскладку при создании следующей.
        # Лучшая хранится только в best; принятые позиции заново не публикуются.
        del current, result, queue, source
    best.trials = trials
    best.search.elapsed_s = monotonic() - started
    best.search.stop_reason = final_stop
    best.search.considered_count = total_considered
    best.search.full_checked_count = total_full_checked
    return best


def _trial_starts(
    geometry: BaseGeometry,
    spacing: float,
    target: int,
    checkpoint: Callable[[], None],
) -> Iterator[tuple[list[PatternCandidate], bool]]:
    """Выбирает начало по структуре области и добирает неполный вариант.

    Если фрагментов уже не меньше N, сначала проверяем их внутренние точки.
    Полные рассчитанные раскладки проверяем без адаптивного хвоста каждой.
    Неполную рассчитанную раскладку расширяем тем же потоком, сохраняя
    принятые при проверке центры, вместо перебора новых неполных начал.
    """
    config = PLACEMENT_CONFIG.geometry_search
    component_first = (
        len(geometry.geoms) if isinstance(geometry, MultiPolygon) else 1
    ) >= target
    if component_first:
        yield [], True
    layouts = propose_target_layouts(
        geometry,
        spacing,
        target,
        max_layouts=min(4, config.maximum_trials),
        max_segments=config.maximum_frontier,
        check_cancelled=checkpoint,
    )
    for points in layouts:
        if len(points) == target:
            yield points, False
    partial: list[PatternCandidate] = max(
        (points for points in layouts if len(points) < target), key=len, default=[]
    )
    if partial:
        yield partial, True
    if not component_first:
        yield [], True
    for point in _start_points(geometry, config.maximum_trials):
        yield [point], True


def _prepared_region_fits_one(geometry: BaseGeometry, spacing_m: float) -> bool:
    """Доказывает только невозможность пары в данном ограничивающем прямоугольнике."""
    if geometry.is_empty or not geometry.is_valid:
        return False
    minx, miny, maxx, maxy = geometry.bounds
    if not all(isfinite(value) for value in (minx, miny, maxx, maxy, spacing_m)):
        return False
    # Разности берём после точного перевода float в рациональные числа: раннее
    # округление ширины вниз могло бы создать ложную верхнюю границу.
    width = Fraction(maxx) - Fraction(minx)
    height = Fraction(maxy) - Fraction(miny)
    exact_below = width * width + height * height < Fraction(spacing_m) ** 2
    # Общий оценщик сравнивает float-гипотенузу с шагом. Например, диагональ
    # единичного квадрата равна float(sqrt(2)) и допускает пару на границе.
    # Точная рациональная граница не должна отменять это действующее равенство.
    scale = max(1.0, abs(minx), abs(miny), abs(maxx), abs(maxy), spacing_m)
    outward = nextafter(hypot(maxx - minx, maxy - miny) + 32 * ulp(scale), inf)
    return exact_below and outward < spacing_m


class _GeometryQueue:
    """Доставляет новые операции после обратной связи о принятых координатах."""

    def __init__(
        self,
        source: GeometryCandidateSearch,
        project: Project,
        selected: set[str],
        compiled: list[CompiledZone],
        radius: float,
        operation_factory: OperationFactory,
        zone_targets: dict[str, int],
        *,
        mixed: bool,
    ) -> None:
        self.source = source
        self.project = project
        self.selected = selected
        self.compiled = compiled
        self.radius = radius
        self.operation_factory = operation_factory
        self.zone_targets = zone_targets
        self.accepted: Counter[str | None] = Counter()
        self.operation_ordinal = 0
        self.retirement_radius = (
            max(radius, default_layout_radius("tree"), default_layout_radius("shrub"))
            if mixed
            else radius
        )
        self.operations: list[PlanObjectAddOperation] = []
        self.candidates: list[PatternCandidate] = []
        self.skipped: list[PatternSkippedCandidate] = []
        self.considered_count = 0
        self.ended = False

    @property
    def stop_reason(self) -> SearchStopReason:
        if self.source.stop_reason == "time_limit":
            return "time_limit"
        if self.source.stop_reason in {"cell_limit", "frontier_limit"}:
            return "candidate_limit"
        return "domain_exhausted"

    def __bool__(self) -> bool:
        return not self.ended

    def batch(self, limit: int) -> list[int]:
        proposals = self.source.next_batch(
            min(limit, PLACEMENT_CONFIG.geometry_search.batch_size)
        )
        self.considered_count += len(proposals)
        if not proposals:
            self.ended = True
            return []
        proposed_operations = self.operation_factory(proposals, self.operation_ordinal)
        self.operation_ordinal += len(proposals)
        begin = len(self.operations)
        for point, operation in zip(proposals, proposed_operations, strict=True):
            payload = operation.object
            radius = (
                payload.layout_radius_m
                or payload.radius
                or default_layout_radius(payload.kind)
            )
            zone = planting_zone_at(
                self.project, point.x, point.y, radius, self.compiled
            )
            if zone is not None and zone.id in self.selected:
                # Вид уже выбран, поэтому габарит и принадлежность совпадают
                # с общим оценщиком даже у смешанного состава во вложенных зонах.
                quota = self.zone_targets.get(zone.id)
                if quota is not None and self.accepted[zone.id] >= quota:
                    continue
                self.candidates.append(point)
                self.operations.append(operation)
            else:
                self.skipped.append(
                    PatternSkippedCandidate(
                        x=point.x,
                        y=point.y,
                        status="blocked",
                        code="OUTSIDE_SELECTED_ZONE",
                        category="constraint",
                        reason=message("pattern.outside_selected_zone"),
                        rule_id="selected-planting-zone",
                        suggested_action=message(
                            "pattern.outside_selected_zone_action"
                        ),
                        zone_id=zone.id if zone else None,
                    )
                )
        return list(range(begin, len(self.operations)))

    def accept(self, x: float, y: float, *, zone_id: str | None = None) -> int:
        self.source.accept(x, y)
        self.accepted[zone_id] += 1
        if (
            self.zone_targets
            and zone_id is not None
            and self.accepted[zone_id] == self.zone_targets.get(zone_id, 0)
        ):
            active = {
                key
                for key in self.selected
                if self.accepted[key] < self.zone_targets.get(key, 0)
            }
            self.source.discard_regions(
                lambda part: _entirely_owned_elsewhere(
                    part, self.retirement_radius, active, self.compiled
                )
            )
        return 0


def _accepted_count(result: GeometricSearchResult, project: Project) -> int:
    return sum(
        item.status == "allowed"
        or informational_source_warning(project, status=item.status, code=item.code)
        for item in result.search.candidate_results
    )


def _start_points(geometry: BaseGeometry, limit: int) -> list[PatternCandidate]:
    """Даёт геометрические альтернативы центральному первому размещению."""
    result: list[PatternCandidate] = []
    seen: set[tuple[float, float]] = set()
    if limit <= 0:
        return result
    for part in sorted(
        polygon_components(geometry), key=lambda item: item.area, reverse=True
    ):
        minx, miny, maxx, maxy = part.bounds
        for x, y in ((minx, miny), (maxx, maxy), (maxx, miny), (minx, maxy)):
            point = nearest_points(part, Point(x, y))[0]
            key = (point.x, point.y)
            if key not in seen:
                seen.add(key)
                result.append(PatternCandidate(*key))
                if len(result) >= limit:
                    return result
    return result


def _entirely_owned_elsewhere(
    part: BaseGeometry,
    radius: float,
    selected: set[str],
    compiled: list[CompiledZone],
) -> bool:
    """Удаляет фрагмент только при доказанной принадлежности другой задаче.

    Расширенный прямоугольник покрывает габарит любой точки фрагмента. Сырой
    вложенный полигон не вычитается: центр может быть внутри, а габарит снаружи.
    """
    minx, miny, maxx, maxy = part.bounds
    expanded = box(minx - radius, miny - radius, maxx + radius, maxy + radius)
    for zone, polygon, prepared in compiled:
        if zone.id in selected or not prepared.covers(expanded):
            continue
        if any(
            candidate.id in selected
            and candidate_geometry.area <= polygon.area
            and candidate_geometry.intersects(part)
            for candidate, candidate_geometry, _ in compiled
        ):
            continue
        return True
    return False
