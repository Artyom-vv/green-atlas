"""Консервативное доказательство неизменности проверок рабочих зон.

Покрытие не заменяет проверку препятствий: при любом сомнении вызывается полный
проверяющий модуль. Старые замечания о выходе за зону не удаляются отдельно.
"""

from shapely.errors import GEOSException
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR
from app.planning.contracts import Plan
from app.projects.contracts import Project
from app.species.forecast import calculation_growth_radii, forecast_at


def _selected_area(project: Project) -> BaseGeometry | None:
    geometries = [shape(zone.geometry) for zone in project.planting_zones]
    if any(
        not geometry.is_valid
        or geometry.is_empty
        or geometry.geom_type not in {"Polygon", "MultiPolygon"}
        for geometry in geometries
    ):
        raise ValueError("Неоднозначная геометрия прежней рабочей зоны")
    return unary_union(geometries) if geometries else None


def unchanged_zone_checks(previous: Project, current: Project, plan: Plan) -> bool:
    """Прежний и новый выбор полностью вмещают текущие и прогнозные габариты."""
    old_zones = {zone.id: zone.geometry for zone in previous.planting_zones}
    new_zones = {zone.id: zone.geometry for zone in current.planting_zones}
    if (
        len(old_zones) == len(previous.planting_zones)
        and len(new_zones) == len(current.planting_zones)
        and old_zones == new_zones
    ):
        return True
    try:
        old_area = _selected_area(previous)
        new_area = _selected_area(current)
        if old_area is not None and new_area is not None and old_area.equals(new_area):
            return True
        for object_ in plan.objects:
            radius = object_.radius
            canopy = forecast_at(object_.canopy_forecast, DEFAULT_PLANNING_HORIZON_YEAR)
            roots = forecast_at(object_.root_forecast, DEFAULT_PLANNING_HORIZON_YEAR)
            if canopy and roots:
                radius = max(radius, *calculation_growth_radii(canopy, roots))
            footprint = Point(object_.x, object_.y).buffer(radius)
            if any(
                area is not None and not area.covers(footprint)
                for area in (old_area, new_area)
            ):
                return False
    except (GEOSException, ValueError, TypeError, KeyError):
        return False
    return True
