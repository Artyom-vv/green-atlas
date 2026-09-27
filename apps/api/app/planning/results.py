from __future__ import annotations

from collections import Counter

from app.planning.pattern_contracts import (
    CandidateReasonSummary,
    PatternSkippedCandidate,
)
from app.planning.types import CandidateCategory, RejectedCategory


def summarize_skips(
    skipped: list[PatternSkippedCandidate],
) -> list[CandidateReasonSummary]:
    counts = Counter(
        (item.status, item.code, item.category, item.reason) for item in skipped
    )
    return [
        CandidateReasonSummary(
            status=status, code=code, category=category, count=count, message=message
        )
        for (status, code, category, message), count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0][1], item[0][3]),
        )
    ]


def rejected_category(category: CandidateCategory) -> RejectedCategory:
    if category == "accepted":
        raise ValueError("Accepted candidate cannot describe a rejected position")
    return category
