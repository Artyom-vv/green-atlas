"""Домен проверки сетей: одно объяснение общих пробелов для чернового плана.

Индивидуальные трассы проверок сохраняются; общий текст не дублируется в посадках.
"""

from collections.abc import Iterable

from app.regulations.trace_contracts import PlantingRuleTrace
from app.shared.messages import has_message, message


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
        message(key if has_message(key) else "network.review.unfinished")
        for code in codes
        for key in (f"network.review.{code}",)
    ]
    return message("network.review.summary", {"reasons": "; ".join(labels)})
