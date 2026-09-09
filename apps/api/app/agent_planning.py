"""Compile retained placement intent into existing validated preview tools."""
from app.agent_memory import TaskState
from app.agent_tools import execute_tool
from app.agent_conditions import bind_placement_conditions
from app.agent_perception import select_zone_by_spatial_intent

DELEGATED_LAYOUT_SAMPLE_LIMIT = 8
EXPLICIT_LAYOUT_SAMPLE_LIMIT = 3


def _resolve_zone_ids(application, project_id: str, task: TaskState, events: list[dict]) -> list[str]:
    """Resolve explicit or delegated scope before any placement preview."""
    values = task.values
    project = application.get(project_id, lightweight=True)
    if values.scope == "project":
        return [zone.id for zone in project.planting_zones]
    if values.scope == "zones" and values.zone_ids:
        known = {zone.id for zone in project.planting_zones}
        if set(values.zone_ids) - known:
            raise ValueError("Один из выбранных участков больше не существует")
        return list(dict.fromkeys(values.zone_ids))
    if values.spatial_anchor:
        event = execute_tool(application, project_id, "select_zone_by_spatial_intent", {
            "anchor": values.spatial_anchor,
            "alignment_target": values.alignment_target,
        })
        events.append(event)
        selected = event["result"].get("zone_id")
        if not selected:
            raise ValueError("Не удалось выбрать рабочий участок по пространственному ориентиру")
        return [selected]
    raise ValueError("Укажите участок для посадки")


def prepare_task(application, project_id: str, task: TaskState,
                 agent_species_revision_ids: list[str] | None = None) -> dict:
    if task.values.operation == "place":
        return prepare_placement(application, project_id, task, agent_species_revision_ids)
    if task.values.operation in {"edit", "delete"}:
        return prepare_existing(application, project_id, task)
    raise ValueError("Уточните, что необходимо сделать с проектом")


def prepare_existing(application, project_id: str, task: TaskState) -> dict:
    """Resolve exact targets, then preview; never choose arbitrary N objects."""
    values = task.values
    project = application.get(project_id, lightweight=True)
    if project.plan is None:
        raise ValueError("В проекте пока нет посадок")
    if values.constraints or values.exclusions:
        raise ValueError("Дополнительные условия сохранены; необходимо уточнить затрагиваемые объекты")
    events = []

    def call(name, arguments):
        event = execute_tool(application, project_id, name, arguments)
        if event["state_version"] != project.state_version:
            raise ValueError("Проект изменился во время подготовки предложения")
        events.append(event)
        return event["result"]

    if values.scope == "objects" and values.object_ids:
        ids = list(dict.fromkeys(values.object_ids))
        objects = []
        for start in range(0, len(ids), 200):
            objects.extend(call("inspect_plantings", {"object_ids": ids[start:start + 200]}))
    elif values.scope == "project" or (values.scope == "zones" and values.zone_ids):
        objects, offset = [], 0
        while True:
            page = call("find_plantings", {"zone_ids": values.zone_ids if values.scope == "zones" else [],
                                          "kind": values.plant_kind if values.plant_kind in {"tree", "shrub"} else None,
                                          "offset": offset, "limit": 200})
            objects.extend(page["items"])
            if page["next_offset"] is None:
                break
            offset = page["next_offset"]
    else:
        raise ValueError("Выберите посадки или укажите участок")
    if values.plant_kind in {"tree", "shrub"}:
        objects = [obj for obj in objects if obj["kind"] == values.plant_kind]
    if values.operation == "delete" and values.species_revision_ids:
        objects = [obj for obj in objects if obj["species_revision_id"] in values.species_revision_ids]
    if not objects:
        raise ValueError("Подходящие посадки не найдены")
    if len(objects) > 5000:
        raise ValueError("Выберите меньшую группу посадок для одного изменения")
    if values.quantity is not None and ((values.quantity_mode == "maximum" and len(objects) > values.quantity) or
                                        (values.quantity_mode != "maximum" and len(objects) != values.quantity)):
        raise ValueError(f"Найдено {len(objects)} посадок. Выберите конкретные объекты для изменения {values.quantity} посадок")
    species_id = None
    if values.operation == "delete":
        operations = [{"type": "delete", "object_id": obj["id"]} for obj in objects]
        label = "Удаление посадок"
    elif values.edit_action in {"lock", "unlock"}:
        locked = values.edit_action == "lock"
        operations = [{"type": "update", "object_id": obj["id"], "changes": {"locked": locked}}
                      for obj in objects if obj["locked"] != locked]
        objects = [obj for obj in objects if obj["locked"] != locked]
        if not operations:
            raise ValueError("Все выбранные посадки уже закреплены" if locked else "Закрепление уже снято")
        label = "Закрепление посадок" if locked else "Снятие закрепления"
    elif values.edit_action == "move":
        if values.move_dx_m is None and values.move_dy_m is None:
            raise ValueError("Укажите смещение посадок в метрах осей чертежа")
        dx, dy = values.move_dx_m or 0, values.move_dy_m or 0
        if dx == 0 and dy == 0:
            raise ValueError("Смещение равно нулю. Посадки остаются на месте")
        operations = [{"type": "update", "object_id": obj["id"], "changes": {"x": obj["x"] + dx, "y": obj["y"] + dy}} for obj in objects]
        label = "Перемещение посадок"
    else:
        species_ids = values.species_revision_ids or []
        if values.species_mode == "automatic":
            # Shortlisting validates all targets; no silent fallback to an
            # unsuitable species merely to make an operation possible.
            shortlist = call("species_shortlist", {"object_ids": [obj["id"] for obj in objects]})
            available = [item for item in shortlist if item["status"] == "available"]
            species_ids = [available[0]["species"]["id"]] if available else []
        if len(species_ids) != 1:
            raise ValueError("Укажите новую породу для выбранных посадок")
        species_id = species_ids[0]
        catalog = call("species_catalog", {})
        species = next((item for item in catalog if item["id"] == species_id), None)
        if not species or any(obj["kind"] != species["kind"] for obj in objects):
            raise ValueError("Порода не соответствует типу выбранных растений")
        operations = [{"type": "update", "object_id": obj["id"], "changes": {"species_revision_id": species_id}} for obj in objects]
        label = "Замена породы"
    preview = call("preview_changes", {"base_plan_version": project.plan.version, "source": "system", "label": label, "operations": operations})
    target_ids = {obj["id"] for obj in objects}
    affected = set(preview["deletion_ids"]) | {obj["id"] for obj in preview["updates"]}
    if preview["additions"] or not affected.issubset(target_ids):
        raise ValueError("Предложение затрагивает объекты вне задания")
    if preview["can_apply"] and affected != target_ids:
        raise ValueError("Предложение не охватывает все выбранные объекты")
    return {"operation": values.operation, "task": task.model_dump(mode="json"), "events": events,
            "change_set": preview, "requested": len(objects), "found": len(affected),
            "shortfall": len(target_ids - affected), "species_revision_id": species_id,
            "target_ids": [obj["id"] for obj in objects], "requires_confirmation": preview["can_apply"]}


