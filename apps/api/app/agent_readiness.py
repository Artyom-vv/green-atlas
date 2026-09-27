"""One necessary question from retained state, not repeated model guesswork."""
from app.agent_memory import TaskState
from app.agent_conditions import is_supported_setback_constraint


def next_question(task: TaskState) -> str | None:
    values = task.values
    if values.operation not in {"place", "edit", "delete"}:
        return None
    if values.scope is None or (
        values.scope == "zones" and not values.zone_ids and values.spatial_anchor != "edge"
    ) or (values.scope == "objects" and not values.object_ids):
        return "На каком участке выполнить посадку?" if values.operation == "place" else "Какие посадки изменить? Укажите участок или выберите их на карте."
    if values.scope == "selection":
        return "Выберите участок или посадки на карте для этого задания."
    if values.operation == "edit" and values.edit_action in {"lock", "unlock"}:
        return None
    if values.operation == "edit" and values.edit_action == "move":
        if values.move_dx_m is None and values.move_dy_m is None:
            return "На какое расстояние и вдоль какой оси чертежа переместить посадки?"
        return None
    if values.operation == "place":
        if values.scope == "objects":
            return "На каком участке разместить новые растения?"
        if values.plant_kind is None:
            return "Необходимо посадить деревья или кустарники?"
        if values.arrangement is None:
            return "Как разместить растения: по площади или вдоль линии?"
        if values.quantity is None and values.quantity_mode != "fill_available":
            return "Сколько растений необходимо разместить?"
    if values.operation in {"place", "edit"} and values.species_mode != "automatic" and not values.species_revision_ids:
        return "Какую породу использовать? Можно поручить подбор помощнику."
    if values.operation == "place" and any(not is_supported_setback_constraint(item) for item in values.constraints or []):
        return "Уточните ограничение для расчёта на карте."
    return None
