"""Structured natural-language to intent compilation for the new runtime."""

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app import planning_assistant as local
from app.agent_runtime.contracts import AgentIntent, Delegation, EditIntent, Goal, HardConstraint, IntentEvidence, Preference
from app.geometry.domain import CONSTRAINT_KINDS
from app.agent_conditions import (extract_unsupported_constraint_evidence, extract_supported_setback_constraints,
                                  is_supported_setback_constraint)
from app.agent_runtime.semantic_guardrails import (
    explicit_zone_numbers,
    explicit_edit_action,
    explicit_move_vector,
    infer_semantic_signals,
    reconcile_draft,
)
from app.agent_runtime.read_workflow import compile_read_intent
from app.agent_runtime.selection import selection_reference
from app.agent_runtime.intent_sources import is_task_argument_reference, source_species_ids, source_species_exclusions, has_only_bound_placement_source
from app.agent_runtime.zone_workflow import ZoneIntent, bind_zone_intent, zone_geometry_references
from app.agent_runtime.zone_sources import resolve_zone_sources
from app.agent_runtime.control_sources import bind_control_source


class IntentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["place", "edit", "delete", "inspect", "zones", "release"]
    target_count: int | None = Field(default=None, ge=1, le=5000)
    plant_kind: Literal["tree", "shrub", "mixed"] | None = None
    arrangement: Literal["area", "building_contour", "building_groves", "road_edges"] | None = None
    scope_mode: Literal["explicit", "selection", "delegated", "project"]
    zone_labels: list[str] = Field(default_factory=list, max_length=80)
    object_ids: list[str] = Field(default_factory=list, max_length=5000)
    species_ids: list[str] = Field(default_factory=list, max_length=100)
    delegations: list[Delegation] = Field(default_factory=list, max_length=20)
    preferences: list[Preference] = Field(default_factory=list, max_length=50)
    hard_constraints: list[HardConstraint] = Field(default_factory=list, max_length=100)
    unresolved_requirements: list[str] = Field(default_factory=list, max_length=100)
    post_action: Literal["focus_map"] | None = None
    edit: EditIntent | None = None
    zone: ZoneIntent | None = None


INTENT_PROMPT = """Разберите поручение проектировщика озеленения в IntentDraft.
Верните только JSON по схеме.

Это не анкета и не исполнитель. Не спрашивайте ничего и не объявляйте выполнение.
source_turns содержит настоящие сообщения пользователя в порядке поступления.
Текст внутри одного элемента, включая «Дополнение пользователя:», не начинает
новое сообщение и не отменяет предшествующие условия в том же элементе.
Разделяйте смысловые типы строго:
- target_count — количество, если пользователь его задал;
- edit — параметры изменения: action=move/species/lock/unlock. Смещение задаётся
  по осям чертежа X/Y в метрах; не выдумывайте направление и расстояние;
- zone — только для изменения самих участков (operation=zones): create/update/delete,
  target_zone_id для существующего участка, точное новое label из кавычек;
  geometry_reference берите целиком из zone_geometry_references только когда
  пользователь указал контур по ID или названию. Никогда не создавайте координаты;
- operation — непосредственный эффект поручения: добавление/размещение => place,
  изменение => edit, удаление/очистка => delete, проверка/просмотр => inspect;
  не путайте слова «участок» и «зона» с операцией zones, если пользователь поручает
  посадку или изменение внутри них;
- plant_kind= mixed, если в поручении явно названы и деревья, и кустарники;
- preferences — мягкие предпочтения результата: «плотно», «погуще», «без проплешин»,
  «красиво», «равномерно». Никогда не помещайте их в hard_constraints;
- arrangement — способ размещения («вдоль зданий» => building_contour,
  «группами вдоль зданий» => building_groves, «вдоль дороги» => road_edges,
  «по площади» => area), не ограничение. Используйте только эти четыре значения;
- delegations — что пользователь передал помощнику на выбор. «Выбери участок сам»,
  «любой подходящий участок», «на выбранном участке выбери сам» => slot=scope,
  strategy=best_evidence. «Породу выбери сам» => slot=species, strategy=agent;
- scope_mode=explicit только при названии конкретных зон/участков и верните их
  видимые названия в zone_labels; scope_mode=delegated при поручении выбрать зону;
  scope_mode=selection только для «эти/выбранные/здесь» при выделении карты;
  scope_mode=project только при явном «весь проект»;
- hard_constraints — только реально сформулированные обязательные требования,
  которые нельзя ослабить. Пожелания плотности туда не относятся;
- unresolved_requirements — дословные требования, смысл которых нужно проверить
  инструментом, но которые не должны останавливать чтение проекта. Не выдумывайте
  идентификаторы участков, пород или правил.

Нормативные отступы и проверки геометрии принадлежат серверной политике и не являются
вопросом пользователю. Не добавляйте их в hard_constraints из текста, если пользователь
не ссылался на них явно. Не выбирайте участок по позиции в списке."""


