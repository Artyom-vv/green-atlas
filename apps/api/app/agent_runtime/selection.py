"""Bind an explicitly requested map selection to an immutable UI snapshot."""
import re

from app.agent_runtime.contracts import SelectionBinding, SelectionContext, SelectionIssue, SelectionReference, ResolvedScope
from app.agent_runtime.semantic_guardrails import infer_semantic_signals


def selection_reference(text: str, *, source_turns: list[str] | None = None) -> SelectionReference | None:
    turns = source_turns or [text]
    references = []
    for turn in turns:
        matches = list(re.finditer(r'\b(?:выделенн\w*|выбранн\w*|выделени\w*)\b', turn, re.IGNORECASE))
        if not matches:
            continue
        targets = set()
        supported_mentions = []
        unsupported_reason = None
        for match in matches:
            # Only the referred noun phrase chooses an object/zone scope.
            phrase = turn[match.end():].split(".")[0].split(",")[0]
            words = re.findall(r'\w+', phrase.casefold())[:3]
            if words and words[0].startswith(("пород", "схем", "метод", "способ", "вариант", "режим", "план")):
                continue
            scoped_noun = False
            for word in words:
                if word.startswith(("участ", "зон", "област")):
                    targets.add("zones")
                    scoped_noun = True
                    break
                if word.startswith(("дерев", "кустар", "посад", "растен", "объект")):
                    targets.add("objects")
                    scoped_noun = True
                    break
            if not scoped_noun:
                previous = re.findall(r'\w+', turn[:match.start()].casefold())[-2:]
                if previous and previous[-1].startswith(("участ", "зон", "област")):
                    targets.add("zones")
                    scoped_noun = True
                elif previous and previous[-1].startswith(("дерев", "кустар", "посад", "растен", "объект")):
                    targets.add("objects")
                    scoped_noun = True
            generic_highlight = (match[0].casefold().startswith("выделени") or
                (match[0].casefold().startswith("выделенн") and (not words or words[:2] == ["на", "карте"])))
            if not scoped_noun and not generic_highlight:
                continue
            supported_mentions.append(match)
            prefix = turn[:match.start()].casefold()
            if re.search(r'\b(?:кроме|исключая|без|не(?:\s+(?:на|в|для|из))?)\s*$', prefix):
                unsupported_reason = "Исключение или отрицание выделенной области пока не поддержано. Укажите нужную область прямо."
        if not supported_mentions:
            continue
        if len(targets) > 1:
            raise ValueError("Укажите одно выделение: участки либо посадки")
        target = next(iter(targets), None)
        kind = infer_semantic_signals(turn).plant_kind if target == "objects" else None
        references.append(SelectionReference(target=target, plant_kind=kind, unsupported_reason=unsupported_reason))
    if not references:
        return None
    last = references[-1]
    # A generic answer explicitly refreshing the selection retains the noun
    # the original question referred to; it never combines the two sets.
    chosen = next((item for item in reversed(references) if item.target is not None), last)
    chosen = chosen.model_copy(update={"unsupported_reason": last.unsupported_reason})
    if chosen.target == "objects" and chosen.plant_kind is None:
        prior_kind = next((item.plant_kind for item in reversed(references) if item.target == "objects" and item.plant_kind), None)
        chosen = chosen.model_copy(update={"plant_kind": prior_kind})
    return chosen


def bound_scope(intent):
    if intent.scope_mode == "selection":
        binding = intent.selection_binding
        return (list(binding.zone_ids), list(binding.object_ids)) if binding and not intent.selection_issue else ([], [])
    return list(intent.explicit_zone_ids), list(intent.explicit_object_ids)


