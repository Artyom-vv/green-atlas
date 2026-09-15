"""Typed bridge to existing domain tools. No arbitrary method or code execution.

This registry contains read and preview operations only. Plan writes remain at
the explicit confirmation boundary, never available to model tool selection.
"""
from dataclasses import dataclass
from typing import Annotated, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field
from shapely.geometry import Point, box
from shapely.geometry import shape
from shapely.ops import unary_union

from app import building_screen
from app.agent_perception import select_zone_by_spatial_intent
from app.agent_conditions import PositionRuleId, bind_placement_conditions
from app.contracts import (BuildingScreenRequest, BrushPreviewRequest, FillPatternRequest,
                          PlacementCheckRequest, PlacementMaskRequest, PlantingZoneAssignment,
                          PlanChangeSetDraft, RecommendationRequest, RowPatternRequest,
                          SpeciesShortlistRequest)


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


Coordinate = Annotated[float, Field(allow_inf_nan=False)]
Bounds = tuple[Coordinate, Coordinate, Coordinate, Coordinate]


class ObjectQuery(Empty):
    text: str = Field(default="", max_length=200)
    kind: Literal["tree", "shrub"] | None = None
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    species_revision_ids: list[str] = Field(default_factory=list, max_length=100)
    locked: bool | None = None
    bounds: Bounds | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


class ObjectSelection(Empty):
    object_ids: list[str] = Field(min_length=1, max_length=200)


class SpeciesQuery(Empty):
    text: str = Field(default="", max_length=200)
    kind: Literal["tree", "shrub"] | None = None


class GeometryQuery(Empty):
    extent: Bounds
    resolution: float = Field(default=1, gt=0, allow_inf_nan=False)


class GrowthQuery(Empty):
    horizon_year: int = Field(ge=0, le=40)


class GrowthObjectsQuery(GrowthQuery):
    object_ids: list[str] = Field(default_factory=list, max_length=200)
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=30, ge=1, le=100)


def _growth_objects(app, project_id, query):
    from app.species.catalog import forecast_at
    project = app.get(project_id, lightweight=True)
    objects = project.plan.objects if project.plan else []
    if set(query.object_ids) - {obj.id for obj in objects}:
        raise ValueError("Одна из посадок больше не существует")
    if set(query.zone_ids) - {zone.id for zone in project.planting_zones}:
        raise ValueError("Один из участков больше не существует")
    selected = [obj for obj in objects if (not query.object_ids or obj.id in query.object_ids)
                and (not query.zone_ids or obj.planting_zone_id in query.zone_ids)]

    def envelope(values):
        value = forecast_at(values, query.horizon_year)
        return ({"diameter_min_m": value.radius_min_m * 2, "diameter_max_m": value.radius_max_m * 2,
                 "confidence": value.confidence, "basis": value.basis} if value else None)

    items = [{"object_id": obj.id, "species_revision_id": obj.species_revision_id,
              "canopy": envelope(obj.canopy_forecast), "roots": envelope(obj.root_forecast)}
             for obj in selected[query.offset:query.offset + query.limit]]
    return {"horizon_year": query.horizon_year, "total": len(selected), "offset": query.offset, "items": items,
            "next_offset": query.offset + len(items) if query.offset + len(items) < len(selected) else None,
            "missing_data_meaning": "null означает отсутствие прогноза, не нулевой размер. Прогноз не является нормативным отступом."}


class ZoneSelection(Empty):
    zone_ids: list[str] = Field(min_length=1, max_length=40)


class ZoneCandidateQuery(Empty):
    """Search all existing work zones before the agent resolves one scope."""

    arrangement: Literal["area", "building_contour", "building_groves", "road_edges"] | None = None
    plant_kind: Literal["tree", "shrub", "mixed"] | None = None
    target_count: int | None = Field(default=None, ge=1, le=5000)
    limit: int = Field(default=20, ge=1, le=80)


