"""Human explanations of native measurements, without inventing CAD semantics."""
from __future__ import annotations

from math import isfinite


def measured_distance(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) and value >= 0 else None


def blocked_description(answer: dict) -> str:
    reason = answer.get("reason")
    layer = answer.get("source_layer")
    source = f" в слое «{layer}»" if layer else ""
    if reason == "outside_site":
        return "Позиция за границей территории"
    if reason == "native_occupied":
        return f"Позиция внутри запрещённой области{source}"
    if reason in {"native_clearance", "native_curve_clearance"}:
        actual = measured_distance(answer.get("nearest_blocked_distance"))
        required = measured_distance(answer.get("required_clearance"))
        if actual is not None and required is not None:
            values = f"{actual:.3f} м при заданном {required:.3f} м".replace(".", ",")
            return f"Недостаточный отступ{source}: {values}"
        return f"Не выдержан отступ{source} — измерения недоступны"
    return f"Не пройдена проверка ограничений{source}"


def advisory_description(answer: dict) -> str:
    if answer.get("reason") == "site_not_found":
        return "Граница территории не подтверждена"
    count = answer.get("local_unknown", 0)
    if answer.get("result") == "unknown" or count > 0:
        return f"Непроверенных объектов в этой позиции: {count}" if count > 0 else "Геометрия позиции не подтверждена"
    return "Черновая позиция — нормативная проверка не выполнена"
