"""Поток геометрических проб из неизменяемой области допустимых центров.

Куча верхних оценок запаса направляет адаптивное деление прямоугольников.
Для точного signed distance оценка в ячейке равна f(центр) + полудиагональ:
функция запаса является 1-Lipschitz. GEOS возвращает числа с плавающей точкой,
поэтому отрицательная оценка сама по себе не доказывает пустоту ячейки.
Отсев требует непересечения с областью либо строгого диска-свидетеля.
Исчерпание конечного разрешения не означает максимальную вместимость.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from hashlib import blake2b
from heapq import heapify, heappop, heappush
from math import hypot, inf, isfinite, nextafter, ulp
from time import monotonic
from typing import Literal

from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep

from app.planning.ports import PatternCandidate

GeometryStopReason = Literal[
    "target_reached",
    "resolution_exhausted",
    "cell_limit",
    "frontier_limit",
    "time_limit",
]


@dataclass(frozen=True, slots=True)
class _Region:
    geometry: Polygon
    prepared: PreparedGeometry
    boundary: BaseGeometry


@dataclass(frozen=True, slots=True)
class _Cell:
    region: int
    bounds: tuple[float, float, float, float]
    x: float
    y: float
    half_diagonal: float
    score: float
    upper: float
    revision: int


class GeometryCandidateSearch:
    """Предлагает центры; допуск позиции остаётся у общего проверяющего модуля.

    Порция не резервирует соседство своих проб. Только accept добавляет диск
    запрета; отклонённую точку больше не повторяем, но её окрестность исследуем.
    initial_points позволяют внешнему сценарию повторить поиск с другим началом.
    refine=False проверяет только явные начальные позиции этого варианта,
    не тратя его время на адаптивное уточнение вместо остальных раскладок.
    Даже полное разрешение выбранной сетки не является доказательством упаковки.
    """

    def __init__(
        self,
        geometry: BaseGeometry,
        spacing_m: float,
        target_count: int,
        seed: int,
        *,
        resolution_m: float = 0.05,
        max_cells: int = 20_000,
        max_frontier: int = 4_096,
        time_limit_s: float = 5.0,
        check_cancelled: Callable[[], None] | None = None,
        initial_points: Sequence[PatternCandidate] = (),
        refine: bool = True,
    ) -> None:
        if not isfinite(spacing_m) or spacing_m <= 0:
            raise ValueError(
                "Минимальное расстояние должно быть конечным и положительным"
            )
        if not isfinite(resolution_m) or resolution_m <= 0:
            raise ValueError("Разрешение должно быть конечным и положительным")
        if not isfinite(time_limit_s) or time_limit_s < 0:
            raise ValueError("Время поиска должно быть конечным и неотрицательным")
        if any(
            type(v) is not int or v < 1 for v in (target_count, max_cells, max_frontier)
        ):
            raise ValueError("Цель и бюджеты поиска должны быть положительными целыми")
        if type(seed) is not int:
            raise ValueError("Начало воспроизводимого порядка должно быть целым")
        if type(refine) is not bool:
            raise ValueError("Режим уточнения должен быть логическим значением")
        if not geometry.is_valid or (
            not geometry.is_empty
            and geometry.geom_type not in {"Polygon", "MultiPolygon"}
        ):
            raise ValueError("Область центров должна быть корректным полигоном")
        self.geometry = geometry
        self.spacing_m = spacing_m
        self.resolution_m = resolution_m
        self.target_count = target_count
        self._seed = seed
        self._refine = refine
        self._max_cells = max_cells
        self._max_frontier = max_frontier
        self._time_limit = time_limit_s
        self._cancel = check_cancelled or (lambda: None)
        self._started = monotonic()
        self._prepared = prep(geometry)
        self._regions: list[_Region] = []
        self._disabled_regions: set[int] = set()
        self._heap: list[tuple[float, int, int, _Cell]] = []
        self._pending: deque[PatternCandidate] = deque()
        self._accepted: list[tuple[float, float]] = []
        self._seen: set[tuple[float, float]] = set()
        self._serial = 0
        self._stop: GeometryStopReason | None = None
        self._work = {
            "cells_evaluated": 0,
            "cells_visited": 0,
            "cells_revalidated": 0,
            "pruned_outside_cells": 0,
            "pruned_spacing_cells": 0,
            "resolution_leaves": 0,
            "generated_candidates": 0,
            "frontier_peak": 0,
            "discarded_components": 0,
            "discarded_cells": 0,
            "discarded_pending_anchors": 0,
        }
        if len(initial_points) > max_frontier:
            self._stop = "frontier_limit"
            return
        self._pending.extend(initial_points)
        part_count = (
            len(geometry.geoms)
            if isinstance(geometry, MultiPolygon)
            else int(not geometry.is_empty)
        )
        if part_count + len(self._pending) > max_frontier:
            self._stop = "frontier_limit"
            return
        parts = list(self._components(geometry))
        for part in sorted(parts, key=lambda p: (*p.bounds, p.area)):
            self._cancel()
            if not all(isfinite(v) for v in part.bounds):
                raise ValueError("Координаты области должны быть конечными")
            region = len(self._regions)
            self._regions.append(_Region(part, prep(part), part.boundary))
            if not self._refine:
                continue
            point = part.representative_point()
            self._pending.append(PatternCandidate(point.x, point.y))
            cell = self._cell(region, part.bounds)
            if cell is not None:
                self._push(cell)
            if self._stop:
                break
        if not self._regions:
            self._stop = "resolution_exhausted"

    @property
    def stop_reason(self) -> GeometryStopReason | None:
        return self._stop

    @property
    def accepted_count(self) -> int:
        return len(self._accepted)

    @property
    def metrics(self) -> dict[str, int | float]:
        """Снимок ограниченной работы; счётчики не являются оценкой вместимости."""
        return {
            **self._work,
            "accepted_count": self.accepted_count,
            "frontier_cells": len(self._heap),
            "pending_anchors": len(self._pending),
            "components": len(self._regions),
            "active_components": len(self._regions) - len(self._disabled_regions),
            "refinement_enabled": int(self._refine),
            "resolution_m": self.resolution_m,
            "elapsed_s": monotonic() - self._started,
        }

    def next_batch(self, limit: int) -> list[PatternCandidate]:
        if type(limit) is not int or limit < 1:
            raise ValueError("Размер порции должен быть положительным целым")
        result: list[PatternCandidate] = []
        while len(result) < limit and not self._stop:
            self._cancel()
            if monotonic() - self._started >= self._time_limit:
                self._stop = "time_limit"
                break
            if self._pending:
                self._offer(self._pending.popleft(), result)
                continue
            if not self._heap:
                self._stop = "resolution_exhausted"
                break
            _, _, _, cell = heappop(self._heap)
            if cell.region in self._disabled_regions:
                continue
            if cell.revision != self.accepted_count:
                current = self._cell(cell.region, cell.bounds)
                self._work["cells_revalidated"] += 1
                if current is not None:
                    self._push(current)
                continue
            self._work["cells_visited"] += 1
            region = self._regions[cell.region]
            rectangle = box(*cell.bounds)
            if not region.prepared.intersects(rectangle):
                self._work["pruned_outside_cells"] += 1
                continue
            if cell.upper < 0 and self._inside_accepted_disk(cell):
                self._work["pruned_spacing_cells"] += 1
                continue
            candidate = PatternCandidate(cell.x, cell.y)
            self._offer(candidate, result)
            if cell.half_diagonal <= self.resolution_m:
                self._work["resolution_leaves"] += 1
                # Центр ячейки может лежать вне тонкой полосы или в отверстии.
                # Представитель пересечения не даёт потерять такой фрагмент.
                if not region.prepared.covers(Point(cell.x, cell.y)):
                    fragment = region.geometry.intersection(rectangle)
                    point = fragment.representative_point()
                    if not point.is_empty:
                        # Если порция уже заполнена центром другого компонента,
                        # свидетель тонкого фрагмента остаётся для следующей.
                        self._pending.appendleft(PatternCandidate(point.x, point.y))
            else:
                self._split(cell)
        return result

    def accept(self, x: float, y: float) -> None:
        self._cancel()
        if not self._feasible(x, y):
            raise ValueError("Принятый центр нарушает область или расстояние")
        if self.accepted_count >= self.target_count:
            raise ValueError("Запрошенное количество уже принято")
        self._accepted.append((x, y))
        if self.accepted_count >= self.target_count:
            self._stop = "target_reached"

    def discard_regions(self, predicate: Callable[[Polygon], bool]) -> int:
        """Отключить целые компоненты по внешнему доказательству и вернуть число.

        Вызывающий слой доказывает ненужность компонента, например после
        достижения квоты его участка. Это не оценка глобальной вместимости.
        Принятые центры продолжают ограничивать расстояния во всех компонентах.
        Порции и куча фильтруются один раз, без перестройки исходной геометрии.
        Отмена до фиксации фильтра не меняет прежний набор активных компонентов.
        """
        if not self._discard_checkpoint():
            return 0
        newly_disabled: set[int] = set()
        for index, region in enumerate(self._regions):
            if index in self._disabled_regions:
                continue
            if not self._discard_checkpoint():
                return 0
            if predicate(region.geometry):
                newly_disabled.add(index)
        if not newly_disabled:
            return 0
        disabled = self._disabled_regions | newly_disabled
        remaining_heap = []
        for entry in self._heap:
            if not self._discard_checkpoint():
                return 0
            if entry[3].region not in disabled:
                remaining_heap.append(entry)
        remaining_pending: deque[PatternCandidate] = deque()
        for candidate in self._pending:
            if not self._discard_checkpoint():
                return 0
            # Начальная раскладка не привязана к одному компоненту. Даже общий
            # граничный центр сохраняем, если его покрывает активный компонент.
            if self._covered_by_active(Point(candidate.x, candidate.y), disabled):
                remaining_pending.append(candidate)
        if not self._discard_checkpoint():
            return 0
        heapify(remaining_heap)
        self._work["discarded_components"] += len(newly_disabled)
        self._work["discarded_cells"] += len(self._heap) - len(remaining_heap)
        self._work["discarded_pending_anchors"] += len(self._pending) - len(
            remaining_pending
        )
        self._disabled_regions = disabled
        self._heap = remaining_heap
        self._pending = remaining_pending
        if self._stop is None and not self._heap and not self._pending:
            self._stop = "resolution_exhausted"
        return len(newly_disabled)

    def _discard_checkpoint(self) -> bool:
        self._cancel()
        if self._stop is None and monotonic() - self._started >= self._time_limit:
            self._stop = "time_limit"
            return False
        return True

    def _covered_by_active(self, point: Point, disabled: set[int]) -> bool:
        return any(
            index not in disabled and region.prepared.covers(point)
            for index, region in enumerate(self._regions)
        )

    @staticmethod
    def _components(geometry: BaseGeometry) -> Iterator[Polygon]:
        if isinstance(geometry, Polygon) and not geometry.is_empty:
            yield geometry
        elif isinstance(geometry, MultiPolygon):
            yield from geometry.geoms

    def _feasible(self, x: float, y: float) -> bool:
        if not (isfinite(x) and isfinite(y)):
            return False
        point = Point(x, y)
        covered = (
            self._covered_by_active(point, self._disabled_regions)
            if self._disabled_regions
            else self._prepared.covers(point)
        )
        return covered and all(
            hypot(x - a, y - b) >= self.spacing_m for a, b in self._accepted
        )

    def _offer(
        self, candidate: PatternCandidate, result: list[PatternCandidate]
    ) -> None:
        key = candidate.x, candidate.y
        if key not in self._seen and self._feasible(*key):
            self._seen.add(key)
            result.append(candidate)
            self._work["generated_candidates"] += 1

    def _cell(
        self, region: int, bounds: tuple[float, float, float, float]
    ) -> _Cell | None:
        if region in self._disabled_regions:
            return None
        if self._work["cells_evaluated"] >= self._max_cells:
            self._stop = "cell_limit"
            return None
        self._work["cells_evaluated"] += 1
        x0, y0, x1, y1 = bounds
        dx, dy = x1 - x0, y1 - y0
        x, y = x0 + dx / 2, y0 + dy / 2
        half = hypot(dx / 2, dy / 2)
        if not all(isfinite(v) for v in (x, y, half)):
            raise ValueError("Масштаб координат превышает конечный диапазон поиска")
        part = self._regions[region]
        point = Point(x, y)
        distance = point.distance(part.boundary)
        score = distance if part.prepared.covers(point) else -distance
        # Расстояния до принятых центров дополняют, а не заменяют границу области.
        for a, b in self._accepted:
            score = min(score, hypot(x - a, y - b) - self.spacing_m)
        margin = 32 * ulp(max(1.0, abs(x), abs(y), half, abs(score)))
        upper = nextafter(score + half + margin, inf)
        return _Cell(region, bounds, x, y, half, score, upper, self.accepted_count)

    def _push(self, cell: _Cell) -> None:
        if cell.region in self._disabled_regions:
            return
        if len(self._heap) >= self._max_frontier:
            self._stop = "frontier_limit"
            return
        self._serial += 1
        # Seed меняет только воспроизводимый порядок равных оценок.
        identity = ":".join(
            [str(self._seed), str(cell.region), *(v.hex() for v in cell.bounds)]
        )
        tie = int.from_bytes(blake2b(identity.encode(), digest_size=8).digest(), "big")
        heappush(self._heap, (-cell.upper, tie, self._serial, cell))
        self._work["frontier_peak"] = max(self._work["frontier_peak"], len(self._heap))

    def _inside_accepted_disk(self, cell: _Cell) -> bool:
        margin = 32 * ulp(
            max(1.0, abs(cell.x), abs(cell.y), self.spacing_m, cell.half_diagonal)
        )
        return any(
            nextafter(hypot(cell.x - x, cell.y - y) + cell.half_diagonal + margin, inf)
            < self.spacing_m
            for x, y in self._accepted
        )

    def _split(self, cell: _Cell) -> None:
        x0, y0, x1, y1 = cell.bounds
        if x1 - x0 >= y1 - y0:
            if not x0 < cell.x < x1:
                self._work["resolution_leaves"] += 1
                return
            bounds = ((x0, y0, cell.x, y1), (cell.x, y0, x1, y1))
        else:
            if not y0 < cell.y < y1:
                self._work["resolution_leaves"] += 1
                return
            bounds = ((x0, y0, x1, cell.y), (x0, cell.y, x1, y1))
        for child_bounds in bounds:
            child = self._cell(cell.region, child_bounds)
            if child is not None:
                self._push(child)
            if self._stop:
                break
