"""Расстояния между посадками по габаритам и прогнозу роста с индексом соседей и
конфликтом версии плана.
"""

from collections import defaultdict
from math import ceil, floor, hypot

from app.planning.contracts import PlanObject
from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR
from app.regulations.placement_config import PLACEMENT_CONFIG
from app.species.forecast import forecast_at


class PlanVersionConflict(Exception):
    def __init__(self, expected_version: int, current_version: int) -> None:
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            f"План изменился: ожидалась версия {expected_version}, текущая версия {current_version}"
        )


def planning_radius(
    object_: PlanObject, planning_horizon_year: int = DEFAULT_PLANNING_HORIZON_YEAR
) -> float:
    """Возвращает наибольший известный радиус кроны на заданном горизонте планирования."""

    forecast = forecast_at(object_.canopy_forecast, planning_horizon_year)
    return max(object_.radius, forecast.radius_max_m if forecast else 0.0)


def required_spacing(
    first: PlanObject,
    second: PlanObject,
    planning_horizon_year: int = DEFAULT_PLANNING_HORIZON_YEAR,
) -> float:
    """Определяет минимальное расстояние между центрами по прогнозным габаритам
    растений.
    """
    first_radius = planning_radius(first, planning_horizon_year)
    second_radius = planning_radius(second, planning_horizon_year)
    same_group = bool(set(first.group_ids) & set(second.group_ids))
    policy = (
        first.spacing_policy
        if same_group and first.spacing_policy == second.spacing_policy
        else "open"
    )
    layout = PLACEMENT_CONFIG.layout
    if first.kind == "tree" and second.kind == "tree":
        if policy == "canopy":
            return layout.tree_canopy.distance(first_radius + second_radius)
        if policy == "balanced":
            return layout.tree_balanced.distance(first_radius + second_radius)
        return layout.tree_open.distance(first_radius + second_radius)
    if first.kind == "shrub" and second.kind == "shrub":
        return layout.shrub.distance(first_radius + second_radius)
    return layout.mixed.distance(first_radius + second_radius)


def respects_plant_spacing(
    candidate: PlanObject,
    objects: list[PlanObject],
    ignore_id: str | None = None,
    *,
    planning_horizon_year: int = DEFAULT_PLANNING_HORIZON_YEAR,
) -> bool:
    return all(
        existing.id == ignore_id
        or hypot(candidate.x - existing.x, candidate.y - existing.y)
        >= required_spacing(candidate, existing, planning_horizon_year)
        for existing in objects
    )


class PlantSpacingIndex:
    """Индекс соседей по общему горизонту плана без квадратичного прохода."""

    def __init__(
        self,
        objects: list[PlanObject] | None = None,
        cell_size: float = 8.0,
        *,
        planning_horizon_year: int = DEFAULT_PLANNING_HORIZON_YEAR,
    ) -> None:
        self.cell_size = cell_size
        self.planning_horizon_year = planning_horizon_year
        self.cells: dict[tuple[int, int], list[PlanObject]] = defaultdict(list)
        self.max_radius = 0.0
        for object_ in objects or []:
            self.add(object_)

    def _key(self, object_: PlanObject) -> tuple[int, int]:
        return floor(object_.x / self.cell_size), floor(object_.y / self.cell_size)

    def nearby(self, candidate: PlanObject) -> list[PlanObject]:
        cell_x, cell_y = self._key(candidate)
        # Пользовательский радиус может значительно превосходить обычную крону.
        # Фиксированное окно 3×3 пропускало столкновения через несколько ячеек 8 м;
        # размер поиска определяется наибольшим возможным правилом.
        max_distance = PLACEMENT_CONFIG.layout.maximum_spacing(
            planning_radius(candidate, self.planning_horizon_year) + self.max_radius
        )
        cell_radius = max(1, ceil(max_distance / self.cell_size))
        return [
            object_
            for offset_x in range(-cell_radius, cell_radius + 1)
            for offset_y in range(-cell_radius, cell_radius + 1)
            for object_ in self.cells.get((cell_x + offset_x, cell_y + offset_y), [])
        ]

    def respects(self, candidate: PlanObject, ignore_id: str | None = None) -> bool:
        return respects_plant_spacing(
            candidate,
            self.nearby(candidate),
            ignore_id=ignore_id,
            planning_horizon_year=self.planning_horizon_year,
        )

    def add(self, object_: PlanObject) -> None:
        self.cells[self._key(object_)].append(object_)
        self.max_radius = max(
            self.max_radius, planning_radius(object_, self.planning_horizon_year)
        )
