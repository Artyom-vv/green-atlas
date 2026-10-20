"""Целевые раскладки N центров по точным сечениям полигональной области.

Главная ось каждого компонента задаётся его минимальным повёрнутым габаритом.
Число сечений зависит от N и минимального шага, а не от плотности полной сетки.
Интервалы и их одномерная вместимость позволяют сразу построить станции.
Неполная раскладка остаётся началом адаптивного поиска, не доказательством
невозможности разместить N. Нормативные отступы здесь повторно не применяются.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from itertools import pairwise
from math import ceil, floor, hypot, inf, isfinite, nextafter, ulp

from numpy import errstate
from shapely.affinity import affine_transform, translate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry
from shapely.prepared import PreparedGeometry, prep

from app.planning.ports import PatternCandidate


@dataclass(frozen=True)
class _Frame:
    part: Polygon
    origin: tuple[float, float]
    u: tuple[float, float]
    v: tuple[float, float]
    quota: int


@dataclass(frozen=True)
class _Row:
    frame: _Frame
    local: BaseGeometry
    y: float
    intervals: tuple[tuple[float, float], ...]
    capacity: int
    safe_step: float


class _Budget:
    def __init__(self, maximum: int, check: Callable[[], None]) -> None:
        self.remaining = maximum
        self.check = check

    def spend(self) -> bool:
        self.check()
        if not self.remaining:
            return False
        self.remaining -= 1
        return True


def propose_target_layouts(
    geometry: BaseGeometry,
    spacing_m: float,
    target_count: int,
    *,
    max_layouts: int,
    max_segments: int,
    check_cancelled: Callable[[], None],
) -> list[list[PatternCandidate]]:
    """Вернуть до четырёх раскладок, каждая содержит не более N точек.

    Общий бюджет расходуется на компоненты, сечения и полученные интервалы.
    Режимы меняют главную/поперечную ось и положение сечений. Они не перечисляют
    произвольные точки габарита. Все предложения покрываются исходной областью
    и разнесены минимум на spacing_m, но ещё требуют общего оценщика посадки.
    """
    if not isfinite(spacing_m) or spacing_m <= 0:
        raise ValueError("Минимальный шаг должен быть конечным и положительным")
    if type(target_count) is not int or not 1 <= target_count <= 5000:
        raise ValueError("Запрошенное количество должно быть целым от 1 до 5000")
    if any(type(v) is not int or v < 1 for v in (max_layouts, max_segments)):
        raise ValueError("Бюджеты раскладок и сечений должны быть положительными")
    check_cancelled()
    if not geometry.is_valid or (
        not geometry.is_empty and geometry.geom_type not in {"Polygon", "MultiPolygon"}
    ):
        raise ValueError("Область центров должна быть корректным полигоном")
    if geometry.is_empty:
        return []
    if not all(isfinite(v) for v in geometry.bounds):
        raise ValueError("Координаты области должны быть конечными")
    budget = _Budget(max_segments, check_cancelled)
    parts = []
    components: Iterator[Polygon]
    if isinstance(geometry, Polygon):
        components = iter((geometry,))
    else:
        assert isinstance(geometry, MultiPolygon)
        components = iter(geometry.geoms)
    for part in components:
        if not budget.spend():
            break
        parts.append(part)
    frames = []
    total_area = sum(p.area for p in parts)
    if not isfinite(total_area) or total_area <= 0:
        raise ValueError("Площадь компонентов превышает конечный диапазон раскладки")
    for part in sorted(parts, key=lambda p: (*p.bounds, p.area)):
        check_cancelled()
        quota = max(1, ceil(target_count * (part.area / total_area)))
        frames.append(_frame(part, quota))
    coverage = prep(geometry)
    result: list[list[PatternCandidate]] = []
    seen: set[tuple[tuple[float, float], ...]] = set()
    for phase, (across, boundary_phase) in enumerate(
        ((False, False), (False, True), (True, False), (True, True))
    ):
        check_cancelled()
        if len(result) >= max_layouts or not budget.remaining:
            break
        rows = []
        for frame in frames:
            rows.extend(
                _rows(frame, spacing_m, target_count, across, boundary_phase, budget)
            )
            if not budget.remaining:
                break
        counts = _allocate([row.capacity for row in rows], target_count)
        candidates = []
        stations = []
        buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
        ox, oy = geometry.bounds[:2]
        for row, count in zip(rows, counts, strict=True):
            for x in _stations(row, count, spacing_m):
                check_cancelled()
                frame = row.frame
                px = frame.origin[0] + x * frame.u[0] + row.y * frame.v[0]
                py = frame.origin[1] + x * frame.u[1] + row.y * frame.v[1]
                key_x, key_y = (px - ox) / spacing_m, (py - oy) / spacing_m
                if not all(isfinite(v) for v in (px, py, key_x, key_y)):
                    raise ValueError("Масштаб области превышает диапазон раскладки")
                key = floor(key_x), floor(key_y)
                if not coverage.covers(Point(px, py)):
                    continue
                if any(
                    hypot(px - x0, py - y0) < spacing_m
                    for dx in (-1, 0, 1)
                    for dy in (-1, 0, 1)
                    for x0, y0 in buckets.get((key[0] + dx, key[1] + dy), ())
                ):
                    continue
                candidates.append(PatternCandidate(px, py))
                stations.append((row, x))
                buckets.setdefault(key, []).append((px, py))
        if len(candidates) == target_count and target_count > 2:
            _irregularize(candidates, stations, coverage, spacing_m, phase, budget)
        identity = tuple((p.x, p.y) for p in candidates)
        if identity and identity not in seen:
            seen.add(identity)
            result.append(candidates)
    return result


def _frame(part: Polygon, quota: int) -> _Frame:
    # Прямоугольная оболочка уже является минимальным габаритом. Этот частый
    # случай не требует численно неустойчивых поворотов осевых рёбер GEOS.
    hull = part.convex_hull
    envelope = part.envelope
    if hull.equals(envelope):
        rectangle = envelope
    else:
        # Калиперы GEOS могут выставлять IEEE-флаги на осевых рёбрах. Габарит
        # задаёт только направление проб: его корректность проверяем ниже,
        # а каждый восстановленный центр отдельно проверяется исходным covers.
        with errstate(divide="ignore", invalid="ignore"):
            rectangle = part.minimum_rotated_rectangle
    if (
        not isinstance(rectangle, Polygon)
        or rectangle.is_empty
        or not rectangle.is_valid
    ):
        raise ValueError("Не удалось определить корректный габарит компонента")
    coordinates = list(rectangle.exterior.coords)
    edges = [(b[0] - a[0], b[1] - a[1]) for a, b in pairwise(coordinates)]
    dx, dy = max(edges, key=lambda v: hypot(*v))
    length = hypot(dx, dy)
    if not isfinite(length) or length <= 0:
        raise ValueError("Компонент не имеет конечной главной оси")
    u = dx / length, dy / length
    if u[0] < 0 or (u[0] == 0 and u[1] < 0):
        u = -u[0], -u[1]
    origin = rectangle.centroid
    return _Frame(part, (origin.x, origin.y), u, (-u[1], u[0]), quota)


def _rows(
    frame: _Frame,
    spacing: float,
    target: int,
    across: bool,
    boundary: bool,
    budget: _Budget,
) -> list[_Row]:
    if across:
        frame = _Frame(
            frame.part, frame.origin, frame.v, (-frame.u[0], -frame.u[1]), frame.quota
        )
    moved = translate(frame.part, -frame.origin[0], -frame.origin[1])
    local = affine_transform(moved, [*frame.u, *frame.v, 0, 0])
    x0, y0, x1, y1 = local.bounds
    length, width = x1 - x0, y1 - y0
    if not all(isfinite(v) for v in (length, width, length / spacing, width / spacing)):
        raise ValueError("Размеры компонента превышают диапазон раскладки")
    along_capacity = min(target, max(1, floor(length / spacing) + 1))
    row_count = min(
        target,
        max(1, ceil(frame.quota / along_capacity)),
        floor(width / spacing) + 1,
        budget.remaining,
    )
    margin = 32 * ulp(max(1.0, *map(abs, frame.origin), length, width))
    safe_step = nextafter(spacing + margin, inf)
    if row_count == 1:
        heights = [(y0 + y1) / 2 if not boundary else y0 + width / 4]
    else:
        slack = max(0.0, width - (row_count - 1) * spacing)
        if boundary:
            inset = min(spacing / 4, slack / 2)
            step = (width - 2 * inset) / (row_count - 1)
        else:
            step = max(spacing, width / row_count)
        if (row_count - 1) * max(step, safe_step) <= width:
            step = max(step, safe_step)
        first = (y0 + y1 - (row_count - 1) * step) / 2
        heights = [first + i * step for i in range(row_count)]
    result = []
    for y in heights:
        if not budget.spend():
            break
        intersection = local.intersection(LineString([(x0, y), (x1, y)]))
        intervals = []
        for segment in _lines(intersection):
            if not budget.spend():
                break
            lo, _, hi, _ = segment.bounds
            intervals.append((lo, hi))
        ordered = tuple(sorted(intervals))
        capacity = sum(count for _, count in _slots(ordered, spacing, target))
        if capacity:
            result.append(_Row(frame, local, y, ordered, capacity, safe_step))
    return result


def _irregularize(
    candidates: list[PatternCandidate],
    stations: list[tuple[_Row, float]],
    coverage: PreparedGeometry[BaseGeometry],
    spacing: float,
    phase: int,
    budget: _Budget,
) -> None:
    """Разнести готовые N станций поперёк, сохраняя все занятые ими места.

    Точное сечение даёт интервал перемещения внутри того же компонента.
    Двоичная последовательность внутренних долей не создаёт случайных проб.
    Каждый сдвиг проверяется также против ещё не сдвинутых станций; при отказе
    или исчерпании бюджета исходный допустимый центр остаётся на месте.
    """
    ox = min(point.x for point in candidates)
    oy = min(point.y for point in candidates)

    def key(point: PatternCandidate) -> tuple[int, int]:
        return floor((point.x - ox) / spacing), floor((point.y - oy) / spacing)

    occupied: dict[tuple[int, int], list[int]] = {}
    for index, point in enumerate(candidates):
        occupied.setdefault(key(point), []).append(index)
    for index, (row, x) in enumerate(stations):
        budget.check()
        _, low, _, high = row.local.bounds
        # В узком проходе пространственной свободы для такого рисунка нет.
        if high - low < 2 * spacing:
            continue
        if not budget.spend():
            return
        section = row.local.intersection(LineString([(x, low), (x, high)]))
        interval = None
        for segment in _lines(section):
            if not budget.spend():
                return
            _, lo, _, hi = segment.bounds
            if lo <= row.y <= hi and hi - lo >= 2 * spacing:
                interval = lo, hi
                break
        if interval is None:
            continue
        lo, hi = interval
        fraction = _dyadic_fraction(index + 1 + phase * len(candidates))
        # Внутренние 80% интервала не ставят центр на границу prepared buffer.
        y = lo + (0.1 + 0.8 * fraction) * (hi - lo)
        frame = row.frame
        point = PatternCandidate(
            frame.origin[0] + x * frame.u[0] + y * frame.v[0],
            frame.origin[1] + x * frame.u[1] + y * frame.v[1],
        )
        if not all(isfinite(value) for value in (point.x, point.y)):
            raise ValueError("Масштаб области превышает диапазон раскладки")
        if not coverage.covers(Point(point.x, point.y)):
            continue
        cell = key(point)
        if any(
            other != index
            and hypot(point.x - candidates[other].x, point.y - candidates[other].y)
            < spacing
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for other in occupied.get((cell[0] + dx, cell[1] + dy), ())
        ):
            continue
        occupied[key(candidates[index])].remove(index)
        candidates[index] = point
        occupied.setdefault(cell, []).append(index)


def _dyadic_fraction(ordinal: int) -> float:
    """Последовательно делить единичный интервал: 1/2, 1/4, 3/4, 1/8, ..."""
    fraction = 0.0
    weight = 0.5
    while ordinal:
        fraction += (ordinal & 1) * weight
        ordinal >>= 1
        weight /= 2
    return fraction


def _lines(geometry: BaseGeometry) -> Iterator[LineString]:
    if isinstance(geometry, LineString) and not geometry.is_empty:
        yield geometry
    elif isinstance(geometry, BaseMultipartGeometry):
        for part in geometry.geoms:
            yield from _lines(part)


def _slots(
    intervals: tuple[tuple[float, float], ...], spacing: float, limit: int
) -> Iterator[tuple[float, int]]:
    for _, _, first, count in _slot_groups(intervals, spacing, limit):
        yield first, count


def _slot_groups(
    intervals: tuple[tuple[float, float], ...], spacing: float, limit: int
) -> Iterator[tuple[float, float, float, int]]:
    last = -inf
    remaining = limit
    for lo, hi in intervals:
        first = max(lo, last + spacing)
        if first > hi:
            continue
        count = min(remaining, floor((hi - first) / spacing) + 1)
        if count:
            yield lo, hi, first, count
            last = first + (count - 1) * spacing
            remaining -= count
        if not remaining:
            return


def _allocate(capacities: list[int], target: int) -> list[int]:
    count = min(target, sum(capacities))
    if not count:
        return [0] * len(capacities)
    total = sum(capacities)
    quotas = [count * capacity // total for capacity in capacities]
    ranking = sorted(
        range(len(capacities)), key=lambda i: (-(count * capacities[i] % total), i)
    )
    for i in ranking[: count - sum(quotas)]:
        quotas[i] += 1
    return quotas


def _stations(row: _Row, count: int, spacing: float) -> Iterator[float]:
    if not count:
        return
    if len(row.intervals) == 1:
        lo, hi = row.intervals[0]
        step = row.safe_step if (count - 1) * row.safe_step <= hi - lo else spacing
        first = (lo + hi - (count - 1) * step) / 2
        yield from (first + i * step for i in range(count))
        return
    safe_capacity = sum(
        n for _, n in _slots(row.intervals, row.safe_step, row.capacity)
    )
    step = row.safe_step if safe_capacity >= count else spacing
    capacity = (
        min(row.capacity, safe_capacity) if step == row.safe_step else row.capacity
    )
    indices = [
        min(capacity - 1, floor((i + 0.5) * capacity / count)) for i in range(count)
    ]
    ordinal = 0
    wanted = 0
    assigned: list[tuple[float, float]] = []
    witness = []
    for lo, hi, first, n in _slot_groups(row.intervals, step, capacity):
        while wanted < len(indices) and indices[wanted] < ordinal + n:
            witness.append(first + (indices[wanted] - ordinal) * step)
            assigned.append((lo, hi))
            wanted += 1
        ordinal += n
    yield from _interior_stations(assigned, witness, step, spacing)


def _interior_stations(
    intervals: list[tuple[float, float]],
    witness: list[float],
    step: float,
    spacing: float,
) -> list[float]:
    """Распределить те же N станций внутри закреплённых интервалов.

    Прямой и обратный проходы дают две допустимые последовательности границ.
    Возрастающий вес между ними сохраняет минимальный шаг, в том числе через
    отверстия. Свободная длина расходуется на раздвижение и внутренние поля.
    При точном заполнении поля нулевые: новый обязательный отступ не вводится.
    """
    earliest = []
    previous = -inf
    for lo, hi in intervals:
        point = max(lo, previous + step)
        if point > hi:
            return witness
        earliest.append(point)
        previous = point
    latest = [0.0] * len(intervals)
    following = inf
    for i in range(len(intervals) - 1, -1, -1):
        lo, hi = intervals[i]
        point = min(hi, following - step)
        if point < lo or point < earliest[i]:
            return witness
        latest[i] = point
        following = point
    result = [
        lo + (i + 1) / (len(intervals) + 1) * (hi - lo)
        for i, (lo, hi) in enumerate(zip(earliest, latest, strict=True))
    ]
    if any(
        not isfinite(point) or not lo <= point <= hi
        for point, (lo, hi) in zip(result, intervals, strict=True)
    ) or any(b - a < spacing for a, b in pairwise(result)):
        # Числовая неустойчивость не уменьшает прежний набор свидетельств.
        return witness
    return result