def prepare_placement(application, project_id: str, task: TaskState,
                      agent_species_revision_ids: list[str] | None = None) -> dict:
    values = task.values
    project = application.get(project_id, lightweight=True)
    if values.operation != "place":
        raise ValueError("Для этого задания требуется другой инструмент")
    if project.plan is None:
        raise ValueError("Сначала необходимо подготовить план проекта")
    events = []
    zones = _resolve_zone_ids(application, project_id, task, events)
    condition_check = bind_placement_conditions(project, values.constraints, values.exclusions)
    if values.plant_kind == 'mixed':
        return prepare_mixed_placement(application, project_id, task, agent_species_revision_ids,
                                       events=events, resolved_zone_ids=zones)
    if values.plant_kind not in {"tree", "shrub"}:
        raise ValueError("Уточните состав посадок")
    if values.arrangement is None:
        raise ValueError("Уточните рисунок посадок")
    species_ids = values.species_revision_ids or agent_species_revision_ids or []
    if values.species_mode == "automatic":
        if not agent_species_revision_ids:
            shortlist = execute_tool(application, project_id, "species_shortlist", {"zone_ids": zones, "kind": values.plant_kind})
            events.append(shortlist)
            candidates = shortlist["result"]
            if not candidates:
                raise ValueError("В каталоге не найдены подходящие породы")
            available = [item for item in candidates if item["status"] == "available"]
            choice = (available or candidates)[0]
            species_ids = [choice["species"]["id"]]
    if len(species_ids) != 1:
        raise ValueError("Выберите одну породу или поручите подбор помощнику")
    quantity = values.quantity
    if quantity is None and values.quantity_mode != "fill_available":
        raise ValueError("Укажите желаемое количество посадок")
    if values.quantity_mode == "fill_available":
        # This is a bounded product policy, not a fabricated user quantity.
        quantity = 30
    common = {"base_plan_version": project.plan.version, "zone_ids": zones,
              "species_revision_id": species_ids[0], "size_class": "standard",
              "spacing_policy": values.spacing_policy or "balanced"}
    if values.arrangement in {"building_contour", "building_groves"}:
        if values.plant_kind != "tree":
            raise ValueError("Для кустарников вдоль зданий требуется отдельный рисунок")
        args = {**common, "max_sites": quantity, "arrangement": "contour" if values.arrangement == "building_contour" else "groves"}
        proposal = execute_tool(application, project_id, "preview_building_groups", args)
    elif values.arrangement in {"area", "grid", "groves", "road_edges"}:
        args = {**common, "target_count": quantity, "placement_mode": "count", "plant_kind": values.plant_kind}
        if values.arrangement == "area":
            proposal = execute_tool(application, project_id, "preview_fill", {**args, "layout": "natural"})
        else:
            mask = {"grid": "regular_grid", "groves": "cluster_groves", "road_edges": "road_edges"}[values.arrangement]
            proposal = execute_tool(application, project_id, "preview_mask", {**args, "mask_id": mask})
    else:
        raise ValueError("Для этого рисунка нужна геометрия линии, мазка или точки на карте")
    events.append(proposal)
    if any(event['state_version'] != project.state_version for event in events):
        raise ValueError("Проект изменился во время подготовки предложения")
    change_set = proposal["result"].get("change_set")
    additions = change_set["additions"] if change_set else []
    if any(obj["species_revision_id"] != species_ids[0] for obj in additions):
        raise ValueError("Результат не соответствует выбранной породе")
    if len(additions) > quantity:
        raise ValueError("Результат превышает заданное количество")
    if any(obj["planting_zone_id"] not in zones for obj in additions):
        raise ValueError("Результат выходит за выбранные участки")
    return {"task": task.model_dump(mode="json"), "events": events, "change_set": change_set,
            "condition_check": condition_check,
            "requested": None if values.quantity_mode == "fill_available" else quantity,
            "found": len(additions),
            "shortfall": 0 if values.quantity_mode == "fill_available" else max(0, quantity - len(additions)),
            "quantity_mode": values.quantity_mode, "species_revision_id": species_ids[0],
            "resolved_zone_ids": zones,
            "delegated_quantity_limit": 30 if values.quantity_mode == "fill_available" else None,
            "requires_confirmation": bool(change_set and change_set["can_apply"])}


