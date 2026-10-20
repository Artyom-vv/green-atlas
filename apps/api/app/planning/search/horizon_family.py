"""Детерминированная семья раскладок по одному неизменяемому набору точек.

valid_through подтверждает статический допуск точки во всех годах 0..year.
Здесь проверяются только расстояния между новыми посадками. Исходные растения,
CAD и итоговая публикация остаются ответственностью общего проверяющего модуля.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from math import floor, hypot, isfinite
from time import monotonic

from app.operations.progress import check_operation_cancelled
from app.planning.config import MAX_PATTERN_CANDIDATES, MAX_PLANNING_HORIZON_YEAR
from app.planning.contracts import PlanObject
from app.planning.domain import required_spacing

MAX_FAMILY_CANDIDATES = 20_000
YEAR_COUNT = MAX_PLANNING_HORIZON_YEAR + 1
SpacingRule = Callable[[PlanObject, PlanObject, int], float]


@dataclass(frozen=True, slots=True)
class HorizonCandidate:
    x: float
    y: float
    valid_through: int
    zone_id: str | None
    template_index: int = 0


@dataclass(frozen=True, slots=True)
class HorizonFamilyStats:
    candidate_checks: int
    pair_checks: int
    completed_stages: int
    last_year: int | None
    orders_started: int
    work_limit_reached: bool
    elapsed_s: float

    @property
    def work_units(self) -> int:
        return self.candidate_checks + self.pair_checks


@dataclass(frozen=True, slots=True)
class HorizonFamilyResult:
    selected_indices: tuple[int, ...]
    stats: HorizonFamilyStats


class HorizonFamilyTimeout(TimeoutError):
    """Wall-clock deadline не публикует неполный результат семьи."""


class _WorkLimit(Exception):
    pass


class _Budget:
    def __init__(
        self,
        maximum_checks: int,
        check_cancelled: Callable[[], None],
        time_limit_s: float | None,
        clock: Callable[[], float],
    ) -> None:
        self.maximum_checks = maximum_checks
        self.check_cancelled = check_cancelled
        self.time_limit_s = time_limit_s
        self.clock = clock
        self.started = clock()
        self.candidate_checks = self.pair_checks = 0

    def checkpoint(self) -> None:
        self.check_cancelled()
        if (
            self.time_limit_s is not None
            and self.clock() - self.started >= self.time_limit_s
        ):
            raise HorizonFamilyTimeout("Расчёт семьи горизонтов не завершён вовремя")

    def consume(self, *, pair: bool = False) -> None:
        if (self.candidate_checks + self.pair_checks) % 64 == 0:
            self.checkpoint()
        if self.candidate_checks + self.pair_checks >= self.maximum_checks:
            raise _WorkLimit()
        if pair:
            self.pair_checks += 1
        else:
            self.candidate_checks += 1


def _spacing_matrices(
    templates: Sequence[PlanObject],
    minimum_spacing_by_year: Sequence[float],
    spacing_rule: SpacingRule,
    budget: _Budget,
) -> list[list[list[float]]]:
    matrices: list[list[list[float]]] = []
    for year, minimum in enumerate(minimum_spacing_by_year):
        budget.checkpoint()
        if not isfinite(minimum) or minimum < 0:
            raise ValueError(
                "Минимальные расстояния должны быть конечными и неотрицательными"
            )
        matrix = []
        for first in templates:
            row = []
            for second in templates:
                distance = spacing_rule(first, second, year)
                reverse = spacing_rule(second, first, year)
                if not all(isfinite(v) and v >= 0 for v in (distance, reverse)):
                    raise ValueError("Правило расстояния вернуло недопустимое значение")
                row.append(max(minimum, distance, reverse))
            matrix.append(row)
        if matrices and any(
            matrix[i][j] < matrices[-1][i][j]
            for i in range(len(templates))
            for j in range(len(templates))
        ):
            raise ValueError("Расстояния семьи должны не убывать с ростом горизонта")
        matrices.append(matrix)
    return matrices


class _Packing:
    def __init__(
        self,
        points: Sequence[HorizonCandidate],
        matrix: list[list[float]],
        zone_targets: Mapping[str, int],
        initial: tuple[int, ...],
        origin: tuple[float, float],
        budget: _Budget,
    ) -> None:
        self.points = points
        self.matrix = matrix
        self.zone_targets = zone_targets
        self.cell_size = max(1.0, *(distance for row in matrix for distance in row))
        self.origin = origin
        self.buckets: dict[tuple[int, int], list[int]] = {}
        self.selected: list[int] = []
        self.selected_set: set[int] = set()
        self.by_zone: Counter[str | None] = Counter()
        self.budget = budget
        for ordinal, index in enumerate(initial):
            if ordinal % 64 == 0:
                budget.checkpoint()
            self._add(index)

    def _key(self, point: HorizonCandidate) -> tuple[int, int]:
        return (
            floor((point.x - self.origin[0]) / self.cell_size),
            floor((point.y - self.origin[1]) / self.cell_size),
        )

    def _add(self, index: int) -> None:
        point = self.points[index]
        self.selected.append(index)
        self.selected_set.add(index)
        self.by_zone[point.zone_id] += 1
        self.buckets.setdefault(self._key(point), []).append(index)

    def try_add(self, index: int, year: int) -> None:
        point = self.points[index]
        if index in self.selected_set or point.valid_through < year:
            return
        if self.zone_targets:
            quota = (
                self.zone_targets.get(point.zone_id, 0)
                if point.zone_id is not None
                else 0
            )
            if self.by_zone[point.zone_id] >= quota:
                return
        self.budget.consume()
        cell = self._key(point)
        # cell_size покрывает максимальный шаг любых двух шаблонов на этом
        # году. Поэтому все запрещённые соседи находятся в этих девяти ячейках.
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other_index in self.buckets.get((cell[0] + dx, cell[1] + dy), ()):
                    self.budget.consume(pair=True)
                    other = self.points[other_index]
                    required = self.matrix[point.template_index][other.template_index]
                    if hypot(point.x - other.x, point.y - other.y) < required:
                        return
        self._add(index)


def select_horizon_family(
    candidates: Sequence[HorizonCandidate],
    templates: Sequence[PlanObject],
    requested_year: int,
    minimum_spacing_by_year: Sequence[float],
    target_count: int = MAX_PATTERN_CANDIDATES,
    zone_targets: Mapping[str, int] | None = None,
    *,
    maximum_checks: int = 100_000,
    check_cancelled: Callable[[], None] = check_operation_cancelled,
    time_limit_s: float | None = None,
    clock: Callable[[], float] = monotonic,
    spacing_rule: SpacingRule = required_spacing,
) -> HorizonFamilyResult:
    """Число выбранных точек не уменьшается при меньшем requested_year.

    Это свойство относится к одинаковым candidates/templates/параметрам и
    maximum_checks. Каждый запрос воспроизводит один prefix от 40 лет вниз.
    Work budget считает предложения и сравнения пар совместно; при исчерпании
    возвращается лучший допущенный incumbent. Timeout и отмена прерываются
    исключением и не возвращают финальный частичный результат.
    """
    if type(requested_year) is not int or not 0 <= requested_year < YEAR_COUNT:
        raise ValueError("Горизонт должен быть целым от 0 до 40")
    if not 1 <= len(templates) <= 2:
        raise ValueError("Семья поддерживает один или два шаблона растения")
    if len(candidates) > MAX_FAMILY_CANDIDATES:
        raise ValueError("Превышен предел 20000 точек семьи")
    if len(minimum_spacing_by_year) != YEAR_COUNT:
        raise ValueError("Нужны расстояния для всех 41 целых годов")
    if type(target_count) is not int or not 1 <= target_count <= MAX_PATTERN_CANDIDATES:
        raise ValueError("Целевое количество должно быть целым от 1 до 5000")
    if type(maximum_checks) is not int or maximum_checks < 0:
        raise ValueError("Бюджет работы должен быть целым и неотрицательным")
    if time_limit_s is not None and (not isfinite(time_limit_s) or time_limit_s < 0):
        raise ValueError("Ограничение времени должно быть конечным и неотрицательным")
    quotas = dict(zone_targets or {})
    if any(type(value) is not int or value < 0 for value in quotas.values()):
        raise ValueError("Квоты участков должны быть целыми и неотрицательными")
    budget = _Budget(maximum_checks, check_cancelled, time_limit_s, clock)
    for ordinal, point in enumerate(candidates):
        if ordinal % 64 == 0:
            budget.checkpoint()
        if not all(isfinite(value) for value in (point.x, point.y)):
            raise ValueError("Координаты точек должны быть конечными")
        if (
            type(point.valid_through) is not int
            or not -1 <= point.valid_through < YEAR_COUNT
        ):
            raise ValueError(
                "Подтверждённый горизонт точки должен быть целым от -1 до 40"
            )
        if type(point.template_index) is not int or not 0 <= point.template_index < len(
            templates
        ):
            raise ValueError("Индекс шаблона точки вне диапазона")
    if candidates and not all(
        isfinite(
            max(getattr(p, axis) for p in candidates)
            - min(getattr(p, axis) for p in candidates)
        )
        for axis in ("x", "y")
    ):
        raise ValueError("Диапазон координат превышает конечный масштаб")
    matrices = _spacing_matrices(
        templates, minimum_spacing_by_year, spacing_rule, budget
    )
    origin = (
        min((point.x for point in candidates), default=0),
        min((point.y for point in candidates), default=0),
    )
    latest_valid_year = max((point.valid_through for point in candidates), default=-1)
    orders = (
        tuple(range(len(candidates))),
        tuple(reversed(range(len(candidates)))),
        tuple(
            sorted(
                range(len(candidates)),
                key=lambda i: (candidates[i].x, candidates[i].y, i),
            )
        ),
        tuple(
            sorted(
                range(len(candidates)),
                key=lambda i: (candidates[i].y, candidates[i].x, i),
            )
        ),
    )
    incumbent: tuple[int, ...] = ()
    completed_stages = orders_started = 0
    last_year: int | None = None
    work_limit_reached = False
    for year in range(MAX_PLANNING_HORIZON_YEAR, requested_year - 1, -1):
        budget.checkpoint()
        last_year = year
        if year > latest_valid_year:
            completed_stages += 1
            continue
        best = list(incumbent)
        for initial, order in (
            (incumbent, orders[0]),
            *(((), order) for order in orders),
        ):
            budget.checkpoint()
            packing = _Packing(
                candidates, matrices[year], quotas, initial, origin, budget
            )
            orders_started += 1
            try:
                for ordinal, index in enumerate(order):
                    if ordinal % 64 == 0:
                        budget.checkpoint()
                    packing.try_add(index, year)
                    if len(packing.selected) >= target_count:
                        break
            except _WorkLimit:
                work_limit_reached = True
            if len(packing.selected) > len(best):
                best = packing.selected
            if work_limit_reached or len(best) >= target_count:
                break
        incumbent = tuple(best)
        if work_limit_reached:
            break
        completed_stages += 1
        if len(incumbent) >= target_count:
            break
    budget.checkpoint()
    return HorizonFamilyResult(
        incumbent,
        HorizonFamilyStats(
            candidate_checks=budget.candidate_checks,
            pair_checks=budget.pair_checks,
            completed_stages=completed_stages,
            last_year=last_year,
            orders_started=orders_started,
            work_limit_reached=work_limit_reached,
            elapsed_s=clock() - budget.started,
        ),
    )
