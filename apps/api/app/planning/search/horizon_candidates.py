"""Конечный воспроизводимый запас центров, общий для всех горизонтов.

Отступы уже включены во входную область. Предложения не занимают соседство,
не доказывают вместимость и не заменяют обычную проверку посадок.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from itertools import accumulate
from math import ceil, isfinite
from random import Random
from typing import cast

from numpy import errstate
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points
from shapely.prepared import prep
from shapely.strtree import STRtree

from app.operations.progress import check_operation_cancelled
from app.planning.config import MAX_PATTERN_CANDIDATES
from app.planning.ports import PatternCandidate
from app.planning.search.geometric_layouts import propose_target_layouts

_MINIMUM_POOL = 128
_DENSITY_MULTIPLIER = 12
_ATTEMPTS_PER_POSITION = 24
_MAXIMUM_SEGMENTS = 4096


class HorizonCandidateLimit(ValueError):
    """The complete component coverage needs more than the allowed proposals."""


class _Checkpoint:
    def __init__(self) -> None:
        self.work = 0
        check_operation_cancelled()

    def __call__(self) -> None:
        self.work += 1
        if self.work % 64 == 0:
            check_operation_cancelled()


class _Pool:
    def __init__(self, geometry: BaseGeometry, parts: list[Polygon], limit: int):
        self.coverage = prep(geometry)
        self.parts = parts
        self.index = STRtree(parts)
        self.limit = limit
        self.covered: set[int] = set()
        self.seen: set[tuple[float, float]] = set()
        self.points: list[PatternCandidate] = []

    def offer(self, x: float, y: float, *, exact: bool = False) -> bool:
        coordinate = (x, y) if exact else (round(x, 6), round(y, 6))
        if coordinate in self.seen or len(self.points) >= self.limit:
            return False
        point = Point(*coordinate)
        if not self.coverage.covers(point):
            return False
        owners = {
            int(index) for index in self.index.query(point, predicate="covered_by")
        }
        newly_covered = owners - self.covered
        missing = len(self.parts) - len(self.covered) - len(newly_covered)
        # Каждому ещё не представленному островку оставляем отдельное место.
        if len(self.points) + 1 + missing > self.limit:
            return False
        self.seen.add(coordinate)
        self.covered.update(owners)
        self.points.append(PatternCandidate(*coordinate))
        return True


@dataclass(frozen=True)
class _Window:
    origin: tuple[float, float]
    across: tuple[float, float]
    along: tuple[float, float]
    area: float

    def draw(self, random: Random) -> tuple[float, float]:
        u, v = random.random(), random.random()
        return (
            self.origin[0] + u * self.across[0] + v * self.along[0],
            self.origin[1] + u * self.across[1] + v * self.along[1],
        )


def _window(part: Polygon) -> _Window:
    with errstate(divide="ignore", invalid="ignore"):
        rectangle = part.minimum_rotated_rectangle
    if not isinstance(rectangle, Polygon) or not rectangle.area > 0:
        raise ValueError("Не удалось определить конечное окно компонента")
    corners = list(rectangle.exterior.coords)[:-1]
    first = min(range(len(corners)), key=corners.__getitem__)
    origin = corners[first]
    neighbors = sorted((corners[(first - 1) % 4], corners[(first + 1) % 4]))
    across, along = (
        (point[0] - origin[0], point[1] - origin[1]) for point in neighbors
    )
    if not all(isfinite(value) for value in (*origin, *across, *along, rectangle.area)):
        raise ValueError("Масштаб компонента превышает конечный диапазон выборки")
    return _Window((origin[0], origin[1]), across, along, rectangle.area)


def _component_anchor(part: Polygon, pool: _Pool) -> None:
    point = part.representative_point()
    # Округление не должно выносить единственную пробу за тонкий компонент.
    x, y = round(point.x, 6), round(point.y, 6)
    for dx, dy in (
        (0, 0),
        (-1, 0),
        (1, 0),
        (0, -1),
        (0, 1),
        (-1, -1),
        (-1, 1),
        (1, -1),
        (1, 1),
    ):
        candidate = Point(round(x + dx * 1e-6, 6), round(y + dy * 1e-6, 6))
        if part.covers(candidate) and pool.offer(candidate.x, candidate.y):
            return
    # Меньший микрометра компонент может вообще не содержать десятичного узла.
    # Полная точность свидетеля сохраняет островок и всё ещё проверяется covers.
    if pool.offer(point.x, point.y, exact=True):
        return
    raise HorizonCandidateLimit(
        "Бюджет кандидатов недостаточен для всех компонентов области"
    )


def generate_horizon_candidates(
    geometry: BaseGeometry,
    minimum_spacing0: float,
    maximum_spacing40: float,
    target_count: int | None,
    seed: int,
    *,
    max_candidates: int = MAX_PATTERN_CANDIDATES,
) -> list[PatternCandidate]:
    """Построить один и тот же запас для любого запрошенного года.

    Конечный бюджет зависит от исходной области и габаритов 0/40 лет. Здесь
    нет отсечения по реальному времени: отмена прерывает всю операцию, а не
    публикует разное число проб на разных компьютерах.
    """
    if (
        any(
            isinstance(value, bool) or not isfinite(value) or value <= 0
            for value in (minimum_spacing0, maximum_spacing40)
        )
        or maximum_spacing40 < minimum_spacing0
    ):
        raise ValueError(
            "Граничные шаги должны быть конечными, положительными и упорядоченными"
        )
    if type(seed) is not int:
        raise ValueError("Начало воспроизводимого порядка должно быть целым")
    if (
        type(max_candidates) is not int
        or not 1 <= max_candidates <= MAX_PATTERN_CANDIDATES
    ):
        raise ValueError("Бюджет кандидатов должен быть целым от 1 до 5000")
    if target_count is not None and (
        type(target_count) is not int or not 1 <= target_count <= MAX_PATTERN_CANDIDATES
    ):
        raise ValueError("Запрошенное количество должно быть целым от 1 до 5000")
    checkpoint = _Checkpoint()
    if not geometry.is_valid or (
        not geometry.is_empty and geometry.geom_type not in {"Polygon", "MultiPolygon"}
    ):
        raise ValueError("Область центров должна быть корректным полигоном")
    if geometry.is_empty:
        return []
    if (
        not all(isfinite(value) for value in (*geometry.bounds, geometry.area))
        or geometry.area <= 0
    ):
        raise ValueError("Координаты и площадь области должны быть конечными")
    parts = sorted(
        list(geometry.geoms)
        if isinstance(geometry, MultiPolygon)
        else [cast(Polygon, geometry)],
        key=lambda part: (*part.bounds, part.area),
    )
    if len(parts) > max_candidates:
        raise HorizonCandidateLimit(
            "Бюджет кандидатов недостаточен для всех компонентов области"
        )
    density = geometry.area / minimum_spacing0 / minimum_spacing0 * _DENSITY_MULTIPLIER
    limit = min(
        max_candidates,
        max(
            _MINIMUM_POOL,
            len(parts),
            ceil(density) if isfinite(density) else max_candidates,
        ),
    )
    if target_count is not None:
        # Малый запрос на большой территории не требует полного запаса 5000
        # проб. Лимит зависит от N, но остаётся одинаковым для всех годов.
        limit = min(limit, max(_MINIMUM_POOL, target_count * 16, len(parts)))
    pool = _Pool(geometry, parts, limit)
    if target_count is not None:
        # Годы запроса не участвуют даже в выборе целевых раскладок.
        for spacing in dict.fromkeys((maximum_spacing40, minimum_spacing0)):
            checkpoint()
            layouts = propose_target_layouts(
                geometry,
                spacing,
                target_count,
                max_layouts=4,
                max_segments=min(_MAXIMUM_SEGMENTS, max(_MINIMUM_POOL, limit * 4)),
                check_cancelled=checkpoint,
            )
            for layout in layouts:
                for point in layout:
                    checkpoint()
                    pool.offer(point.x, point.y)
    for index, part in enumerate(parts):
        checkpoint()
        if index not in pool.covered:
            _component_anchor(part, pool)
    # Края и узкие окончания представлены без перебора вершин CAD-полигона.
    for part in parts:
        low_x, low_y, high_x, high_y = part.bounds
        for x, y in (
            (low_x, (low_y + high_y) / 2),
            (high_x, (low_y + high_y) / 2),
            ((low_x + high_x) / 2, low_y),
            ((low_x + high_x) / 2, high_y),
        ):
            checkpoint()
            boundary_point = nearest_points(part, Point(x, y))[0]
            pool.offer(boundary_point.x, boundary_point.y)
    if len(pool.points) < limit:
        windows = []
        for part in parts:
            checkpoint()
            windows.append(_window(part))
        weights = list(accumulate(window.area for window in windows))
        if not isfinite(weights[-1]):
            raise ValueError("Площадь окон превышает конечный диапазон выборки")
        random = Random(f"horizon-candidates:{seed}")
        for _ in range(limit * _ATTEMPTS_PER_POSITION):
            checkpoint()
            index = min(
                bisect_right(weights, random.random() * weights[-1]), len(windows) - 1
            )
            pool.offer(*windows[index].draw(random))
            if len(pool.points) >= limit:
                break
    check_operation_cancelled()
    return pool.points