def prepare_mixed_placement(application, project_id: str, task: TaskState,
                            agent_species_revision_ids: list[str] | None = None,
                            *, events: list[dict] | None = None,
                            resolved_zone_ids: list[str] | None = None) -> dict:
    """One mixed preview: the existing planner validates mutual spacing jointly."""
    values = task.values
    project = application.get(project_id, lightweight=True)
    events = events if events is not None else []
    zones = resolved_zone_ids or _resolve_zone_ids(application, project_id, task, events)
    if not project.plan or not zones or (not values.quantity and values.quantity_mode != "fill_available"):
        raise ValueError('Укажите участок и общее количество растений')
    quantity = values.quantity if values.quantity is not None else 30
    condition_check = bind_placement_conditions(project, values.constraints, values.exclusions)
    def call(name, arguments):
        event = execute_tool(application, project_id, name, arguments)
        if event['state_version'] != project.state_version:
            raise ValueError('Проект изменился во время подготовки предложения')
        events.append(event)
        return event['result']
    chosen = {}
    if values.species_mode == 'automatic' and agent_species_revision_ids:
        catalog = call('species_catalog', {})
        for identity in agent_species_revision_ids:
            species = next((item for item in catalog if item['id'] == identity), None)
            if not species or species['kind'] in chosen:
                raise ValueError('Агент выбрал недоступный или повторяющийся тип растения')
            chosen[species['kind']] = identity
        if set(chosen) != {'tree', 'shrub'}:
            raise ValueError('Агент не выбрал одновременно дерево и кустарник')
    elif values.species_mode == 'automatic':
        for kind in ('tree', 'shrub'):
            options = call('species_shortlist', {'zone_ids': zones, 'kind': kind})
            available = [item for item in options if item['status'] == 'available']
            if not available:
                raise ValueError('Для смешанного состава не найдены подходящие деревья и кустарники')
            chosen[kind] = available[0]['species']['id']
    else:
        catalog = call('species_catalog', {})
        for identity in values.species_revision_ids or []:
            species = next((item for item in catalog if item['id'] == identity), None)
            if not species or species['kind'] in chosen:
                raise ValueError('Для этого состава выберите одну породу дерева и одну кустарника')
            chosen[species['kind']] = identity
        if set(chosen) != {'tree', 'shrub'}:
            raise ValueError('Выберите дерево и кустарник или поручите подбор помощнику')
    args = {'base_plan_version': project.plan.version, 'zone_ids': zones,
            'composition': 'mixed', 'plant_kind': 'tree', 'tree_share': 0.65,
            'tree_species_revision_id': chosen['tree'], 'shrub_species_revision_id': chosen['shrub'],
            'size_class': 'standard', 'target_count': quantity, 'placement_mode': 'count',
            'spacing_policy': values.spacing_policy or 'balanced'}
    masks = {'building_contour': 'building_contour', 'building_groves': 'building_screen',
             'grid': 'regular_grid', 'groves': 'cluster_groves', 'road_edges': 'road_edges'}
    if values.arrangement not in {*masks, 'area'}:
        raise ValueError('Для этого рисунка требуется геометрия на карте')
    tool = 'preview_fill' if values.arrangement == 'area' else 'preview_mask'
    layout = {'layout': 'natural'} if tool == 'preview_fill' else {'mask_id': masks[values.arrangement]}
    attempts = []
    result = None
    # Try bounded alternative samples only when species selection is delegated.
    # Keep scope, species, count, placement and clearance policy unchanged.
    # A failed search is not proof of the site's absolute maximum capacity.
    # Bounded alternative sampling is a domain search, not a model retry. It
    # keeps the requested zone, composition, density and setbacks fixed while
    # giving geometry a few independent layouts before reporting a shortfall.
    sample_limit = (DELEGATED_LAYOUT_SAMPLE_LIMIT
                    if values.species_mode == 'automatic'
                    else EXPLICIT_LAYOUT_SAMPLE_LIMIT)
    for seed in range(1, sample_limit + 1):
        candidate = call(tool, {**args, **layout, 'seed': seed})
        change = candidate.get('change_set')
        objects = change['additions'] if change else []
        complete_composition = {obj['kind'] for obj in objects} == {'tree', 'shrub'}
        score = len(objects) if change and change['can_apply'] and complete_composition else 0
        attempts.append({'seed': seed, 'found': len(objects), 'usable': bool(score),
                         'reasons': candidate.get('reason_summary', [])})
        if result is None or score > result[0]:
            result = (score, candidate)
        if score >= quantity:
            break
    assert result is not None
    result = result[1]
    preview = result.get('change_set')
    additions = preview['additions'] if preview else []
    if any(obj['planting_zone_id'] not in zones or obj['species_revision_id'] != chosen.get(obj['kind']) for obj in additions):
        raise ValueError('Результат не соответствует участку или составу')
    if len(additions) > quantity:
        raise ValueError('Результат превышает общее количество растений')
    if additions and {obj['kind'] for obj in additions} != {'tree', 'shrub'}:
        raise ValueError('В проверенных позициях не удалось разместить оба типа растений')
    shortfall = max(0, quantity-len(additions)) if values.quantity_mode != "fill_available" else 0
    reasons = result.get('reason_summary', []) if shortfall else []
    codes = {reason.get('code') for reason in reasons}
    explanation = None
    if shortfall:
        explanation = f'В проверенных вариантах найдено {len(additions)} из {quantity} мест. '
        if 'PLANT_SPACING' in codes:
            explanation += 'Часть позиций исключена из-за расстояний между посадками. '
        explanation += 'Участок и условия сохранены; максимальная вместимость не установлена.'
        if not reasons:
            reasons = [{
                'code': 'PLACEMENT_CAPACITY_SAMPLE',
                'message': 'В ограниченном поиске не найдено больше допустимых мест без изменения условий.',
                'found': len(additions),
                'requested': quantity,
                'samples_checked': len(attempts),
            }]
    return {'task': task.model_dump(mode='json'), 'events': events, 'change_set': preview,
            'condition_check': condition_check,
            'requested': None if values.quantity_mode == 'fill_available' else quantity,
            'found': len(additions), 'shortfall': shortfall,
            'quantity_mode': values.quantity_mode, 'species_revision_ids': list(chosen.values()),
            'resolved_zone_ids': zones,
            'delegated_quantity_limit': 30 if values.quantity_mode == "fill_available" else None,
            'search': {'attempts': attempts, 'exhaustive': False},
            'shortfall_evidence': reasons, 'shortfall_explanation': explanation,
            'composition_policy': {'tree_share': 0.65, 'source': 'planner_default', 'exact_ratio': False},
            'requires_confirmation': bool(preview and preview['can_apply'])}