class PlacementPrepareQuery(Empty):
    """Structured bridge from an agent intent to the placement planner.

    The model supplies only product decisions. Norms, geometry and the final
    candidate validation remain in ``agent_planning.prepare_task``.
    """

    base_plan_version: int = Field(ge=1)
    zone_ids: list[str] = Field(min_length=1, max_length=40)
    plant_kind: Literal["tree", "shrub", "mixed"]
    arrangement: Literal["area", "building_contour", "building_groves", "road_edges"]
    target_count: int | None = Field(default=None, ge=1, le=5000)
    quantity_mode: Literal["target", "maximum", "fill_available"] = "target"
    spacing_policy: Literal["open", "balanced", "canopy"] = "balanced"
    species_revision_ids: list[str] = Field(default_factory=list, max_length=10)
    condition_rule_ids: list[PositionRuleId] = Field(default_factory=list, max_length=2)


class SpatialIntentQuery(Empty):
    anchor: Literal["edge"]
    alignment_target: Literal["road", "building"] | None = None


class IssueQuery(Empty):
    object_ids: list[str] = Field(default_factory=list, max_length=200)
    zone_ids: list[str] = Field(default_factory=list, max_length=80)
    rule_ids: list[str] = Field(default_factory=list, max_length=80)
    severity: Literal["warning", "error"] | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


def _issues(app, project_id, query):
    project = app.get(project_id, lightweight=True)
    plan = project.plan
    objects = {obj.id: obj for obj in plan.objects} if plan else {}
    if set(query.object_ids) - objects.keys():
        raise ValueError("Одна из посадок больше не существует")
    if set(query.zone_ids) - {zone.id for zone in project.planting_zones}:
        raise ValueError("Один из участков больше не существует")
    scoped = bool(query.object_ids or query.zone_ids)
    targets = {identity for identity, obj in objects.items()
               if (not query.object_ids or identity in query.object_ids)
               and (not query.zone_ids or obj.planting_zone_id in query.zone_ids)}
    matches = []
    for issue in plan.issues if plan else []:
        affected = set(issue.related_object_ids)
        if issue.object_id:
            affected.add(issue.object_id)
        if scoped and not affected.intersection(targets):
            continue
        if query.rule_ids and issue.rule_id not in query.rule_ids:
            continue
        if query.severity and issue.severity != query.severity:
            continue
        matches.append(issue.model_dump(mode="json"))
    items = matches[query.offset:query.offset + query.limit]
    return {"source": "saved_plan_validation", "plan_version": plan.version if plan else None,
            "total": len(matches), "offset": query.offset, "items": items,
            "next_offset": query.offset + len(items) if query.offset + len(items) < len(matches) else None,
            "scope_note": "Показаны сохранённые результаты проверки, не новая полная проверка. Отсутствие записей не доказывает отсутствие всех ограничений.",
            "global_issues_excluded": scoped}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    effect: Literal["read", "preview"]
    execute: Callable


def _summary(app, project_id, _):
    from app.agent_interpreter import project_context
    project = app.get(project_id, lightweight=True)
    return {"id": project.id, "name": project.name, "state_version": project.state_version,
            "plan_version": project.plan.version if project.plan else None,
            "planting_count": len(project.plan.objects) if project.plan else 0,
            "zones": project_context(project)["zones"],
            "layers": [layer.model_dump(mode="json") for layer in project.layers]}


def _zones(app, project_id, query):
    project = app.get(project_id, lightweight=True)
    zones = {zone.id: zone for zone in project.planting_zones}
    if set(query.zone_ids) - zones.keys():
        raise ValueError("Один из участков больше не существует")
    return [zones[identity] for identity in dict.fromkeys(query.zone_ids)]


