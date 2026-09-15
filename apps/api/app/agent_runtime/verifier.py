"""Deterministic verification boundary for preview results."""

from typing import Any
from math import isclose

from pydantic import ValidationError

from app.agent_runtime.contracts import AgentIntent, PlacementOutcome, VerificationSummary
from app.agent_runtime.policy import PLAN_PREVIEW, assess_requirements
from app.agent_runtime.selection import bound_scope


def extract_change_set(data: Any, *, tool_name: str | None = None) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    if tool_name == "prepare_placement":
        proposal = data.get("proposal")
        if isinstance(proposal, dict) and isinstance(proposal.get("change_set"), dict):
            return proposal["change_set"]
    if isinstance(data.get("change_set"), dict):
        return data["change_set"]
    if {"id", "digest", "base_plan_version"}.issubset(data):
        return data
    return None


def verify_preview_data(data: Any, *, tool_name: str | None = None,
                        intent: AgentIntent | None = None,
                        zone_ids: list[str] | None = None) -> VerificationSummary:
    """Verify only facts required before a change-set can be approved."""
    change_set = extract_change_set(data, tool_name=tool_name)
    if change_set is None:
        return VerificationSummary(status="not_applicable")

    checks: list[str] = []
    if intent is not None:
        if PLAN_PREVIEW.get(intent.goal.operation) != tool_name:
            return VerificationSummary(status="rejected", message="Предложение не соответствует действию пользователя")
        requirements = assess_requirements(intent)
        if requirements.status == "unsupported":
            return VerificationSummary(status="rejected", message="Обязательные условия ещё не связаны с проверкой")
        if requirements.rule_ids:
            proposal = data.get("proposal") if isinstance(data.get("proposal"), dict) else data
            condition = data.get("condition_check") or proposal.get("condition_check") or {}
            bound_rules = {rule for binding in condition.get("bindings", []) for rule in binding.get("rule_ids", [])}
            checked_rules = {rule.get("rule_id") for rule in condition.get("rules", []) if rule.get("status") == "geometry_available"}
            if (set(data.get("condition_rule_ids") or []) != set(requirements.rule_ids)
                    or not set(requirements.rule_ids).issubset(bound_rules & checked_rules)):
                return VerificationSummary(status="rejected", message="В preview нет доказательства проверки обязательных условий")
            checks.append("required_conditions")
        checks.append("intent_operation")
        if (intent.scope_mode == "selection" and intent.selection_binding is not None
                and change_set.get("base_plan_version") != intent.selection_binding.plan_version):
            return VerificationSummary(status="rejected", message="Предложение относится к другой версии принятого выделения")
    if tool_name == "prepare_placement":
        try:
            outcome = PlacementOutcome.model_validate(data.get("placement_outcome"))
        except ValidationError:
            return VerificationSummary(status="rejected", message="У preview нет достоверного результата вместимости")
        if outcome.status != "exact":
            return VerificationSummary(status="rejected", message=outcome.reason[:500])
        additions = change_set.get("additions")
        actual_count = len(additions) if isinstance(additions, list) else change_set.get("additions_count")
        if actual_count != outcome.found:
            return VerificationSummary(status="rejected", message="Количество в preview не совпадает с проверенными позициями")
        if intent is not None and intent.goal.target_count is not None:
            target = intent.goal.target_count
            if outcome.requested != target or actual_count != target:
                return VerificationSummary(status="rejected", message="Количество в preview не соответствует поручению пользователя")
        checks.append("target_count")
        if (change_set.get("updates") or change_set.get("updates_count") or change_set.get("deletion_ids")
                or change_set.get("deletions_count")):
            return VerificationSummary(status="rejected", message="Предложение посадки содержит изменение существующих объектов")
        if intent is not None:
            if intent.arrangement is not None and data.get("arrangement") != intent.arrangement:
                return VerificationSummary(status="rejected", message="Схема размещения не соответствует поручению")
            if intent.plant_kind is not None:
                expected_kinds = {"tree", "shrub"} if intent.plant_kind == "mixed" else {intent.plant_kind}
                kinds = {item.get("kind") for item in additions} if isinstance(additions, list) else {
                    kind for kind, count in (data.get("kind_counts") or {}).items() if count
                }
                if data.get("plant_kind") != intent.plant_kind or kinds != expected_kinds:
                    return VerificationSummary(status="rejected", message="Состав посадки не соответствует поручению")
            if intent.species_ids:
                species = {item.get("species_revision_id") for item in additions} if isinstance(additions, list) else set(data.get("actual_species_revision_ids") or [])
                if species != set(intent.species_ids):
                    return VerificationSummary(status="rejected", message="Породы в preview не соответствуют поручению")
            checks.append("placement_choices")
        proposal = data.get("proposal") if isinstance(data.get("proposal"), dict) else data
        actual_zones = data.get("resolved_zone_ids") or proposal.get("resolved_zone_ids")
        expected_zones = (bound_scope(intent)[0] if intent is not None and intent.scope_mode in {"explicit", "selection"}
                          else zone_ids)
        if expected_zones and set(actual_zones or []) != set(expected_zones):
            return VerificationSummary(status="rejected", checks=checks, message="Участок в preview не соответствует поручению")
        if expected_zones and isinstance(additions, list) and any(
            item.get("planting_zone_id") not in expected_zones for item in additions
        ):
            return VerificationSummary(status="rejected", checks=checks, message="В preview есть посадки вне выбранного участка")
        checks.append("scope")
    if tool_name == "prepare_existing_change":
        operation = intent.goal.operation if intent is not None else data.get("operation")
        if operation not in {"edit", "delete"} or data.get("operation") != operation:
            return VerificationSummary(status="rejected", message="Тип изменения не соответствует поручению")
        additions_count = len(change_set["additions"]) if isinstance(change_set.get("additions"), list) else change_set.get("additions_count", 0)
        updates = change_set.get("updates")
        update_ids = [item.get("id") for item in updates] if isinstance(updates, list) else change_set.get("update_ids", [])
        deletion_ids = change_set.get("deletion_ids", [])
        if additions_count or (operation == "edit" and deletion_ids) or (operation == "delete" and update_ids):
            return VerificationSummary(status="rejected", message="Preview содержит действия вне поручения")
        affected = update_ids if operation == "edit" else deletion_ids
        target_ids = data.get("target_ids")
        if not affected or not isinstance(target_ids, list) or set(affected) != set(target_ids) or len(affected) != len(set(affected)):
            return VerificationSummary(status="rejected", message="Preview не подтверждает точный список затронутых объектов")
        if data.get("found") != len(affected) or data.get("requested") != len(affected) or data.get("shortfall") != 0:
            return VerificationSummary(status="rejected", message="Preview не охватывает запрошенное количество объектов")
        if intent is not None:
            if operation == "edit" and (intent.edit is None or data.get("edit_parameters") != intent.edit.model_dump(mode="json")):
                return VerificationSummary(status="rejected", message="Способ изменения или смещение не соответствует поручению")
            before = {item.get("id"): item for item in data.get("target_before", [])}
            if set(before) != set(affected):
                return VerificationSummary(status="rejected", message="Нет исходных фактов для затронутых объектов")
            if intent.plant_kind in {"tree", "shrub"} and any(item.get("kind") != intent.plant_kind for item in before.values()):
                return VerificationSummary(status="rejected", message="Тип затронутых растений не соответствует поручению")
            intent_zones, intent_objects = bound_scope(intent)
            if intent_zones and any(item.get("planting_zone_id") not in intent_zones for item in before.values()):
                return VerificationSummary(status="rejected", message="Изменение затрагивает посадки вне выбранного участка")
            species_filter = operation == "delete" or (intent.edit is not None and intent.edit.action != "species")
            if species_filter and intent.species_ids and any(item.get("species_revision_id") not in intent.species_ids for item in before.values()):
                return VerificationSummary(status="rejected", message="Изменение затрагивает другие породы")
            if operation == "edit":
                actual_updates = updates if isinstance(updates, list) else data.get("verified_updates", [])
                if {item.get("id") for item in actual_updates} != set(affected):
                    return VerificationSummary(status="rejected", message="Нет проверяемых фактов изменения")
                for item in actual_updates:
                    original = before[item["id"]]
                    if not _edit_matches(original, item, intent):
                        return VerificationSummary(status="rejected", message="Фактическое изменение не соответствует поручению")
            if intent.goal.target_count is not None and (len(affected) != intent.goal.target_count or data.get("quantity_mode") != "target"):
                return VerificationSummary(status="rejected", message="Количество изменений не соответствует поручению")
            if intent.scope_mode in {"explicit", "selection"}:
                if (set(data.get("resolved_zone_ids") or []) != set(intent_zones)
                        or set(data.get("resolved_object_ids") or []) != set(intent_objects)):
                    return VerificationSummary(status="rejected", message="Область изменения не соответствует поручению")
                if intent_objects and set(affected) != set(intent_objects):
                    return VerificationSummary(status="rejected", message="Изменены другие объекты вместо выбранных пользователем")
        checks.extend(["existing_targets", "existing_operation", "target_count"])
    if not isinstance(change_set.get("id"), str) or not change_set["id"].strip():
        return VerificationSummary(status="rejected", checks=checks, message="У preview нет идентификатора")
    checks.append("change_set_id")
    if not isinstance(change_set.get("digest"), str) or not change_set["digest"].strip():
        return VerificationSummary(status="rejected", checks=checks, message="У preview нет digest")
    checks.append("digest")
    if not isinstance(change_set.get("base_plan_version"), int) or change_set["base_plan_version"] < 1:
        return VerificationSummary(status="rejected", checks=checks, message="У preview некорректна версия плана")
    checks.append("base_plan_version")
    if change_set.get("can_apply") is not True:
        return VerificationSummary(status="rejected", checks=checks, message="Домен не разрешил применение preview")
    checks.append("can_apply")

    candidate_results = change_set.get("candidate_results")
    summary = change_set.get("candidate_summary")
    full_facts = isinstance(change_set.get("additions"), list) or isinstance(change_set.get("updates"), list)
    if isinstance(summary, dict) and not full_facts:
        total, allowed = summary.get("total"), summary.get("allowed")
        if not isinstance(total, int) or not isinstance(allowed, int) or total < 0 or allowed != total:
            return VerificationSummary(status="rejected", checks=checks, message="В preview есть непроверенные позиции")
        checks.append("candidate_results")
    elif isinstance(candidate_results, list):
        if any(not isinstance(item, dict) or item.get("status") not in {"allowed", "accepted"} for item in candidate_results):
            return VerificationSummary(status="rejected", checks=checks, message="В preview есть недопустимые позиции")
        checks.append("candidate_results")
    elif intent is not None:
        return VerificationSummary(status="rejected", checks=checks, message="В preview нет результатов проверки позиций")
    return VerificationSummary(status="verified", checks=checks)


def _edit_matches(before: dict, after: dict, intent: AgentIntent) -> bool:
    """Check editable fields independently of a tool's declared action."""
    edit = intent.edit
    if edit is None:
        return False
    expected = {key: before.get(key) for key in ("kind", "x", "y", "locked", "species_revision_id",
        "radius", "layout_radius_m", "size_class", "pattern_id", "group_ids", "spacing_policy")}
    if edit.action == "move":
        if not isinstance(before.get("x"), (int, float)) or not isinstance(before.get("y"), (int, float)):
            return False
        expected["x"] += edit.move_dx_m or 0
        expected["y"] += edit.move_dy_m or 0
    elif edit.action in {"lock", "unlock"}:
        expected["locked"] = edit.action == "lock"
    elif edit.action == "species":
        species = intent.species_ids
        if species and after.get("species_revision_id") not in species:
            return False
        if not after.get("species_revision_id"):
            return False
        expected["species_revision_id"] = after["species_revision_id"]
    for key, value in expected.items():
        if key in {"x", "y"} and isinstance(value, (int, float)) and isinstance(after.get(key), (int, float)):
            if not isclose(value, after[key], rel_tol=0, abs_tol=1e-7):
                return False
        elif after.get(key) != value:
            return False
    return True