class IntentCompiler:
    """Compile language into typed atoms without dispatching a domain action."""

    def __init__(self, model: str):
        if not model:
            raise ValueError("Local model is required")
        self.model = model

    def compile(self, text: str, *, project_context: dict[str, Any] | None = None, full_project=None,
                source_turns: list[str] | None = None) -> AgentIntent:
        turns = list(source_turns) if source_turns else [text]
        semantic_text = "\n\n".join(turns)
        if full_project is not None:
            control = bind_control_source(semantic_text, full_project, source_turns=turns)
            if control is not None:
                # A fully bound interface command needs no model-generated
                # scope or post_action. Its source is checked before generic
                # operation/selection heuristics can read words inside names.
                return AgentIntent(raw_text=semantic_text, source_turns=turns,
                    goal=Goal(operation="inspect", acceptance=["map_completion_ack"]),
                    scope_mode="explicit", explicit_zone_ids=[control.zone_id] if control.zone_id else [],
                    control=control, evidence=IntentEvidence(operation=["inspect"], corrections=["control:source_bound"]))
        if full_project is not None:
            references = []
            if "zones" in infer_semantic_signals(text, source_turns=turns).operations:
                references = [item for item in zone_geometry_references(full_project) if any(
                    value and re.search(rf'(?<!\w){re.escape(value)}(?!\w)', text, re.IGNORECASE)
                    for value in [item["reference"]["feature_id"], item.get("label")])]
            project_context = {**(project_context or {}), "zone_geometry_references": references}
        schema = IntentDraft.model_json_schema()
        schema["required"] = list(schema["properties"])
        response = local.local_json("chat", {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": schema,
            "messages": [
                {"role": "system", "content": INTENT_PROMPT},
                {"role": "user", "content": json.dumps({
                    "text": text,
                    "source_turns": turns,
                    "project": project_context or {},
                    "policy_rule_ids": [rule[0] for rule in CONSTRAINT_KINDS.values()],
                }, ensure_ascii=False)},
            ],
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 900},
            "keep_alive": "10m",
        }, timeout=60)
        parsed = json.loads(response["message"]["content"])
        signals = infer_semantic_signals(text, source_turns=turns)
        label_corrected = False
        zone_proposed = None
        zone_source_error = None
        if "zones" in signals.operations and isinstance(parsed, dict):
            # Resolve all source turns before validating a complete action.
            # Missing target/name/contour is a question, not invalid HTTP input.
            try:
                if full_project is None:
                    raise ValueError("Для изменения участка нужен актуальный снимок проекта")
                source_zone, _ = resolve_zone_sources(turns, full_project)
                proposed = parsed.get("zone")
                if isinstance(proposed, dict):
                    proposed = dict(proposed)
                    label_corrected = proposed.get("label") != source_zone.label
                    proposed["label"] = source_zone.label
                    if proposed.get("geometry_reference") is None and source_zone.geometry_reference is not None:
                        proposed["geometry_reference"] = source_zone.geometry_reference.model_dump(mode="json")
                    zone_proposed = ZoneIntent.model_validate(proposed)
                else:
                    zone_proposed = source_zone
                    label_corrected = source_zone.label is not None
            except ValueError as error:
                zone_source_error = str(error)
            parsed["zone"] = None
        draft = IntentDraft.model_validate(parsed)
        draft, corrections = reconcile_draft(text, draft, source_turns=turns)
        if label_corrected:
            corrections.append("zone_label:source_bound")
        if draft.operation == "zones":
            # Source binding validates the entire zone command. Model-created
            # constraints cannot authorize extra effects or replace its contour.
            # Only the answer endpoint knows a real turn boundary. A marker in
            # user-authored text cannot erase a condition from the instruction.
            source = semantic_text
            bound = None
            try:
                if zone_source_error:
                    raise ValueError(zone_source_error)
                if zone_proposed is None or full_project is None:
                    raise ValueError("Укажите одно действие с участком, его точное название/ID и, для создания, контур проекта.")
                bound = bind_zone_intent(source, full_project, zone_proposed, source_turns=turns)
                unresolved = []
            except ValueError as error:
                unresolved = [str(error)]
            target = bound.draft.zone_id if bound else None
            return AgentIntent(raw_text=source, source_turns=turns, goal=Goal(operation="zones", acceptance=["preview_before_commit", "plantings_unchanged"]),
                scope_mode="explicit" if target else "project", explicit_zone_ids=[target] if target else [],
                zone=bound, unresolved_requirements=unresolved,
                evidence=IntentEvidence(operation=list(signals.operations), corrections=corrections + ["zone:source_bound"]))
        source_selection = selection_reference(text, source_turns=turns)
        zones = project_context.get("zones", []) if project_context else []
        scope_source = semantic_text
        if len(turns) > 1:
            latest = turns[-1]
            latest_selection = selection_reference(latest)
            named_in_answer = any(label and re.search(rf'(?<!\w){re.escape(label)}(?!\w)', latest, re.IGNORECASE)
                                  for label in [*draft.zone_labels, *(str(item.get("label") or "") for item in zones)])
            if latest_selection is not None or explicit_zone_numbers(latest) or named_in_answer:
                scope_source = latest
                if latest_selection is None:
                    source_selection = None
                    draft = draft.model_copy(update={"scope_mode": "explicit"})
        by_label: dict[str, list[str]] = {}
        for item in zones:
            if not item.get("id") or not item.get("label"):
                continue
            key = str(item["label"]).casefold().replace("ё", "е")
            by_label.setdefault(key, []).append(str(item["id"]))
        zone_ids = []
        unknown_labels = []
        ambiguous_labels = []
        for label in draft.zone_labels:
            matches = by_label.get(label.casefold().replace("ё", "е"), [])
            if len(matches) == 1:
                zone_ids.append(matches[0])
            elif len(matches) > 1:
                ambiguous_labels.append(label)
            else:
                unknown_labels.append(label)
        explicit_numbers = explicit_zone_numbers(scope_source, source_turns=turns if scope_source == semantic_text else None)
        numbered_zones = {
            int(item["number"]): str(item["id"])
            for item in zones
            if item.get("number") is not None and str(item.get("number")).isdigit() and item.get("id")
        }
        missing_numbers = [number for number in explicit_numbers if number not in numbered_zones]
        if source_selection is not None and not explicit_numbers:
            # A model label is not the requested map selection. Retain only
            # labels actually present in the source for conflict detection.
            zone_ids = [str(item["id"]) for item in zones if item.get("id") and item.get("label")
                        and re.search(rf'(?<!\w){re.escape(str(item["label"]))}(?!\w)', scope_source, re.IGNORECASE)]
            unknown_labels, ambiguous_labels = [], []
            draft = draft.model_copy(update={"scope_mode": "selection"})
        # Source numbers outrank model-generated labels and scope guesses.
        # Otherwise a model can silently replace an existing explicit zone
        # with a different existing label, or call a missing number delegated.
        if explicit_numbers and zones:
            draft = draft.model_copy(update={"scope_mode": "explicit"})
            if not missing_numbers:
                source_zone_ids = [numbered_zones[number] for number in explicit_numbers]
                if zone_ids != source_zone_ids:
                    corrections.append("explicit_zone_ids:source_number")
                zone_ids = source_zone_ids
                unknown_labels = []
                ambiguous_labels = []
        source_read = compile_read_intent(text, draft.operation, source_turns=turns, zone_labels=[], object_ids=[])
        if source_read is not None:
            # A read's default is the project, not a model-selected subset.
            # Explicit source references are the only way to narrow it.
            named_ids = [str(item["id"]) for item in zones if item.get("id") and item.get("label")
                         and re.search(rf'(?<!\w){re.escape(str(item["label"]))}(?!\w)', scope_source, re.IGNORECASE)]
            source_ids = ([numbered_zones[number] for number in explicit_numbers if number in numbered_zones]
                          if explicit_numbers else named_ids)
            if zone_ids != source_ids:
                corrections.append("read_scope:source_only")
            zone_ids = source_ids
            unknown_labels, ambiguous_labels = [], []
            if not explicit_numbers:
                ambiguous_labels = [str(item["label"]) for item in zones if item.get("id") in named_ids
                                    and len(by_label.get(str(item["label"]).casefold().replace("ё", "е"), [])) > 1]
            has_scope_reference = bool(re.search(r'\b(?:участ\w*|зон\w*|област\w*|выбран\w*|выделен\w*)\b', text, re.IGNORECASE))
            draft = draft.model_copy(update={"scope_mode": "explicit" if zone_ids or (missing_numbers and zones) else
                                            "selection" if has_scope_reference else "project"})
        if draft.scope_mode == "explicit" and zones and (unknown_labels or ambiguous_labels or missing_numbers):
            available = [str(item.get("label")) for item in zones if item.get("label")]
            details = []
            if unknown_labels:
                details.append(f"не найдено: {', '.join(unknown_labels[:3])}")
            if ambiguous_labels:
                details.append(f"неоднозначно: {', '.join(ambiguous_labels[:3])}")
            if missing_numbers:
                details.append(f"номер {', '.join(map(str, missing_numbers[:3]))} отсутствует")
            visible = ", ".join(dict.fromkeys(available))
            raise ValueError(f"Не удалось точно определить участок: {'; '.join(details)}. Доступны: {visible}")
        # Model-generated IDs are not a map selection. Until selection context
        # has a typed transport, explicit objects must occur in the user source.
        # Bare numbers are quantities/zone numbers unless labelled as an ID.
        object_ids = []
        for identity in draft.object_ids:
            if not identity or not re.search(rf'(?<![\w-]){re.escape(identity)}(?![\w-])', scope_source, re.IGNORECASE):
                continue
            if identity.isdigit() and not re.search(rf'\b(?:id|ид|идентификатор)\s*[:=]?\s*{re.escape(identity)}\b', scope_source, re.IGNORECASE):
                continue
            object_ids.append(identity)
        if object_ids != draft.object_ids:
            corrections.append("explicit_object_ids:source_only")
        if source_selection is not None:
            draft = draft.model_copy(update={"scope_mode": "selection"})
        if source_read is not None and object_ids:
            draft = draft.model_copy(update={"scope_mode": "explicit"})
        if zone_ids and object_ids:
            raise ValueError("Укажите одну область изменения: участок целиком либо конкретные посадки.")
        species_ids = source_species_ids(text, source_turns=turns, proposed=draft.species_ids,
            zone_labels=[str(item["label"]) for item in zones if item.get("id") in zone_ids and item.get("label")], object_ids=object_ids)
        if species_ids != draft.species_ids:
            corrections.append("species_ids:source_only")
            draft = draft.model_copy(update={"species_ids": species_ids})
        def task_reference(fragment):
            return is_task_argument_reference(fragment, text=semantic_text, source_turns=turns, operation=draft.operation,
                zone_labels=[str(item["label"]) for item in zones if item.get("id") in zone_ids and item.get("label")],
                object_ids=object_ids)
        unresolved = list(dict.fromkeys([item for item in draft.unresolved_requirements if not task_reference(item)]
                                        + unknown_labels + ambiguous_labels))
        if unresolved != list(dict.fromkeys([*draft.unresolved_requirements, *unknown_labels, *ambiguous_labels])):
            corrections.append("requirements:source_task_arguments")
        valid_rules = {rule[0] for rule in CONSTRAINT_KINDS.values()}
        constraints = []
        for item in draft.hard_constraints:
            if item.source_text and task_reference(item.source_text):
                if "requirements:source_task_arguments" not in corrections:
                    corrections.append("requirements:source_task_arguments")
                continue
            # Only known policy bindings become hard rules.  Unknown model
            # classifications remain visible but cannot block investigation.
            if item.policy_owned or item.rule_id in valid_rules:
                constraints.append(item)
            elif item.source_text:
                unresolved.append(item.source_text)
        source_setbacks = extract_supported_setback_constraints(semantic_text)
        # The model may classify the same source phrase as both a known policy
        # and unresolved. Resolve only the closed, source-backed policy meaning;
        # arbitrary model requirements and source modifiers remain unresolved.
        if source_setbacks:
            remaining = [item for item in unresolved if not is_supported_setback_constraint(item)]
            if remaining != unresolved:
                corrections.append("requirements:bound_policy")
            unresolved = remaining
            if draft.operation == "place" and has_only_bound_placement_source(semantic_text, source_turns=turns,
                zone_labels=[str(item["label"]) for item in zones if item.get("id") in zone_ids and item.get("label")], object_ids=object_ids):
                # The entire source has supported typed meaning. Keep quoted
                # source requirements, but do not invent missing numeric
                # policy parameters from the model's explanatory prose.
                normalize = lambda value: re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip(" .,;:!?")
                remaining = [item for item in unresolved if normalize(item) in normalize(semantic_text)]
                if remaining != unresolved:
                    corrections.append("requirements:source_coverage")
                unresolved = remaining
        unresolved.extend(extract_unsupported_constraint_evidence(semantic_text))
        unresolved.extend(source_species_exclusions(semantic_text,
            zone_labels=[str(item["label"]) for item in zones if item.get("id") in zone_ids and item.get("label")], object_ids=object_ids))
        for source in source_setbacks:
            for rule_id in valid_rules:
                if not any(item.rule_id == rule_id and item.source_text == source for item in constraints):
                    constraints.append(HardConstraint(rule_id=rule_id, source_text=source, policy_owned=True))
        delegations = list(draft.delegations)
        if draft.scope_mode == "delegated" and not any(item.slot == "scope" for item in delegations):
            delegations.append(Delegation(slot="scope", strategy="best_evidence", reason="Пользователь делегировал выбор участка"))
        if "species" in signals.delegations and not any(item.slot == "species" for item in delegations):
            delegations.append(Delegation(slot="species", strategy="agent", reason="Пользователь делегировал выбор состава"))
        edit = None
        if draft.operation == "edit":
            action = explicit_edit_action(text, source_turns=turns)
            if action is not None:
                edit = EditIntent(action=action, **(explicit_move_vector(text, source_turns=turns) if action == "move" else {}))
                if draft.edit != edit:
                    corrections.append("edit:source_bound_parameters")
        read = compile_read_intent(text, draft.operation, source_turns=turns,
            zone_labels=[str(item["label"]) for item in zones if item.get("id") in zone_ids and item.get("label")],
            object_ids=object_ids)
        if read is not None and not read.unsupported_requirements:
            # The supported read vocabulary contains only the request, scope
            # and plant kinds. Model-authored rules/preferences are not part of
            # that source; preserve genuine qualifiers via ReadIntent instead.
            if constraints or unresolved or draft.preferences or draft.species_ids:
                corrections.append("read:source_bound_requirements")
            constraints, unresolved = [], []
            draft = draft.model_copy(update={"preferences": [], "species_ids": []})
        return AgentIntent(
            raw_text=text,
            source_turns=turns,
            goal=Goal(operation=draft.operation, target_count=draft.target_count,
                      acceptance=["preview_before_commit"] if draft.operation in {"place", "edit", "delete"} else []),
            scope_mode=draft.scope_mode,
            explicit_zone_ids=list(dict.fromkeys(zone_ids)),
            explicit_object_ids=object_ids,
            hard_constraints=constraints,
            unresolved_requirements=list(dict.fromkeys(unresolved)),
            preferences=draft.preferences,
            delegations=delegations,
            evidence=IntentEvidence(
                operation=list(signals.operations),
                plant_kind=list(signals.plant_kinds),
                arrangement=list(signals.arrangements),
                delegations=list(signals.delegations),
                corrections=corrections,
            ),
            arrangement=draft.arrangement,
            plant_kind=draft.plant_kind,
            species_ids=draft.species_ids,
            post_action=draft.post_action,
            edit=edit,
            read=read,
            selection_reference=source_selection,
        )
