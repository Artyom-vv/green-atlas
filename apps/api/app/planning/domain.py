from collections import defaultdict
from math import ceil, floor, hypot

from app.planning.contracts import PlanObject
from app.regulations.placement_config import PLACEMENT_CONFIG


class PlanVersionConflict(Exception):
    def __init__(self, expected_version: int, current_version: int) -> None:
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            f"План изменился: ожидалась версия {expected_version}, текущая версия {current_version}"
        )


def planning_radius(object_: PlanObject) -> float:
    """Largest known crown radius at the configured planning horizon."""

    forecast = next(
        (item for item in object_.canopy_forecast if item.horizon_year == PLACEMENT_CONFIG.layout.growth_horizon_year),
        None,
    )
    return max(object_.radius, forecast.radius_max_m if forecast else 0.0)


def required_spacing(first: PlanObject, second: PlanObject) -> float:
    """Minimum centre-to-centre distance based on the plants' growth envelopes."""
    first_radius = planning_radius(first)
    second_radius = planning_radius(second)
    same_group = bool(set(first.group_ids) & set(second.group_ids))
    policy = first.spacing_policy if same_group and first.spacing_policy == second.spacing_policy else "open"
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


def respects_plant_spacing(candidate: PlanObject, objects: list[PlanObject], ignore_id: str | None = None) -> bool:
    return all(
        existing.id == ignore_id
        or hypot(candidate.x - existing.x, candidate.y - existing.y) >= required_spacing(candidate, existing)
        for existing in objects
    )


class PlantSpacingIndex:
    """Uniform spatial index for validation without a quadratic pass."""

    def __init__(self, objects: list[PlanObject] | None = None, cell_size: float = 8.0) -> None:
        self.cell_size = cell_size
        self.cells: dict[tuple[int, int], list[PlanObject]] = defaultdict(list)
        self.max_radius = 0.0
        for object_ in objects or []:
            self.add(object_)

    def _key(self, object_: PlanObject) -> tuple[int, int]:
        return floor(object_.x / self.cell_size), floor(object_.y / self.cell_size)

    def nearby(self, candidate: PlanObject) -> list[PlanObject]:
        cell_x, cell_y = self._key(candidate)
        # Custom planting radii can be much larger than a default crown. A
        # fixed 3×3 window silently misses collisions across several 8 m
        # cells, so derive the search span from the widest possible rule.
        max_distance = PLACEMENT_CONFIG.layout.maximum_spacing(planning_radius(candidate) + self.max_radius)
        cell_radius = max(1, ceil(max_distance / self.cell_size))
        return [
            object_
            for offset_x in range(-cell_radius, cell_radius + 1)
            for offset_y in range(-cell_radius, cell_radius + 1)
            for object_ in self.cells.get((cell_x + offset_x, cell_y + offset_y), [])
        ]

    def respects(self, candidate: PlanObject, ignore_id: str | None = None) -> bool:
        return respects_plant_spacing(candidate, self.nearby(candidate), ignore_id=ignore_id)

    def add(self, object_: PlanObject) -> None:
        self.cells[self._key(object_)].append(object_)
        self.max_radius = max(self.max_radius, planning_radius(object_))
