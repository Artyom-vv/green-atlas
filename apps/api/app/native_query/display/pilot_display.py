"""Разделение сохранённого CAD-отображения и рабочих областей native-пилота без расчёта
или изменения посадок.
"""

from app.planting_zones.contracts import PlantingZoneAssignment
from app.planting_zones.domain import (
    attach_planting_zone_features,
    validate_planting_zones,
)
from app.projects.contracts import Project


def validate_pilot_working_zones(
    project: Project, zones: list[PlantingZoneAssignment]
) -> None:
    """Проверять только корректность AOI; каждую внешнюю посадку отклоняет
    native-проверка.

    Старые CAD-полигоны карты не допускают native-пилот и не подтверждают включение
    всей ручной области в территорию.
    """
    validate_planting_zones(
        project, zones, changed_only=True, check_source_boundary=False
    )


def synchronize_pilot_display(project: Project) -> bool:
    """Сохранять CAD-контекст и только текущие рабочие области этого проекта.

    Посадки, зоны и CAD-геометрия не рассчитываются и не меняются. Производные
    planting_area/allowed/forbidden чужого снимка не выдаются за исходные объекты.
    """
    if project.geometry is None:
        return False
    before = project.geometry.feature_collection.get("features", [])
    project.geometry.feature_collection["features"] = [
        feature
        for feature in before
        if feature.get("properties", {}).get("kind") not in {"allowed", "forbidden"}
    ]
    attach_planting_zone_features(project)
    return before != project.geometry.feature_collection["features"]