def _zone_candidates(app, project_id, query):
    """Return deterministic evidence for an agent-delegated zone choice.

    This is a read-only shortlist, not a silent selection.  A later tool call
    must resolve exactly one candidate and retain the evidence reference.
    """
    project = app.get(project_id)
    objects = project.plan.objects if project.plan else []
    building_features = []
    if project.geometry:
        for feature in project.geometry.feature_collection.get("features", []):
            if (feature.get("properties") or {}).get("kind") != "building" or not feature.get("geometry"):
                continue
            try:
                geometry = shape(feature["geometry"])
            except (TypeError, ValueError):
                continue
            if not geometry.is_empty:
                building_features.append(geometry)
    buildings = unary_union(building_features) if building_features else None
    candidates = []
    for zone in project.planting_zones:
        try:
            geometry = shape(zone.geometry)
        except (TypeError, ValueError):
            continue
        if geometry.is_empty:
            continue
        nearby_building = bool(buildings is not None and geometry.buffer(40).intersects(buildings))
        zone_objects = [item for item in objects if item.planting_zone_id == zone.id]
        score = (
            int(query.arrangement in {"building_contour", "building_groves"} and nearby_building),
            int(nearby_building),
            float(geometry.area),
            -len(zone_objects),
            zone.id,
        )
        candidates.append({
            "zone_id": zone.id,
            "label": zone.label,
            "area_m2": round(float(geometry.area), 2),
            "existing_plantings": len(zone_objects),
            "nearby_buildings": nearby_building,
            "requested_count": query.target_count,
            "ranking_score": list(score[:-1]),
            "selection_status": "candidate",
        })
    candidates.sort(key=lambda item: tuple(item["ranking_score"] + [item["zone_id"]]), reverse=True)
    return {
        "items": candidates[:query.limit],
        "total": len(candidates),
        "selection_status": "not_resolved",
        "note": "Кандидаты не являются выбранным участком. Выберите одну зону и подтвердите её фактическими инструментами.",
    }


def _select_zone(app, project_id, query):
    # Spatial intent is one of the few reads that must include the normalized
    # DXF geometry. A lightweight project intentionally omits that payload.
    project = app.get(project_id)
    return select_zone_by_spatial_intent(
        project,
        anchor=query.anchor,
        alignment_target=query.alignment_target,
    )


def _road_targets(app, project_id, query):
    """Return compact evidence about real road geometry near the target zone."""
    from app.building_screen import screen_features
    project = app.get(project_id)
    roads = [feature for feature in screen_features(project, query.zone_ids)
             if (feature.get("properties") or {}).get("kind") == "road"]
    return {
        "count": len(roads),
        "geometry_types": sorted({feature.get("geometry", {}).get("type") for feature in roads}),
        "source_ids": [str((feature.get("properties") or {}).get("source_id"))
                       for feature in roads[:20]
                       if (feature.get("properties") or {}).get("source_id") is not None],
    }


def _objects(app, project_id, query):
    project = app.get(project_id, lightweight=True)
    objects = project.plan.objects if project.plan else []
    known_zones = {zone.id for zone in project.planting_zones}
    if set(query.zone_ids) - known_zones:
        raise ValueError("Один из участков больше не существует")
    area = None
    if query.bounds is not None:
        if query.bounds[0] > query.bounds[2] or query.bounds[1] > query.bounds[3]:
            raise ValueError("Некорректная область поиска")
        area = box(*query.bounds)
    text = query.text.casefold().strip()
    species = {item.id: item for item in app.species_catalog()}
    matches = []
    for obj in objects:
        if query.kind and obj.kind != query.kind:
            continue
        if query.zone_ids and obj.planting_zone_id not in query.zone_ids:
            continue
        if query.group_ids and not set(query.group_ids).intersection(obj.group_ids):
            continue
        if query.species_revision_ids and obj.species_revision_id not in query.species_revision_ids:
            continue
        if query.locked is not None and obj.locked != query.locked:
            continue
        if area is not None and not area.covers(Point(obj.x, obj.y)):
            continue
        item = species.get(obj.species_revision_id)
        searchable = " ".join(filter(None, [obj.id, obj.species_revision_id, item.common_name if item else None]))
        if text and text not in searchable.casefold():
            continue
        matches.append(obj.model_dump(mode="json"))
    page = matches[query.offset:query.offset + query.limit]
    return {"total": len(matches), "offset": query.offset, "items": page,
            "next_offset": query.offset + len(page) if query.offset + len(page) < len(matches) else None}


