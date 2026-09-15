from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkProgress:
    """Observable work reported by an adapter.

    ``fraction`` is absent while a native atomic operation cannot expose partial
    completion. Counters always describe the current stage, not a prediction.
    """

    stage: str
    fraction: float | None = None
    processed: int | None = None
    total: int | None = None
    unit: str | None = None


ProgressReporter = Callable[[WorkProgress], None]


class OperationCancelled(RuntimeError):
    """Control-flow signal raised by a progress reporter after user cancellation."""
