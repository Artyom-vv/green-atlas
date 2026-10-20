"""Область проб за вычетом доказанного соседства сохранённых посадок.

Вписанные многоугольники уменьшают только заведомо запрещённые промежутки.
Оставшаяся область не допускает посадки: каждую пробу проверяет общий оценщик.
"""

from math import hypot, inf, isfinite, nextafter, ulp

from shapely import get_num_coordinates
from shapely.errors import ShapelyError
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.operations.progress import check_operation_cancelled
from app.planning.contracts import PlanObjectCreate
from app.planning.domain import required_spacing
from app.planning.rules import default_layout_radius
from app.planning.search.spacing_precheck import spacing_template
from app.projects.contracts import Project

MAX_OCCUPANCY_POLYGONS = 512
MAX_OCCUPANCY_COORDINATES = 100_000
OCCUPANCY_BATCH_SIZE = 32


def unoccupied_centers(
    project: Project, payload: PlanObjectCreate, usable: BaseGeometry
) -> BaseGeometry | None:
    """Возвращает надмножество свободных центров или сохраняет прежнюю область.

    Лимит может оставить часть запрещённых окружностей необработанной. Это
    ослабляет отсев, но не теряет допустимые позиции и не меняет точные правила.
    """
    check_operation_cancelled()
    if project.plan is None or not project.plan.objects or usable.is_empty:
        return None
    if (
        not usable.is_valid
        or usable.geom_type not in {"Polygon", "MultiPolygon"}
        or get_num_coordinates(usable) > MAX_OCCUPANCY_COORDINATES
    ):
        return None
    bounds = usable.bounds
    if not all(isfinite(value) for value in bounds):
        return None
    radius = (
        payload.layout_radius_m or payload.radius or default_layout_radius(payload.kind)
    )
    template = spacing_template(payload, radius)
    if template is None:
        return None
    minx, miny, maxx, maxy = bounds
    batches: list[BaseGeometry] = []
    pending: list[BaseGeometry] = []
    processed = 0
    try:
        for ordinal, saved in enumerate(project.plan.objects):
            if ordinal % OCCUPANCY_BATCH_SIZE == 0:
                check_operation_cancelled()
            distance = required_spacing(
                template, saved, project.plan.planning_horizon_year
            )
            if not isfinite(distance) or distance <= 0:
                continue
            dx = max(minx - saved.x, 0.0, saved.x - maxx)
            dy = max(miny - saved.y, 0.0, saved.y - maxy)
            if hypot(dx, dy) >= distance:
                continue
            scale = max(1.0, abs(saved.x), abs(saved.y), distance)
            margin = max(1e-9, 32 * ulp(scale))
            inner_radius = nextafter(distance, -inf) - margin
            if inner_radius <= 0:
                continue
            polygon = Point(saved.x, saved.y).buffer(inner_radius, quad_segs=32)
            # Выпуклый многоугольник с такими вершинами целиком внутри открытого
            # диска запрета; допустимое равенство расстояния остаётся снаружи.
            if (
                polygon.is_empty
                or not polygon.is_valid
                or any(
                    hypot(x - saved.x, y - saved.y) >= distance
                    for x, y in polygon.exterior.coords
                )
            ):
                continue
            pending.append(polygon)
            processed += 1
            if len(pending) == OCCUPANCY_BATCH_SIZE:
                batches.append(unary_union(pending))
                pending = []
                check_operation_cancelled()
            if processed >= MAX_OCCUPANCY_POLYGONS:
                break
        if pending:
            batches.append(unary_union(pending))
        if not batches:
            return None
        check_operation_cancelled()
        occupied = unary_union(batches)
        check_operation_cancelled()
        result = usable.difference(occupied)
        check_operation_cancelled()
        if (
            not result.is_valid
            or result.geom_type not in {"Polygon", "MultiPolygon"}
            or get_num_coordinates(result) > MAX_OCCUPANCY_COORDINATES
        ):
            return None
        return result
    except ShapelyError:
        # Ошибка производной оптимизации возвращает прежнюю область, без
        # изменения авторитетного снимка и без ослабления итогового оценщика.
        return None
