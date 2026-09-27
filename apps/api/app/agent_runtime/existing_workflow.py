"""A resolved existing-object task needs one canonical domain preview."""
from app.agent_runtime.selection import bound_scope, selection_problem


def existing_arguments(intent, plan_version):
    if intent.goal.operation not in {"edit", "delete"} or intent.scope_mode not in {"explicit", "selection"} or plan_version is None:
        return None
    zones, objects = bound_scope(intent)
    if bool(zones) == bool(objects) or selection_problem(intent):
        return None
    edit = intent.edit if intent.goal.operation == "edit" else None
    if intent.goal.operation == "edit" and (edit is None or (edit.action == "move" and not (edit.move_dx_m or edit.move_dy_m))
            or (edit.action == "species" and len(intent.species_ids) != 1)):
        return None
    return {"base_plan_version": plan_version, "operation": intent.goal.operation,
        "zone_ids": zones, "object_ids": objects, "plant_kind": intent.plant_kind,
        "species_revision_ids": intent.species_ids, "quantity": intent.goal.target_count, "quantity_mode": "target",
        "edit_action": edit.action if edit else None,
        "move_dx_m": edit.move_dx_m if edit else None, "move_dy_m": edit.move_dy_m if edit else None}
