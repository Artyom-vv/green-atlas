"""Shared intent policy for discovery, direct tools and saved-preview approval."""

from app.agent_conditions import is_supported_setback_constraint
from app.agent_runtime.contracts import AgentIntent, RequirementAssessment
from app.geometry.domain import CONSTRAINT_KINDS
from app.agent_runtime.selection import selection_problem


PLAN_OPERATIONS = frozenset({"place", "edit", "delete"})
PLAN_PREVIEW = {"place": "prepare_placement", "edit": "prepare_existing_change", "delete": "prepare_existing_change"}


def capability_allowed(operation: str, name: str, effect: str) -> bool:
    if effect == "control":
        return operation == "inspect" and name == "focus_zone"
    if effect == "read":
        return True
    if effect == "write":
        return (operation in PLAN_OPERATIONS and name == "commit_change_set") or (operation == "zones" and name == "commit_zone_change")
    if effect == "preview":
        return name == PLAN_PREVIEW.get(operation) or (operation == "zones" and name == "prepare_zone_change")
    return False


def assess_requirements(intent: AgentIntent) -> RequirementAssessment:
    known = {rule[0] for rule in CONSTRAINT_KINDS.values()}
    unsupported = list(intent.unresolved_requirements)
    supported = []
    if intent.control is not None:
        unsupported.extend(intent.control.unsupported_requirements)
        if intent.control.zone_id is None:
            unsupported.append("Укажите точный ID, номер или уникальное название одного участка")
        if (intent.goal.operation != "inspect" or intent.goal.target_count is not None
                or intent.scope_mode != "explicit"
                or intent.explicit_zone_ids != ([intent.control.zone_id] if intent.control.zone_id else [])
                or intent.explicit_object_ids or intent.selection_reference is not None or intent.selection_binding is not None
                or intent.edit is not None or intent.read is not None or intent.zone is not None
                or intent.plant_kind or intent.species_ids or intent.arrangement or intent.hard_constraints
                or intent.preferences or intent.delegations or intent.post_action):
            unsupported.append("Показ участка принимает только один явный участок и не выполняет другие действия")
    if intent.goal.operation == "zones":
        if intent.zone is None:
            unsupported.append("Укажите одно проверяемое действие с участком и его точную область")
        if intent.goal.target_count not in {None, 1}:
            unsupported.append("Для изменения участков укажите одно действие с одним участком")
        if intent.plant_kind or intent.species_ids or intent.arrangement or intent.edit is not None or intent.read is not None:
            unsupported.append("Изменение участка не может одновременно менять посадки или выполнять отдельную проверку")
        if intent.hard_constraints:
            unsupported.append("Уточните условия изменения участка; условия посадки нельзя считать проверкой нового контура")
        if intent.scope_mode in {"selection", "delegated"}:
            unsupported.append("Для изменения участка укажите точный ID или название одного участка")
    if intent.scope_mode == "selection":
        problem = selection_problem(intent)
        if problem:
            unsupported.append(problem.message)
        elif intent.goal.operation == "place" and intent.selection_binding.object_ids:
            unsupported.append("Для новой посадки выделите участки, а не существующие растения")
    if intent.goal.operation == "edit":
        if intent.edit is None:
            unsupported.append("Укажите одно изменение: перемещение, замену породы, закрепление или снятие закрепления")
        elif intent.edit.action == "move" and not (intent.edit.move_dx_m or intent.edit.move_dy_m):
            unsupported.append("Укажите ненулевое смещение по осям чертежа X и Y в метрах")
        elif intent.edit.action == "species" and len(intent.species_ids) > 1:
            unsupported.append("Для замены породы укажите одну новую породу")
        elif intent.edit.action == "species" and not intent.species_ids and not any(item.slot == "species" for item in intent.delegations):
            unsupported.append("Укажите новую породу или поручите её подбор агенту")
    for constraint in intent.hard_constraints:
        if (constraint.rule_id not in known or
                (constraint.source_text and not is_supported_setback_constraint(constraint.source_text))):
            unsupported.append(constraint.source_text or constraint.rule_id)
        else:
            supported.append(constraint.rule_id)
    return RequirementAssessment(
        status="unsupported" if unsupported else "supported",
        rule_ids=sorted(set(supported)), unresolved=list(dict.fromkeys(unsupported))[:100],
    )
