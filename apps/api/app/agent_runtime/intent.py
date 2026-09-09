"""Structured natural-language to intent compilation for the new runtime."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app import planning_assistant as local
from app.agent_runtime.contracts import AgentIntent, Delegation, Goal, HardConstraint, IntentEvidence, Preference
from app.geometry.domain import CONSTRAINT_KINDS
from app.agent_runtime.semantic_guardrails import (
    explicit_zone_numbers,
    infer_semantic_signals,
    reconcile_draft,
)


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


INTENT_PROMPT = """Разберите поручение проектировщика озеленения в IntentDraft.
Верните только JSON по схеме.

Это не анкета и не исполнитель. Не спрашивайте ничего и не объявляйте выполнение.
Разделяйте смысловые типы строго:
- target_count — количество, если пользователь его задал;
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

    def compile(self, text: str, *, project_context: dict[str, Any] | None = None) -> AgentIntent:
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
                    "project": project_context or {},
                    "policy_rule_ids": [rule[0] for rule in CONSTRAINT_KINDS.values()],
                }, ensure_ascii=False)},
            ],
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 900},
            "keep_alive": "10m",
        }, timeout=60)
        draft = IntentDraft.model_validate_json(response["message"]["content"])
        signals = infer_semantic_signals(text)
        draft, corrections = reconcile_draft(text, draft)
        zones = project_context.get("zones", []) if project_context else []
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
        explicit_numbers = explicit_zone_numbers(text)
        numbered_zones = {
            int(item["number"]): str(item["id"])
            for item in zones
            if item.get("number") is not None and str(item.get("number")).isdigit() and item.get("id")
        }
        missing_numbers = [number for number in explicit_numbers if number not in numbered_zones]
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
        unresolved = list(dict.fromkeys([*draft.unresolved_requirements, *unknown_labels, *ambiguous_labels]))
        valid_rules = {rule[0] for rule in CONSTRAINT_KINDS.values()}
        constraints = []
        for item in draft.hard_constraints:
            # Only known policy bindings become hard rules.  Unknown model
            # classifications remain visible but cannot block investigation.
            if item.policy_owned or item.rule_id in valid_rules:
                constraints.append(item)
            elif item.source_text:
                unresolved.append(item.source_text)
        delegations = list(draft.delegations)
        if draft.scope_mode == "delegated" and not any(item.slot == "scope" for item in delegations):
            delegations.append(Delegation(slot="scope", strategy="best_evidence", reason="Пользователь делегировал выбор участка"))
        if "species" in signals.delegations and not any(item.slot == "species" for item in delegations):
            delegations.append(Delegation(slot="species", strategy="agent", reason="Пользователь делегировал выбор состава"))
        return AgentIntent(
            raw_text=text,
            goal=Goal(operation=draft.operation, target_count=draft.target_count,
                      acceptance=["preview_before_commit"] if draft.operation in {"place", "edit", "delete"} else []),
            scope_mode=draft.scope_mode,
            explicit_zone_ids=list(dict.fromkeys(zone_ids)),
            explicit_object_ids=draft.object_ids,
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
        )
