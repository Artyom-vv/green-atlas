from __future__ import annotations

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.planting_zones.config import MAX_PARTIAL_OVERLAP_M2, MIN_ZONE_AREA_M2
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project


def validate_planting_zones(
    project: Project, zones: list[PlantingZoneAssignment], *,
    changed_only: bool = False, check_source_boundary: bool = True,
) -> None:
    if project.geometry is None:
        raise ValueError("Сначала подготовьте карту и ограничения")
    if not zones and not (changed_only and project.planting_zones):
        raise ValueError("Выберите хотя бы один участок")
    referenced_zone_ids = {
        object_.planting_zone_id
        for object_ in (project.plan.objects if project.plan else [])
        if object_.planting_zone_id
    }
    supplied_zone_ids = {zone.id for zone in zones}
    if referenced_zone_ids - supplied_zone_ids:
        raise ValueError("Нельзя удалить участок, в котором уже есть посадки")
    previous = {zone.id: zone for zone in project.planting_zones}
    changed_ids = {zone.id for zone in zones if not changed_only
                   or zone.id not in previous or zone.geometry != previous[zone.id].geometry}
    # Removal/rename must not be blocked by pre-existing geometry elsewhere.
    # Native point-query projects also deliberately use working zones as search
    # domains: their actual planting positions are checked against the CAD site.
    check_boundary = check_source_boundary and bool(changed_ids)
    site_surfaces = [
        shape(feature["geometry"])
        for feature in (project.geometry.feature_collection.get("features", []) if check_boundary else [])
        if project.source_review is None and feature.get("properties", {}).get("kind") == "site_surface"
    ]
    borders = site_surfaces or [
        shape(feature["geometry"])
        for feature in (project.geometry.feature_collection.get("features", []) if check_boundary else [])
        if project.source_review is None and feature.get("properties", {}).get("kind") == "site_border"
    ]
    site = unary_union(borders).buffer(0) if borders else None
    seen_ids: set[str] = set()
    parsed_zones: list[tuple[PlantingZoneAssignment, BaseGeometry]] = []
    for zone in zones:
        if zone.id in seen_ids:
            raise ValueError("Идентификаторы участков должны быть уникальны")
        seen_ids.add(zone.id)
        changed = zone.id in changed_ids
        if not changed_ids:
            continue
        try:
            parsed = shape(zone.geometry)
        except Exception as error:
            if not changed:
                continue
            raise ValueError(
                f"Участок «{zone.label}» содержит некорректную геометрию"
            ) from error
        # The operator's contour is an explicit decision. ``buffer(0)``
        # would quietly turn a bow-tie or other self-intersection into a
        # different area, then persist the original invalid GeoJSON. Do
        # not invent that decision on the backend: reject it and keep the
        # already saved working areas untouched.
        if not parsed.is_valid:
            if not changed:
                continue
            raise ValueError(
                f"Участок «{zone.label}» содержит самопересекающийся или некорректный контур"
            )
        if (
            parsed.is_empty
            or parsed.geom_type not in {"Polygon", "MultiPolygon"}
            or parsed.area < MIN_ZONE_AREA_M2
        ):
            if not changed:
                continue
            raise ValueError(
                f"Участок «{zone.label}» должен быть полигоном площадью от 24 м²"
            )
        if changed and site is not None and not site.covers(parsed):
            # GEOS can report ``covers=False`` for a valid complex
            # MultiPolygon whose exact set difference from the site is empty
            # (observed on the calculated Kustanayskaya allowed area).  The
            # set difference is the authoritative containment check here: it
            # does not add a distance tolerance or admit any outside surface.
            outside = parsed.difference(site)
            if not outside.is_empty:
                raise ValueError(
                    f"Участок «{zone.label}» выходит за границы территории"
                )
        for other_zone, other_geometry in parsed_zones:
            if not changed and other_zone.id not in changed_ids:
                continue
            overlap = parsed.intersection(other_geometry)
            # A local task may be carved inside a broad territory such as
            # SITE_BORDER. Nested areas are intentional; partial overlaps
            # remain ambiguous and are rejected.
            nested = parsed.covers(other_geometry) or other_geometry.covers(parsed)
            if overlap.area > MAX_PARTIAL_OVERLAP_M2 and not nested:
                raise ValueError(
                    f"Участки «{other_zone.label}» и «{zone.label}» пересекаются"
                )
        parsed_zones.append((zone, parsed))


def validate_changed_planting_zones(project: Project, zones: list[PlantingZoneAssignment]) -> None:
    """An edit is not an implicit demand to repair every previously saved zone."""
    validate_planting_zones(project, zones, changed_only=True)


def attach_planting_zone_features(project: Project) -> None:
    """Keep areas selected before a geometry calculation visible afterwards."""
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