def _inspect(app, project_id, query):
    project = app.get(project_id, lightweight=True)
    objects = {obj.id: obj for obj in project.plan.objects} if project.plan else {}
    if set(query.object_ids) - objects.keys():
        raise ValueError("Одна из посадок больше не существует")
    return [objects[identity] for identity in dict.fromkeys(query.object_ids)]


def _prepare_placement(app, project_id, query):
    """Prepare one auditable placement proposal on a verified zone set.

    This adapter deliberately does not duplicate placement algorithms. It
    converts the typed capability request into the already validated domain
    task, so a model cannot invent coordinates or bypass the rules engine.
    """
    from app.agent_memory import TaskPatch, TaskState
    from app.agent_planning import prepare_task
    from app.agent_runtime.placement import placement_outcome

    project = app.get(project_id, lightweight=True)
    if project.plan is None:
        raise ValueError("План ещё не создан")
    if project.plan.version != query.base_plan_version:
        raise ValueError("План изменился до подготовки предложения")

    task = TaskState(
        values=TaskPatch(
            operation="place",
            scope="zones",
            zone_ids=list(dict.fromkeys(query.zone_ids)),
            quantity=query.target_count,
            quantity_mode=query.quantity_mode,
            spacing_policy=query.spacing_policy,
            arrangement=query.arrangement,
            plant_kind=query.plant_kind,
            species_mode="specified" if query.species_revision_ids else "automatic",
            species_revision_ids=query.species_revision_ids or None,
            post_action="focus_map",
            constraints=["нормативные отступы"] if query.condition_rule_ids else None,
        ),
        provenance={"runtime": "agent-runtime"},
    )
    prepared = prepare_task(app, project_id, task, query.species_revision_ids or None)
    if query.condition_rule_ids:
        # Use the complete geometry for coverage evidence; lightweight project
        # metadata intentionally omits source features.
        prepared["condition_check"] = bind_placement_conditions(app.get(project_id), ["нормативные отступы"], [])
    outcome = placement_outcome(prepared, requested=query.target_count)
    requires_confirmation = outcome.status == "exact" and bool(prepared.get("requires_confirmation"))
    prepared["requires_confirmation"] = requires_confirmation
    return {
        "base_plan_version": query.base_plan_version,
        "proposal": prepared,
        "requires_confirmation": requires_confirmation,
        "placement_outcome": outcome.model_dump(mode="json"),
        "requested": outcome.requested,
        "found": outcome.found,
        "shortfall": outcome.shortfall,
        "arrangement": query.arrangement,
        "plant_kind": query.plant_kind,
        "condition_rule_ids": list(query.condition_rule_ids),
    }


