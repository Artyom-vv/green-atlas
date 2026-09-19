from __future__ import annotations

from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.planting_zones.config import MAX_PARTIAL_OVERLAP_M2, MIN_ZONE_AREA_M2
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project


def validate_planting_zones(
    project: Project, zones: list[PlantingZoneAssignment]
) -> None:
    if project.geometry is None:
        raise ValueError("Сначала подготовьте карту и ограничения")
    if not zones:
        raise ValueError("Выберите хотя бы один участок")
    referenced_zone_ids = {
        object_.planting_zone_id
        for object_ in (project.plan.objects if project.plan else [])
        if object_.planting_zone_id
    }
    supplied_zone_ids = {zone.id for zone in zones}
    if referenced_zone_ids - supplied_zone_ids:
        raise ValueError("Нельзя удалить участок, в котором уже есть посадки")
    site_surfaces = [
        shape(feature["geometry"])
        for feature in project.geometry.feature_collection.get("features", [])
        if project.source_review is None and feature.get("properties", {}).get("kind") == "site_surface"
    ]
    borders = site_surfaces or [
        shape(feature["geometry"])
        for feature in project.geometry.feature_collection.get("features", [])
        if project.source_review is None and feature.get("properties", {}).get("kind") == "site_border"
    ]
    site = unary_union(borders).buffer(0) if borders else None
    seen_ids: set[str] = set()
    parsed_zones: list[tuple[str, BaseGeometry]] = []
    for zone in zones:
        if zone.id in seen_ids:
            raise ValueError("Идентификаторы участков должны быть уникальны")
        seen_ids.add(zone.id)
        try:
            parsed = shape(zone.geometry)
        except Exception as error:
            raise ValueError(
                f"Участок «{zone.label}» содержит некорректную геометрию"
            ) from error
        # The operator's contour is an explicit decision. ``buffer(0)``
        # would quietly turn a bow-tie or other self-intersection into a
        # different area, then persist the original invalid GeoJSON. Do
        # not invent that decision on the backend: reject it and keep the
        # already saved working areas untouched.
        if not parsed.is_valid:
            raise ValueError(
                f"Участок «{zone.label}» содержит самопересекающийся или некорректный контур"
            )
        if (
            parsed.is_empty
            or parsed.geom_type not in {"Polygon", "MultiPolygon"}
            or parsed.area < MIN_ZONE_AREA_M2
        ):
            raise ValueError(
                f"Участок «{zone.label}» должен быть полигоном площадью от 24 м²"
            )
        if site is not None and not site.covers(parsed):
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
        for other_label, other_geometry in parsed_zones:
            overlap = parsed.intersection(other_geometry)
            # A local task may be carved inside a broad territory such as
            # SITE_BORDER. Nested areas are intentional; partial overlaps
            # remain ambiguous and are rejected.
            nested = parsed.covers(other_geometry) or other_geometry.covers(parsed)
            if overlap.area > MAX_PARTIAL_OVERLAP_M2 and not nested:
                raise ValueError(
                    f"Участки «{other_label}» и «{zone.label}» пересекаются"
                )
        parsed_zones.append((zone.label, parsed))


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