def selection_problem(intent, project=None):
    if intent.scope_mode != "selection":
        return None
    if intent.selection_issue:
        return intent.selection_issue
    context, binding, reference = intent.selection_context, intent.selection_binding, intent.selection_reference
    if context is None or binding is None or reference is None:
        return SelectionIssue(code="SELECTION_MISSING", message="Передайте выделение карты: участки либо посадки.")
    if reference.unsupported_reason:
        return SelectionIssue(code="SELECTION_CONFLICT", message=reference.unsupported_reason)
    if intent.explicit_zone_ids or intent.explicit_object_ids or selection_reference(intent.raw_text, source_turns=intent.source_turns) != reference:
        return SelectionIssue(code="SELECTION_CONFLICT", message="Область выделения не подтверждена исходным запросом.")
    if (set(binding.zone_ids) - set(context.zone_ids) or set(binding.object_ids) - set(context.object_ids)
            or (reference.target == "zones" and binding.object_ids) or (reference.target == "objects" and binding.zone_ids)):
        return SelectionIssue(code="SELECTION_CONFLICT", message="Область задания не совпадает с исходным выделением.")
    if project is not None:
        if context.project_id != project.id:
            return SelectionIssue(code="SELECTION_PROJECT_MISMATCH", message="Выделение относится к другому проекту.")
        if binding.state_version != project.state_version or binding.plan_version != (project.plan.version if project.plan else None):
            return SelectionIssue(code="SELECTION_STALE", message="Проект изменился после принятия выделения. Подтвердите текущее выделение или пересчитайте то же задание.")
        if (set(binding.zone_ids) - {item.id for item in project.planting_zones}
                or set(binding.object_ids) - {item.id for item in (project.plan.objects if project.plan else [])}):
            return SelectionIssue(code="SELECTION_UNKNOWN", message="Один из выделенных участков или объектов больше не существует.")
    zones = set(context.zone_ids) if not binding.object_ids else set()
    objects = set(context.object_ids) if not binding.zone_ids else set()
    if reference.target is None and context.zone_ids and context.object_ids:
        return SelectionIssue(code="SELECTION_AMBIGUOUS", message="Уточните, какое выделение использовать: участки или посадки.")
    if reference.plant_kind in {"tree", "shrub"}:
        if intent.plant_kind != reference.plant_kind:
            return SelectionIssue(code="SELECTION_CONFLICT", message="Тип растений изменён относительно просьбы о выделении.")
        if project is not None:
            all_objects = {item.id: item for item in (project.plan.objects if project.plan else [])}
            if objects - all_objects.keys():
                return SelectionIssue(code="SELECTION_UNKNOWN", message="Исходное выделение содержит неизвестные посадки.")
            objects = {identity for identity in objects if all_objects[identity].kind == reference.plant_kind}
        else:
            objects = set(binding.object_ids)  # kind membership is checked against authoritative objects at the gateway
    if zones != set(binding.zone_ids) or objects != set(binding.object_ids):
        return SelectionIssue(code="SELECTION_CONFLICT", message="Задание не охватывает точный запрошенный набор выделения.")
    if intent.goal.operation == "place" and binding.object_ids:
        return SelectionIssue(code="SELECTION_CONFLICT", message="Для новой посадки выделите участки, а не существующие растения.")
    return None


def bind_selection(intent, context: SelectionContext | None, project, *, reference=None):
    reference = reference or intent.selection_reference or selection_reference(intent.raw_text, source_turns=intent.source_turns)
    if reference is None:
        return intent  # Merely sending a UI snapshot never authorizes its use.
    updates = {"scope_mode": "selection", "selection_reference": reference, "selection_context": context,
               "selection_binding": None, "selection_issue": None, "explicit_zone_ids": [], "explicit_object_ids": []}
    if reference.plant_kind:
        updates["plant_kind"] = reference.plant_kind
    def reject(code, message):
        return intent.model_copy(update={**updates, "selection_issue": SelectionIssue(code=code, message=message)})
    if reference.unsupported_reason:
        return reject("SELECTION_CONFLICT", reference.unsupported_reason)
    if intent.explicit_zone_ids or intent.explicit_object_ids:
        return reject("SELECTION_CONFLICT", "В задании одновременно указаны явная область и выделение. Уточните одну область.")
    if context is None:
        return reject("SELECTION_MISSING", "В задании запрошено выделение, но снимок карты не передан.")
    if context.project_id != project.id:
        return reject("SELECTION_PROJECT_MISMATCH", "Выделение относится к другому проекту.")
    if context.state_version != project.state_version or context.plan_version != (project.plan.version if project.plan else None):
        return reject("SELECTION_STALE", "Выделение устарело. Передайте текущее выделение карты.")
    zones, objects = list(dict.fromkeys(context.zone_ids)), list(dict.fromkeys(context.object_ids))
    if reference.target == "objects":
        zones = []
    elif reference.target == "zones":
        objects = []
    elif zones and objects:
        return reject("SELECTION_AMBIGUOUS", "В снимке есть участки и посадки. Уточните, какое выделение использовать.")
    known_objects = {item.id: item for item in project.plan.objects} if project.plan else {}
    if set(zones) - {item.id for item in project.planting_zones} or set(objects) - known_objects.keys():
        return reject("SELECTION_UNKNOWN", "Один из выделенных участков или объектов не найден в проекте.")
    if reference.plant_kind in {"tree", "shrub"}:
        objects = [identity for identity in objects if known_objects[identity].kind == reference.plant_kind]
    if not zones and not objects:
        return reject("SELECTION_EMPTY", "В выделении нет запрошенных участков или посадок. Выделите их на карте и явно передайте новое выделение.")
    updates["selection_binding"] = SelectionBinding(zone_ids=zones, object_ids=objects,
        state_version=project.state_version, plan_version=project.plan.version if project.plan else None)
    return intent.model_copy(update=updates)


def refresh_selection(intent, project):
    """Explicit Resume validates the same IDs, never current UI selection."""
    if intent.scope_mode != "selection" or intent.selection_binding is None or intent.selection_context is None:
        return intent
    binding = intent.selection_binding.model_copy(update={"state_version": project.state_version,
        "plan_version": project.plan.version if project.plan else None})
    updated = intent.model_copy(update={"selection_binding": binding, "selection_issue": None})
    problem = selection_problem(updated, project)
    return updated.model_copy(update={"selection_issue": problem}) if problem else updated


def resolved_selection(intent, project_id):
    if intent.scope_mode != "selection" or selection_problem(intent):
        return None
    binding = intent.selection_binding
    return ResolvedScope(project_id=project_id, zone_ids=binding.zone_ids, object_ids=binding.object_ids,
        basis="selection", criteria=["source_requested_selection", "snapshot_ids_verified"], source_revision=binding.state_version)
