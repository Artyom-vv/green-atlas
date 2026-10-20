"""Пространственно разнообразная очередь проб; место резервируют только принятые
позиции, отклонённые не занимают соседство.
"""

from typing import Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from app.planning.changes.contracts import PlanObjectAddOperation

SearchStopReason = Literal[
    "target_reached", "candidate_limit", "time_limit", "domain_exhausted"
]


class CandidateSearchQueue(Protocol):
    """Подаёт индексы операций и получает только подтверждённые посадки.

    Поток может вычислять следующие координаты после принятия предыдущих.
    Исчерпание потока не является доказательством вместимости территории.
    """

    @property
    def stop_reason(self) -> SearchStopReason: ...

    def __bool__(self) -> bool: ...

    def batch(self, limit: int) -> list[int]: ...

    def accept(self, x: float, y: float, *, zone_id: str | None = None) -> int: ...


class CandidateQueue:
    stop_reason: SearchStopReason = "candidate_limit"

    def __init__(
        self, operations: list[PlanObjectAddOperation], spacing_m: float
    ) -> None:
        self.points: NDArray[np.float64] = np.asarray(
            [(op.object.x, op.object.y) for op in operations], dtype=float
        )
        self.alive: NDArray[np.bool_] = np.ones(len(operations), dtype=bool)
        self.clearance: NDArray[np.float64] = np.full(len(operations), np.inf)
        self.spacing_squared = spacing_m**2

    def __bool__(self) -> bool:
        return bool(self.alive.any())

    def batch(self, limit: int) -> list[int]:
        available = self.alive.copy()
        clearance = self.clearance.copy()
        result: list[int] = []
        while available.any() and len(result) < limit:
            eligible = np.flatnonzero(available)
            index = int(eligible[np.argmax(clearance[eligible])])
            result.append(index)
            self.alive[index] = False
            distance = np.square(self.points - self.points[index]).sum(axis=1)
            clearance = np.minimum(clearance, distance)
            available &= distance >= self.spacing_squared
            available[index] = False
        return result

    def accept(self, x: float, y: float, *, zone_id: str | None = None) -> int:
        distance = np.square(self.points - (x, y)).sum(axis=1)
        self.clearance = np.minimum(self.clearance, distance)
        covered = self.alive & (distance < self.spacing_squared)
        count = int(covered.sum())
        self.alive[covered] = False
        return count
