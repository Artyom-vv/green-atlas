"""Local model adapter that chooses the next typed agent decision."""

import json
from typing import Any

from app import planning_assistant as local
from app.agent_runtime.contracts import AgentDecision, AgentIntent
from app.agent_runtime.registry import CapabilityRegistry
from app.agent_runtime.selection import bound_scope
from pydantic import ValidationError


PLANNER_PROMPT = """Вы — planner автономного агента проектирования озеленения.
Верните только JSON AgentDecision. Это внутренний шаг run, а не ответ пользователю.

Работайте по фактам из context и после каждого tool result выбирайте следующий шаг.
Не спрашивайте о нормативах: обязательные правила принадлежат серверу. Не утверждайте
успех без фактического результата инструмента. Если в intent есть делегация scope,
сначала вызовите find_zone_candidates, затем выберите одну доказанную зону и прочитайте
её через inspect_zones. Не используйте всю выделенную область автоматически.
Для делегации породы сначала прочитайте species_shortlist. Для размещения вдоль зданий
прочитайте building_targets. Для итогового задания place используйте prepare_placement:
он передаёт структурированные решения в доменный расчётный движок и возвращает единый
проверяемый proposal; не собирайте для этого отдельные координаты и не подменяйте его
сырыми preview_* вызовами. Preview — только предложение, не применение. Approval можно
вернуть лишь для успешного preview; finish — только с outcome_ref. Если инструмент
вернул ошибку, измените следующий шаг по её коду, а не повторяйте тот же вызов без новой
информации. Если preview вернул found=0, shortfall>0 или reason_summary, это объяснение
результата, а не повод повторить тот же вызов: выберите другую доказанную породу,
схему или допустимый участок, сохранив явно заданные требования. Если runtime вернул
REPEATED_TOOL_CALL, следующий tool call обязан отличаться именем или аргументами.
Ориентируйтесь на цель, а не на фиксированный порядок инструментов.
Количество target_count и quantity_mode=target берите из run.intent.goal, если
пользователь задал количество. Никогда не уменьшайте цель до найденной вместимости.
placement_outcome=partial/impossible означает, что задание не выполнено: approval
недоступен. Runtime проверяет до трёх ранжированных участков при делегации scope;
явно выбранный участок и обязательные требования не меняются без ответа пользователя.
Для prepare_placement используйте run.plan_version, а не run.snapshot_version:
первая — версия плана для base_plan_version, вторая — версия состояния проекта.
species_revision_ids — это строки id из результата species_shortlist; не передавайте
индексы вроде 0, 1 или 2. Если порода делегирована и id не выбран, оставьте список пустым:
доменный расчёт выберет доступную породу автоматически.
Если intent.plant_kind=mixed, shortlist читайте без kind или с kind=null. При делегации
подбора в prepare_placement оставьте species_revision_ids пустым: domain сам выберет
одну доступную tree и одну shrub. Передача только части состава недопустима.
Для intent.operation=edit или delete используйте только prepare_existing_change. Сначала
прочитайте необходимые посадки или проект, затем передайте либо zone_ids, либо object_ids,
но не оба. Удаление всегда представляется preview и ждёт approval; не используйте сырые
preview_changes и не выбирайте объекты по позиции в списке. Для edit укажите edit_action;
для species передайте одну породу, для move реальные dx/dy.
При explicit scope берите zone_ids и object_ids из run.intent без замены зоны найденными
объектами. Доменный инструмент сам проверяет точное количество подходящих посадок;
не выбирайте произвольную часть результатов find_plantings.
Не раскрывайте chain-of-thought; в decision укажите только действие и параметры."""


def _bind_existing_change_scope(decision: AgentDecision, context: dict[str, Any]) -> AgentDecision:
    """Keep source-bound explicit scope intact across model tool selection.

    A discovered object list cannot replace the user's whole zone or choose a
    subset of explicit objects. The domain still checks the requested count,
    filters, object existence and all actual changes through the gateway.
    """
    if decision.action != "tool" or decision.tool.name != "prepare_existing_change":
        return decision
    run = context.get("run")
    if not isinstance(run, dict):
        return decision
    try:
        intent = AgentIntent.model_validate(run.get("intent"))
    except ValidationError:
        return decision
    zones, objects = bound_scope(intent)
    if (intent.goal.operation not in {"edit", "delete"} or intent.scope_mode not in {"explicit", "selection"}
            or bool(zones) == bool(objects)):
        return decision
    arguments = {
        **decision.tool.arguments,
        "zone_ids": zones,
        "object_ids": objects,
    }
    return decision.model_copy(update={"tool": decision.tool.model_copy(update={"arguments": arguments})})


class LocalPlanner:
    def __init__(self, model: str, registry: CapabilityRegistry | None = None):
        if not model:
            raise ValueError("Local model is required")
        self.model = model
        self.registry = registry or CapabilityRegistry()

    def __call__(self, context: dict[str, Any]) -> AgentDecision:
        schema = AgentDecision.model_json_schema()
        schema["required"] = ["action"]
        tool_schema = schema.get("$defs", {}).get("ToolCall")
        if tool_schema:
            names = context.get("allowed_capability_names") or self.registry.names()
            tool_schema.setdefault("properties", {}).setdefault("name", {})["enum"] = list(names)
        response = local.local_json("chat", {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": schema,
            "messages": [
                {"role": "system", "content": PLANNER_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 700},
            "keep_alive": "10m",
        }, timeout=60)
        content = response["message"]["content"]
        try:
            decision = AgentDecision.model_validate_json(content)
        except ValidationError as error:
            # Ollama supports only a conservative JSON-schema subset. Keep
            # the transport schema portable and spend one corrective turn only
            # when the model violates the typed decision contract.
            repair_schema = {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["tool", "ask", "approval", "finish", "wait"]},
                    "tool": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": list(context.get("allowed_capability_names") or self.registry.names())},
                            "arguments": {"type": "object"},
                        },
                        "required": ["name"],
                        "additionalProperties": False,
                    },
                    "question": {"type": ["string", "null"]},
                    "missing_slot": {"type": ["string", "null"]},
                    "preview_ref": {"type": ["string", "null"]},
                    "outcome_ref": {"type": ["string", "null"]},
                    "job_ref": {"type": ["string", "null"]},
                    "reason": {"type": ["string", "null"]},
                },
                "required": ["action"],
                "additionalProperties": False,
            }
            repair = local.local_json("chat", {
                "model": self.model,
                "stream": False,
                "think": False,
                "format": repair_schema,
                "messages": [
                    {"role": "system", "content": PLANNER_PROMPT},
                    {"role": "user", "content": json.dumps({
                        "context": context,
                        "invalid_decision": content[:2000],
                        "validation_error": str(error)[:800],
                        "correction": "Верните одно действие с обязательным payload: для action=tool добавьте tool.name и arguments.",
                    }, ensure_ascii=False)},
                ],
                "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 700},
                "keep_alive": "10m",
            }, timeout=60)
            decision = AgentDecision.model_validate_json(repair["message"]["content"])
        return _bind_existing_change_scope(decision, context)
