"""Проверка рабочих зон и их отображение в снимке; занятые зоны и недопустимые
пересечения остаются явными отказами.
"""

from __future__ import annotations

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.planting_zones.config import MAX_PARTIAL_OVERLAP_M2, MIN_ZONE_AREA_M2
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


def validate_planting_zones(
    project: Project,
    zones: list[PlantingZoneAssignment],
    *,
    changed_only: bool = False,
    check_source_boundary: bool = True,
) -> None:
    if project.geometry is None:
        raise DomainFailure(FailureReason.MANUAL_MAP_REQUIRED)
    if not zones and not (changed_only and project.planting_zones):
        raise DomainFailure(FailureReason.ZONES_REQUIRED)
    referenced_zone_ids = {
        object_.planting_zone_id
        for object_ in (project.plan.objects if project.plan else [])
        if object_.planting_zone_id
    }
    supplied_zone_ids = {zone.id for zone in zones}
    if referenced_zone_ids - supplied_zone_ids:
        raise DomainFailure(FailureReason.ZONE_OCCUPIED)
    previous = {zone.id: zone for zone in project.planting_zones}
    changed_ids = {
        zone.id
        for zone in zones
        if not changed_only
        or zone.id not in previous
        or zone.geometry != previous[zone.id].geometry
    }
    # Удаление и переименование не блокируются прежними ошибками других зон.
    # Native-проекты используют рабочие зоны для поиска, а позиции проверяют
    # по территории CAD отдельно.
    # Native CAD projects validate their working zones against the CAD source
    # separately. Do not scan the entire display graph twice just to discard
    # every feature when source_review is present (large captures are common).
    check_boundary = (
        check_source_boundary and bool(changed_ids) and project.source_review is None
    )
    site_surfaces = [
        shape(feature["geometry"])
        for feature in (
            project.geometry.feature_collection.get("features", [])
            if check_boundary
            else []
        )
        if feature.get("properties", {}).get("kind") == "site_surface"
    ]
    borders = site_surfaces or [
        shape(feature["geometry"])
        for feature in (
            project.geometry.feature_collection.get("features", [])
            if check_boundary
            else []
        )
        if feature.get("properties", {}).get("kind") == "site_border"
    ]
    site = unary_union(borders).buffer(0) if borders else None
    seen_ids: set[str] = set()
    parsed_zones: list[tuple[PlantingZoneAssignment, BaseGeometry]] = []
    for zone in zones:
        if zone.id in seen_ids:
            raise DomainFailure(FailureReason.ZONE_ID_DUPLICATE)
        seen_ids.add(zone.id)
        changed = zone.id in changed_ids
        if not changed_ids:
            continue
        try:
            parsed = shape(zone.geometry)
        except Exception as error:
            if not changed:
                continue
            raise DomainFailure(
                FailureReason.ZONE_GEOMETRY_INVALID, {"zone_label": zone.label}
            ) from error
        # Контур является явным решением оператора. buffer(0) молча изменил бы
        # самопересечение, сохранив исходный неверный GeoJSON. Отклоняем его без
        # выдумывания области и изменения уже сохранённых зон.
        if not parsed.is_valid:
            if not changed:
                continue
            raise DomainFailure(
                FailureReason.ZONE_CONTOUR_INVALID, {"zone_label": zone.label}
            )
        if (
            parsed.is_empty
            or parsed.geom_type not in {"Polygon", "MultiPolygon"}
            or parsed.area < MIN_ZONE_AREA_M2
        ):
            if not changed:
                continue
            raise DomainFailure(
                FailureReason.ZONE_AREA_TOO_SMALL, {"zone_label": zone.label}
            )
        if changed and site is not None and not site.covers(parsed):
            # Для сложного корректного MultiPolygon GEOS может дать covers=False при
            # пустой точной разности с территорией. Проверяем включение разностью множеств
            # без допуска расстояния и без принятия внешней поверхности.
            outside = parsed.difference(site)
            if not outside.is_empty:
                raise DomainFailure(
                    FailureReason.ZONE_OUTSIDE_SITE, {"zone_label": zone.label}
                )
        for other_zone, other_geometry in parsed_zones:
            if not changed and other_zone.id not in changed_ids:
                continue
            overlap = parsed.intersection(other_geometry)
            # Локальная задача может быть вложена в общую территорию SITE_BORDER.
            # Вложенность намеренна; неоднозначное частичное пересечение отклоняется.
            nested = parsed.covers(other_geometry) or other_geometry.covers(parsed)
            if overlap.area > MAX_PARTIAL_OVERLAP_M2 and not nested:
                raise DomainFailure(
                    FailureReason.ZONE_OVERLAP,
                    {"zone_label": zone.label, "other_zone_label": other_zone.label},
                )
        parsed_zones.append((zone, parsed))


def validate_changed_planting_zones(
    project: Project, zones: list[PlantingZoneAssignment]
) -> None:
    """Правка зоны не требует исправлять все ранее сохранённые зоны."""
    validate_planting_zones(project, zones, changed_only=True)


def attach_planting_zone_features(project: Project) -> None:
    """Сохраняет видимость выбранных областей после геометрического расчёта."""
    if project.geometry is None:
        return
    features = [
        feature
        for feature in project.geometry.feature_collection.get("features", [])
        if feature.get("properties", {}).get("kind") != "planting_area"
    ]
    features.extend(
        {
            "type": "Feature",
            "id": f"planting-area-{zone.id}",
            "properties": {
                "kind": "planting_area",
                "planting_zone_id": zone.id,
                "label": zone.label,
            },
            "geometry": zone.geometry.copy(),
        }
        for zone in project.planting_zones
    )
    project.geometry.feature_collection["features"] = features
