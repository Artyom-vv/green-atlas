"""Детерминированная выборка кандидатов, включая окна разреженных областей и ограничение
размера предложения.
"""

from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import accumulate
from math import cos, floor, hypot, pi, sin
from random import Random

from shapely import prepare
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry

from app.planning.config import MAX_PATTERN_CANDIDATES
from app.planning.ports import PatternCandidate
from app.shared.failures import DomainFailure, FailureReason

POISSON_MIN_ATTEMPTS = 4_000
POISSON_ATTEMPTS_PER_TARGET = 120
POISSON_MIN_STAGNANT_ATTEMPTS = 2_000
POISSON_STAGNANT_ATTEMPTS_PER_ACCEPTED = 20


@dataclass(frozen=True)
class SamplingWindow:
    geometry: Polygon
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


def polygon_components(geometry: BaseGeometry) -> Iterator[Polygon]:
    if isinstance(geometry, Polygon) and not geometry.is_empty:
        yield geometry
    elif isinstance(geometry, BaseMultipartGeometry):
        for part in geometry.geoms:
            yield from polygon_components(part)


class SparseAreaSampler:
    def __init__(self, windows: list[SamplingWindow]) -> None:
        self.windows = windows
        self._weights = list(accumulate(window.area for window in windows))

    def draw(self, random: Random) -> tuple[BaseGeometry, float, float]:
        if len(self.windows) == 1:
            window = self.windows[0]
        else:
            index = bisect_right(self._weights, random.random() * self._weights[-1])
            window = self.windows[min(index, len(self.windows) - 1)]
        x, y = window.draw(random)
        # Проверяем этот компонент, а не общую область: перекрытие прямоугольников
        # выборки не повышает вероятность принятия в пересечении.
        return window.geometry, x, y


def sparse_area_sampler(
    geometry: BaseGeometry,
    target: int,
    attempt_budget: int,
) -> SparseAreaSampler | None:
    """Меняет способ выборки, если ожидаемых попаданий в геометрию недостаточно для
    цели.

    Вероятность попадания при равномерной выборке в габарите равна area / bbox_area.
    Если ожидаемых геометрических попаданий недостаточно ещё до проверки расстояний,
    бюджет попыток расходуется преимущественно на пустое пространство. Для компактных
    областей сохраняются исходная последовательность координат и смысл seed.
    """
    bbox_area = geometry.envelope.area
    if bbox_area <= 0 or geometry.area * attempt_budget >= target * bbox_area:
        return None
    windows = []
    for polygon in polygon_components(geometry):
        rectangle = polygon.minimum_rotated_rectangle
        if not isinstance(rectangle, Polygon) or rectangle.area <= 0:
            continue
        coordinates = list(rectangle.exterior.coords)
        origin = (coordinates[0][0], coordinates[0][1])
        windows.append(
            SamplingWindow(
                geometry=polygon,
                origin=origin,
                across=(coordinates[1][0] - origin[0], coordinates[1][1] - origin[1]),
                along=(coordinates[3][0] - origin[0], coordinates[3][1] - origin[1]),
                area=rectangle.area,
            )
        )
    # Выбор прямоугольника по площади с допуском только его полигона даёт
    # постоянную плотность на полезной площади. Отверстия и проверка расстояний
    # отклоняют кандидатов независимо.
    return SparseAreaSampler(windows) if windows else None


def poisson_candidates(
    geometry: BaseGeometry,
    spacing: float,
    target: int,
    seed: int,
    *,
    alternatives: bool = False,
) -> list[PatternCandidate]:
    """Строит воспроизводимую последовательную случайную выборку Пуассона.

    Кандидаты покрывают всю область без обхода неявной решётки. Пространственный хеш
    ограничивает поиск соседей при проверке минимального расстояния. Одинаковые
    геометрия и seed дают одинаковый результат без повторяющихся рядов X/Y, похожих
    на инженерную сетку.
    """
    if geometry.is_empty:
        return []
    min_x, min_y, max_x, max_y = geometry.bounds
    width, height = max_x - min_x, max_y - min_y
    if width <= 0 or height <= 0:
        return []
    cell = max(spacing, 1e-6)
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    accepted: list[PatternCandidate] = []
    if alternatives:
        # Каждый отдельный фрагмент получает пробу, даже если слишком мал для
        # случайного выбора по площади. Проба не резервирует посадку: её принимает оценщик.
        for part in sorted(
            polygon_components(geometry), key=lambda g: g.area, reverse=True
        ):
            point = part.representative_point()
            accepted.append(PatternCandidate(point.x, point.y))
            if len(accepted) >= target:
                return accepted
    random = Random(f"poisson:{seed}")
    max_attempts = max(POISSON_MIN_ATTEMPTS, target * POISSON_ATTEMPTS_PER_TARGET)
    sampler = sparse_area_sampler(geometry, target, max_attempts)
    # GEOS индексирует повторные проверки покрытия без изменения координат,
    # случайной выборки, включения границы и проверки расстояний.
    prepare(
        geometry if sampler is None else [window.geometry for window in sampler.windows]
    )
    attempts_without_acceptance = 0
    for _ in range(max_attempts):
        if sampler is None:
            component = geometry
            x = min_x + random.random() * width
            y = min_y + random.random() * height
        else:
            component, x, y = sampler.draw(random)
        point = Point(x, y)
        if not component.covers(point):
            attempts_without_acceptance += 1
            continue
        bucket = (floor(x / cell), floor(y / cell))
        too_close = False
        for offset_x in (-1, 0, 1):
            for offset_y in (-1, 0, 1):
                for other_x, other_y in buckets.get(
                    (bucket[0] + offset_x, bucket[1] + offset_y), []
                ):
                    if hypot(x - other_x, y - other_y) + 1e-9 < spacing:
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                break
        if too_close:
            attempts_without_acceptance += 1
            if accepted and attempts_without_acceptance >= max(
                POISSON_MIN_STAGNANT_ATTEMPTS,
                len(accepted) * POISSON_STAGNANT_ATTEMPTS_PER_ACCEPTED,
            ):
                break
            continue
        # Альтернативные пробы не занимают место. Позицию резервирует приложение
        # после успешной проверки геометрии, роста и расстояний.
        if not alternatives:
            buckets.setdefault(bucket, []).append((x, y))
        accepted.append(PatternCandidate(round(x, 6), round(y, 6)))
        attempts_without_acceptance = 0
        if len(accepted) >= target:
            break
    return accepted


def bounded_candidates(candidates: list[PatternCandidate]) -> list[PatternCandidate]:
    """Ограничивает число предложений до обычной проверки безопасности."""
    if len(candidates) > MAX_PATTERN_CANDIDATES:
        raise DomainFailure(
            FailureReason.PATTERN_CANDIDATE_LIMIT,
            {"max_candidates": MAX_PATTERN_CANDIDATES},
        )
    return candidates


def cluster_offsets(
    spacing: float, count: int, angle: float
) -> list[tuple[float, float]]:
    """Возвращает компактную шестиугольную группу с воспроизводимым поворотом."""
    offsets = [(0.0, 0.0)]
    ring = 1
    while len(offsets) < count:
        radius = ring * spacing
        slots = 6 * ring
        for index in range(slots):
            theta = angle + (2 * pi * index / slots)
            offsets.append((radius * cos(theta), radius * sin(theta)))
            if len(offsets) >= count:
                break
        ring += 1
    return offsets
