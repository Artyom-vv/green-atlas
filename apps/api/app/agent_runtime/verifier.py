"""Deterministic verification boundary for preview results."""

from typing import Any

from app.agent_runtime.contracts import VerificationSummary


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


def verify_preview_data(data: Any, *, tool_name: str | None = None) -> VerificationSummary:
    """Verify only facts required before a change-set can be approved."""
    change_set = extract_change_set(data, tool_name=tool_name)
    if change_set is None:
        return VerificationSummary(status="not_applicable")

    checks: list[str] = []
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
    if isinstance(candidate_results, list):
        invalid = [item for item in candidate_results
                   if isinstance(item, dict) and item.get("status") not in {"allowed", "accepted"}]
        if invalid:
            return VerificationSummary(
                status="rejected",
                checks=checks,
                message="В preview есть недопустимые позиции",
            )
        checks.append("candidate_results")
    return VerificationSummary(status="verified", checks=checks)