TOOLS = (
    Tool("project_context", "Прочитать проект, участки и слои", Empty, "read", _summary),
    Tool("find_zone_candidates", "Найти подходящие рабочие участки по геометрии и контексту задачи", ZoneCandidateQuery, "read", _zone_candidates),
    Tool("select_zone_by_spatial_intent", "Выбрать одну существующую зону по пространственному ориентиру", SpatialIntentQuery, "read", _select_zone),
    Tool("inspect_zones", "Прочитать полную геометрию конкретных рабочих участков по ID", ZoneSelection, "read", _zones),
    Tool("find_plantings", "Найти посадки по виду, участку, группе, области и закреплению", ObjectQuery, "read", _objects),
    Tool("inspect_plantings", "Прочитать параметры конкретных посадок", ObjectSelection, "read", _inspect),
    Tool("species_catalog", "Найти породы по названию и типу", SpeciesQuery, "read", lambda a, p, q: [s for s in a.species_catalog(q.kind) if q.text.casefold() in (s.common_name + " " + s.scientific_name).casefold()]),
    Tool("species_shortlist", "Подобрать породы для реальных участков или посадок", SpeciesShortlistRequest, "read", lambda a, p, q: a.shortlist_species(p, q.object_ids, q.zone_ids, q.kind)),
    Tool("data_passport", "Проверить источники и полноту исходных данных", Empty, "read", lambda a, p, q: a.get_data_passport(p)),
    Tool("query_geometry", "Прочитать геометрию в области карты", GeometryQuery, "read", lambda a, p, q: a.query_geometry(p, q.extent, q.resolution)),
    Tool("growth_scene", "Рассчитать сцену с прогнозом роста", GrowthQuery, "read", lambda a, p, q: a.get_scene(p, q.horizon_year)),
    Tool("growth_objects", "Прочитать прогноз кроны и корней выбранных посадок или участков без построения 3D. Все размеры — диаметры в метрах. Отсутствующий прогноз — null.", GrowthObjectsQuery, "read", _growth_objects),
    Tool("plan_history", "Прочитать историю изменений плана", Empty, "read", lambda a, p, q: a.get_plan_history(p)),
    Tool("plan_issues", "Прочитать сохранённые нарушения плана, фактические и требуемые значения; фильтры по посадкам, участкам, правилам. Не выполняет новую проверку.", IssueQuery, "read", _issues),
    Tool("building_targets", "Найти контуры зданий рядом с участками", ZoneSelection, "read", lambda a, p, q: building_screen.targets(a.get(p), q.zone_ids)),
    Tool("road_targets", "Найти распознанные улицы и проезды рядом с участками", ZoneSelection, "read", _road_targets),
    Tool("prepare_placement", "Рассчитать проверяемое предложение посадки на выбранных участках", PlacementPrepareQuery, "preview", _prepare_placement),
    Tool("check_placement", "Проверить конкретное место посадки", PlacementCheckRequest, "read", lambda a, p, q: a.check_placement(p, q)),
    Tool("preview_zone", "Проверить геометрию нового участка", PlantingZoneAssignment, "preview", lambda a, p, q: a.preview_planting_zone(p, q)),
    Tool("preview_changes", "Предложить добавление, перемещение, состав, группы, закрепление или удаление посадок", PlanChangeSetDraft, "preview", lambda a, p, q: a.preview_change_set(p, q)),
    Tool("preview_row", "Предложить посадки вдоль заданной линии", RowPatternRequest, "preview", lambda a, p, q: a.preview_pattern(p, q)),
    Tool("preview_fill", "Предложить заполнение участков", FillPatternRequest, "preview", lambda a, p, q: a.preview_pattern(p, q)),
    Tool("preview_mask", "Предложить рисунок посадок: дороги, сетка, группы", PlacementMaskRequest, "preview", lambda a, p, q: a.preview_pattern(p, q)),
    Tool("preview_brush", "Предложить посадки по геометрии мазка", BrushPreviewRequest, "preview", lambda a, p, q: a.preview_brush(p, q)),
    Tool("preview_recommendation", "Подготовить предложение по площади", RecommendationRequest, "preview", lambda a, p, q: a.preview_recommendation(p, q)),
    Tool("preview_building_groups", "Подготовить посадки вдоль зданий: contour — ряд по контуру, groves — группы рядом", BuildingScreenRequest, "preview", lambda a, p, q: building_screen.preview(a, p, q)),
)
REGISTRY = {tool.name: tool for tool in TOOLS}


def tool_schemas() -> list[dict]:
    return [{"name": tool.name, "description": tool.description, "effect": tool.effect,
             "parameters": tool.arguments.model_json_schema()} for tool in TOOLS]


def _json(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    return value


def execute_tool(application, project_id: str, name: str, arguments: dict) -> dict:
    tool = REGISTRY.get(name)
    if tool is None:
        raise ValueError("Неизвестный инструмент")
    # Existing API models sometimes ignore extras; agent calls must not silently
    # discard user constraints or a forged project_id at this boundary.
    if set(arguments) - tool.arguments.model_fields.keys():
        raise ValueError("Инструмент получил неизвестные параметры")
    parsed = tool.arguments.model_validate(arguments, extra="forbid")
    before = application.get(project_id, lightweight=True).state_version
    result = tool.execute(application, project_id, parsed)
    after = application.get(project_id, lightweight=True).state_version
    if before != after:
        raise ValueError("Проект изменился во время расчёта. Повторите запрос")
    return {"tool": name, "effect": tool.effect, "project_id": project_id,
            "state_version": before, "result": _json(result)}
