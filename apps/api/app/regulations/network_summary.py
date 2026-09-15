"""One draft-level limitation instead of repeating it for every planting."""

from collections.abc import Iterable

from app.regulations.trace_contracts import PlantingRuleTrace

NETWORK_REVIEW_LABELS = {
    "NO_NETWORK_FEATURES": "сети отсутствуют в модели",
    "NETWORK_CONTEXT_UNKNOWN": "тип сети не подтверждён",
    "NETWORK_SOURCE_UNCONFIRMED": "источник сети не подтверждён",
    "NETWORK_INSTALLATION_UNSUPPORTED": "способ прокладки не охвачен правилами",
    "NETWORK_AXIS_EXTENT_UNKNOWN": "наружный размер сети неизвестен",
    "NETWORK_REFERENCE_UNSUPPORTED": "способ измерения не подтверждён",
    "NETWORK_FOOTPRINT_UNCONFIRMED": "занятый сетью контур не подтверждён",
    "NETWORK_SHRUB_DISTANCE_UNSPECIFIED": "отступ кустарника не установлен",
    "NETWORK_CROWN_CLEARANCE_UNRESOLVED": "увеличение отступа для кроны не установлено",
    "NETWORK_SOURCE_INCOMPLETE": "исходные сети покрыты не полностью",
    "GEOMETRY_NOT_READY": "расчётная геометрия не готова",
}


def network_review_reason(traces: Iterable[PlantingRuleTrace | None]) -> str | None:
    codes = sorted(
        {
            entry.code
            for trace in traces
            if trace is not None
            for entry in trace.entries
            if entry.obstacle_kind == "utility" and entry.status == "not_checked"
        }
    )
    if not codes:
        return None
    labels = [
        NETWORK_REVIEW_LABELS.get(code, "проверка не завершена") for code in codes
    ]
    return "Инженерные сети: " + "; ".join(labels) + ". Доступен черновик."
