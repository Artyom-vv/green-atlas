"""Explain why a native network distance can (or cannot) use a base rule.

Missing source facts, unsupported representations and an empty table cell are
different decisions. None of them is a zero setback or a native query failure.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.geometry.utility_contracts import (
    UtilityContext,
    UtilityGeometryReference as Reference,
    UtilityType,
)
from app.regulations.network_rules import NETWORK_RULE_BY_TYPE, NetworkRule


class UtilityReviewReason(StrEnum):
    CONTEXT_MISSING = "utility_context_missing"
    TYPE_UNKNOWN = "utility_type_unknown"
    INSTALLATION_UNKNOWN = "utility_installation_unknown"
    INSTALLATION_UNSUPPORTED = "utility_installation_unsupported"
    REFERENCE_UNKNOWN = "utility_reference_unknown"
    AXIS_EXTENT_UNKNOWN = "utility_axis_extent_unknown"
    REFERENCE_UNSUPPORTED = "utility_reference_unsupported"
    CONTEXT_UNCONFIRMED = "utility_context_unconfirmed"
    RULE_MISSING = "utility_rule_missing"
    NO_NUMERIC_SETBACK = "utility_no_numeric_setback"


REVIEW_LABELS = {
    UtilityReviewReason.CONTEXT_MISSING: "Не заданы характеристики сети",
    UtilityReviewReason.TYPE_UNKNOWN: "Не определён тип сети",
    UtilityReviewReason.INSTALLATION_UNKNOWN: "Не указан способ прокладки сети",
    UtilityReviewReason.INSTALLATION_UNSUPPORTED: "Для надземной сети нет подключённого правила",
    UtilityReviewReason.REFERENCE_UNKNOWN: "Не указан смысл линии сети: ось или наружный контур",
    UtilityReviewReason.AXIS_EXTENT_UNKNOWN: "Для оси сети не подтверждены наружные размеры",
    UtilityReviewReason.REFERENCE_UNSUPPORTED: "Смысл линии не соответствует правилу для этого типа сети",
    UtilityReviewReason.CONTEXT_UNCONFIRMED: "Характеристики сети не подтверждены источником",
    UtilityReviewReason.RULE_MISSING: "Для этого типа сети нет подключённого правила",
    UtilityReviewReason.NO_NUMERIC_SETBACK: "Для этого типа растения в выбранной таблице не указан численный отступ",
}


@dataclass(frozen=True)
class UtilityRequirement:
    rule: NetworkRule | None
    distance_m: float | None
    review_reasons: tuple[UtilityReviewReason, ...]

    @property
    def description(self) -> str:
        return "; ".join(REVIEW_LABELS[reason] for reason in self.review_reasons)


def utility_requirement(
    context: UtilityContext | None, plant_kind: str
) -> UtilityRequirement:
    if plant_kind not in {"tree", "shrub"}:
        raise ValueError("Неизвестный тип посадки")
    if context is None:
        return UtilityRequirement(None, None, (UtilityReviewReason.CONTEXT_MISSING,))

    reasons = []
    rule = NETWORK_RULE_BY_TYPE.get(context.network_type)
    if context.network_type == UtilityType.UNKNOWN:
        reasons.append(UtilityReviewReason.TYPE_UNKNOWN)
    elif rule is None:
        reasons.append(UtilityReviewReason.RULE_MISSING)
    if context.installation == "unknown":
        reasons.append(UtilityReviewReason.INSTALLATION_UNKNOWN)
    elif context.installation != "underground":
        reasons.append(UtilityReviewReason.INSTALLATION_UNSUPPORTED)
    reference = context.geometry_reference
    if reference == Reference.UNKNOWN:
        reasons.append(UtilityReviewReason.REFERENCE_UNKNOWN)
    elif reference == Reference.AXIS:
        # A native distance to an axis is not a distance to its outside surface.
        # Existing legacy axis bindings cannot establish native XREF identity.
        reasons.append(UtilityReviewReason.AXIS_EXTENT_UNKNOWN)
    elif reference not in (
        {Reference.OUTER_SURFACE, Reference.CHANNEL_WALL}
        if context.network_type == UtilityType.HEAT
        else {Reference.OUTER_SURFACE, Reference.PROTECTIVE_CASING}
    ):
        reasons.append(UtilityReviewReason.REFERENCE_UNSUPPORTED)
    if context.review_status != "confirmed":
        reasons.append(UtilityReviewReason.CONTEXT_UNCONFIRMED)

    distance = rule.distance(plant_kind) if rule else None
    if rule and distance is None:
        reasons.append(UtilityReviewReason.NO_NUMERIC_SETBACK)
    return UtilityRequirement(rule, None if reasons else distance, tuple(reasons))
