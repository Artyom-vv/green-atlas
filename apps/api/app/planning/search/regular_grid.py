"""Неизменяемая повёрнутая сетка регулярного заполнения для подсчёта и повторного поиска
позиций.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import deque
from collections.abc import Iterator
from math import (
    ceil,
    cos,
    floor,
    hypot,
    inf,
    isfinite,
    nextafter,
    radians,
    sin,
    sqrt,
    ulp,
)
from typing import Literal

from shapely.affinity import rotate, translate
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep

from app.operations.progress import check_operation_cancelled
from app.planning.config import MAX_PATTERN_CANDIDATES
from app.planning.ports import PatternCandidate

GRID_BOUNDARY_EPSILON = 1e-9
GRID_COORDINATE_DECIMALS = 6
COUNT_GRID_STEP_VARIANTS = 9
COUNT_GRID_COMPONENT_LIMIT = 512
COUNT_GRID_COMPONENT_PHASES = 4
COUNT_GRID_MAX_PROBES = 100_000
COUNT_GRID_VARIANT_PROBES = 20_000


class RegularFillGrid:
    def __init__(
        self,
        geometry: BaseGeometry,
        angle_deg: float,
        layout: Literal["regular", "staggered"],
    ) -> None:
        self.geometry = geometry
        self.angle_deg = angle_deg
        self.staggered = layout == "staggered"
        self.rotated = rotate(geometry, -angle_deg, origin=(0, 0), use_radians=False)
        self.bounds = self.rotated.bounds
        self.coverage = prep(self.rotated)

    def _keys(self, spacing: float) -> Iterator[tuple[float, float]]:
        min_x, min_y, max_x, max_y = self.bounds
        first_x = floor(min_x / spacing) * spacing
        first_y = floor(min_y / spacing) * spacing
        row = 0
        row_step = spacing * sqrt(3) / 2 if self.staggered else spacing
        seen: set[tuple[float, float]] = set()
        y = first_y
        # Сохраняем накопительное вычисление, общую фазу, чётность рядов и порядок.
        # Пропуск компонентов и другая стратегия поиска количества сюда не входят.
        while y <= max_y + GRID_BOUNDARY_EPSILON:
            x_offset = spacing / 2 if self.staggered and row % 2 else 0
            x = first_x + x_offset
            while x <= max_x + GRID_BOUNDARY_EPSILON:
                point = Point(x, y)
                if self.coverage.covers(point):
                    restored = rotate(
                        point, self.angle_deg, origin=(0, 0), use_radians=False
                    )
                    key = (
                        round(restored.x, GRID_COORDINATE_DECIMALS),
                        round(restored.y, GRID_COORDINATE_DECIMALS),
                    )
                    if key not in seen:
                        seen.add(key)
                        yield key
                x += spacing
            y += row_step
            row += 1

    def count(self, spacing: float, limit: int) -> int:
        count = 0
        for _ in self._keys(spacing):
            count += 1
            if count >= limit:
                break
        return count

    def candidates(self, spacing: float) -> list[PatternCandidate]:
        return [PatternCandidate(*key) for key in self._keys(spacing)]

    def count_candidates(
        self, minimum_spacing: float, limit: int
    ) -> list[PatternCandidate]:
        """Выбирает одну решётку без предположения монотонной вместимости.

        limit ограничивает резерв предложений, но не является условием успеха:
        лучший неполный вариант сохраняется и при недостижимом резерве. Фазы
        привязаны к геометрии, а не к мировому нулю. Посадки разных решёток не
        смешиваются. Общий бюджет проб ограничивает также очень разреженные
        или огромные области; его исчерпание не доказывает вместимость.
        """
        if not isfinite(minimum_spacing) or minimum_spacing <= 0:
            raise ValueError("Минимальный шаг должен быть конечным и положительным")
        if type(limit) is not int or not 1 <= limit <= MAX_PATTERN_CANDIDATES:
            raise ValueError("Резерв позиций должен быть целым от 1 до 5000")
        if self.geometry.is_empty:
            return []
        minx, miny, maxx, maxy = self.geometry.bounds
        origin = minx / 2 + maxx / 2, miny / 2 + maxy / 2
        local = rotate(
            translate(self.geometry, -origin[0], -origin[1]),
            -self.angle_deg,
            origin=(0, 0),
        )
        if not all(isfinite(value) for value in (*local.bounds, local.area)):
            raise ValueError("Размеры области превышают диапазон решётки")
        parts: list[BaseGeometry] = []
        components = local.geoms if hasattr(local, "geoms") else (local,)
        for index, part in enumerate(components):
            check_operation_cancelled()
            if index >= COUNT_GRID_COMPONENT_LIMIT:
                break
            if not part.is_empty:
                parts.append(part)
        parts.sort(key=lambda part: (-part.area, *part.bounds))
        if not parts:
            return []
        left, bottom, right, top = local.bounds
        center = left / 2 + right / 2, bottom / 2 + top / 2
        original_minimum = minimum_spacing
        # Каждая координата округляется до 6 знаков. Две точки могут
        # приблизиться на sqrt(2) * 10^-6 м, дополнительно учитываем ulp
        # исходных координат. Отступы области не ослабляются: округлённые
        # узлы по-прежнему должны покрываться исходным полигоном.
        scale = max(1.0, *map(abs, self.geometry.bounds))
        minimum_spacing = nextafter(
            minimum_spacing
            + sqrt(2) * 10 ** (-GRID_COORDINATE_DECIMALS)
            + 32 * ulp(scale),
            inf,
        )
        high = max(minimum_spacing * 2, sqrt(max(local.area, 1)) * 2)
        steps = [minimum_spacing]
        steps.extend(
            minimum_spacing
            * (high / minimum_spacing) ** (index / (COUNT_GRID_STEP_VARIANTS - 1))
            for index in range(1, COUNT_GRID_STEP_VARIANTS)
        )
        # При точном заполнении границ запас округления может отнять узел.
        # Исходный шаг пробуем отдельно; каждый округлённый центр всё равно
        # проходит строгую проверку расстояния до уже выбранных узлов.
        steps.append(original_minimum)
        angle = radians(self.angle_deg)
        cosine, sine = cos(angle), sin(angle)
        coverage = prep(self.geometry)
        best: list[PatternCandidate] = []
        remaining = COUNT_GRID_MAX_PROBES
        for spacing in steps:
            check_operation_cancelled()
            phases = [
                (center[0] + dx * spacing, center[1] + dy * spacing)
                for dx, dy in ((0, 0), (0.5, 0), (0, 0.5), (0.5, 0.5))
            ]
            phases.extend(
                (point.x, point.y)
                for part in parts[:COUNT_GRID_COMPONENT_PHASES]
                for point in (part.representative_point(),)
            )
            for anchor in dict.fromkeys(phases):
                if remaining <= 0:
                    return best
                candidates, spent = self._count_variant(
                    parts,
                    spacing,
                    anchor,
                    origin,
                    cosine,
                    sine,
                    coverage,
                    limit,
                    min(remaining, COUNT_GRID_VARIANT_PROBES),
                    original_minimum,
                )
                remaining -= spent
                if len(candidates) > len(best):
                    best = candidates
                if len(best) >= limit:
                    return best
        return best

    def _count_variant(
        self,
        parts: list[BaseGeometry],
        spacing: float,
        anchor: tuple[float, float],
        origin: tuple[float, float],
        cosine: float,
        sine: float,
        coverage: PreparedGeometry[BaseGeometry],
        limit: int,
        probe_limit: int,
        minimum_spacing: float,
    ) -> tuple[list[PatternCandidate], int]:
        """Обходит ограниченную выборку одной решётки по окнам компонентов."""
        row_step = spacing * sqrt(3) / 2 if self.staggered else spacing
        windows: list[tuple[int, int, int, int]] = []
        ends: list[int] = []
        total = 0
        for part in parts:
            check_operation_cancelled()
            minx, miny, maxx, maxy = part.bounds
            first_row = ceil((miny - anchor[1]) / row_step)
            last_row = floor((maxy - anchor[1]) / row_step)
            # Общий диапазон колонок включает обе чётности шахматного ряда.
            first_col = ceil(
                (minx - anchor[0]) / spacing - (0.5 if self.staggered else 0)
            )
            last_col = floor((maxx - anchor[0]) / spacing)
            width = last_col - first_col + 1
            if last_row < first_row or width <= 0:
                continue
            total += (last_row - first_row + 1) * width
            windows.append((first_row, first_col, width, total))
            ends.append(total)
        result: list[PatternCandidate] = []
        seen: set[tuple[float, float]] = set()
        occupied: dict[tuple[int, int], list[tuple[float, float]]] = {}
        spent = 0

        def offer(x: float, y: float) -> None:
            nonlocal spent
            if spent >= probe_limit:
                return
            if spent % 64 == 0:
                check_operation_cancelled()
            spent += 1
            key = (
                round(origin[0] + x * cosine - y * sine, GRID_COORDINATE_DECIMALS),
                round(origin[1] + x * sine + y * cosine, GRID_COORDINATE_DECIMALS),
            )
            if key in seen or not coverage.covers(Point(*key)):
                return
            seen.add(key)
            cell = floor(key[0] / minimum_spacing), floor(key[1] / minimum_spacing)
            if any(
                hypot(key[0] - other[0], key[1] - other[1]) < minimum_spacing
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                for other in occupied.get((cell[0] + dx, cell[1] + dy), ())
            ):
                return
            occupied.setdefault(cell, []).append(key)
            result.append(PatternCandidate(*key))

        # Начало выбранной фазы само является узлом этой же решётки.
        offer(*anchor)
        if len(result) >= limit:
            return result, spent
        probes = min(total, probe_limit - spent)
        for ordinal in _spread_ordinals(total, probes):
            window_index = bisect_right(ends, ordinal)
            first_row, first_col, width, _ = windows[window_index]
            offset = ordinal - (ends[window_index - 1] if window_index else 0)
            row, column = first_row + offset // width, first_col + offset % width
            offer(
                anchor[0]
                + (column + (0.5 if self.staggered and row % 2 else 0)) * spacing,
                anchor[1] + row * row_step,
            )
            if len(result) >= limit:
                break
        result.sort(key=lambda point: (point.y, point.x))
        return result, spent


def _spread_ordinals(total: int, limit: int) -> Iterator[int]:
    """Покрывает всё окно до уточнения; ранний резерв не становится префиксом ряда."""
    if not total or not limit:
        return
    yield 0
    if total == 1 or limit == 1:
        return
    yield total - 1
    pending = deque([(1, total - 2)])
    emitted = 2
    while pending and emitted < limit:
        low, high = pending.popleft()
        if low > high:
            continue
        middle = (low + high) // 2
        yield middle
        emitted += 1
        if low < middle:
            pending.append((low, middle - 1))
        if middle < high:
            pending.append((middle + 1, high))
