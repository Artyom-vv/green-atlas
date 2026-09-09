"""Resolve map references against the project, never against model guesses.

A viewport describes what is visible; it is deliberately not a selection.
References are pinned to the state the user saw and stored with their message.
"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import shape
from shapely.ops import unary_union

from app.agent_memory import TaskPatch


Coordinate = Annotated[float, Field(allow_inf_nan=False)]


class MapContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state_version: int = Field(ge=1)
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    object_ids: list[str] = Field(default_factory=list, max_length=200)
    viewport: tuple[Coordinate, Coordinate, Coordinate, Coordinate] | None = None

    @model_validator(mode="after")
    def valid_extent(self):
        if self.viewport and (self.viewport[0] >= self.viewport[2] or self.viewport[1] >= self.viewport[3]):
            raise ValueError("Некорректная область карты")
        if len(set(self.zone_ids)) != len(self.zone_ids) or len(set(self.object_ids)) != len(self.object_ids):
            raise ValueError("Повторяющиеся ссылки в выделении")
        return self


class StaleMapContext(ValueError):
    pass


def perceive(project, context: MapContext) -> dict:
    if project.state_version != context.state_version:
        raise StaleMapContext("Проект изменился. Обновите выделение перед отправкой.")
    zones = {zone.id: zone for zone in project.planting_zones}
    objects = {obj.id: obj for obj in project.plan.objects} if project.plan else {}
    if set(context.zone_ids) - zones.keys() or set(context.object_ids) - objects.keys():
        raise ValueError("Объект выделения не найден в этом проекте")
    return {
        "state_version": project.state_version,
        "selected_zone_ids": context.zone_ids,
        "selected_objects": [objects[identity].model_dump(mode="json") for identity in context.object_ids],
        "viewport": context.viewport,
        "viewport_is_selection": False,
    }


def bind_selection(patch: TaskPatch, perception: dict | None) -> TaskPatch:
    """Freeze an explicit, unambiguous 'these objects' scope into stable IDs.

    Mixed/empty selection remains unresolved instead of widening the task to
    the whole project or dropping one kind of selected entity.
    """
    if patch.scope != "selection" or not perception:
        return patch
    zones = perception["selected_zone_ids"]
    objects = [item["id"] for item in perception["selected_objects"]]
    if bool(zones) == bool(objects):
        return patch
    changes = patch.model_dump(exclude_unset=True)
    if zones:
        if patch.object_ids or (patch.zone_ids and set(patch.zone_ids) != set(zones)):
            raise ValueError("Указанные участки не соответствуют выделению")
        changes.update(scope="zones", zone_ids=zones, object_ids=None)
    else:
        if patch.zone_ids or (patch.object_ids and set(patch.object_ids) != set(objects)):
            raise ValueError("Указанные посадки не соответствуют выделению")
        changes.update(scope="objects", object_ids=objects, zone_ids=None)
    return TaskPatch.model_validate(changes)


def select_zone_by_spatial_intent(project, *, anchor: str, alignment_target: str | None = None) -> dict:
    """Choose one existing zone for a delegated spatial request.

    This is deliberately deterministic and read-only. ``edge`` means contact
    with the outer boundary of the project territory, not the first item in a
    database list. When the user names a road-oriented action, a zone must be
    within the recognised road vicinity; a pedestrian path or an arbitrary
    source feature cannot silently stand in for a road.
    """
    if anchor != "edge":
        raise ValueError("Неподдерживаемый пространственный ориентир")
    geometries = []
    for zone in project.planting_zones:
        try:
            geometry = shape(zone.geometry)
        except (TypeError, ValueError):
            continue
        if not geometry.is_empty and geometry.geom_type in {"Polygon", "MultiPolygon"}:
            geometries.append((zone, geometry))
    if not geometries:
        raise ValueError("В проекте нет замкнутых рабочих участков для выбора")

    source_features = (project.geometry.feature_collection.get("features", [])
                       if project.geometry else [])
    borders = []
    roads = []
    for feature in source_features:
        kind = (feature.get("properties") or {}).get("kind")
        geometry_payload = feature.get("geometry")
        if not geometry_payload:
            continue
        try:
            geometry = shape(geometry_payload)
        except (TypeError, ValueError):
            continue
        if geometry.is_empty:
            continue
        if kind == "site_border":
            borders.append(geometry)
        elif kind == "road":
            roads.append(geometry)
    territory = unary_union(borders) if borders else unary_union([geometry for _, geometry in geometries])
    territory_boundary = territory.boundary
    road_vicinity = unary_union([road.buffer(40) for road in roads]) if roads else None
    scored = []
    for zone, geometry in geometries:
        edge_contact = geometry.boundary.intersection(territory_boundary).length
        road_match = bool(road_vicinity is not None and geometry.intersects(road_vicinity))
        if alignment_target == "road" and not road_match:
            continue
        # Prefer a real edge contact, then a road-facing candidate, then a
        # larger usable area. The ID is the stable final tie-breaker.
        scored.append((edge_contact, int(road_match), geometry.area, zone.id, zone, geometry))
    if not scored:
        if alignment_target == "road":
            raise ValueError("У края проекта не найдена рабочая зона рядом с распознанной улицей")
        raise ValueError("У края проекта не найдена подходящая рабочая зона")
    _edge_contact, _road_match, _area, _zone_id, zone, geometry = max(
        scored, key=lambda item: (item[0], item[1], item[2], item[3])
    )
    return {
        "zone_id": zone.id,
        "label": zone.label,
        "anchor": anchor,
        "alignment_target": alignment_target,
        "edge_contact_m": round(float(geometry.boundary.intersection(territory_boundary).length), 3),
        "road_count": len(roads),
    }
