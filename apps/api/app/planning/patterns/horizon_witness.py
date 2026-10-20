"""Ограниченный запас координат для сравнения одного запроса по годам.

Это подсказка поиска, а не кеш допуска: каждую прежнюю позицию снова проверяет
обычный оценщик. Здесь нет снимков проекта, геометрии или трасс проверок.
"""

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
from struct import iter_unpack, pack
from threading import RLock

from app.planning.config import MAX_PATTERN_CANDIDATES
from app.planning.patterns.contracts import (
    FillPatternRequest,
    PatternPreview,
    PatternPreviewRequest,
)
from app.planning.ports import PatternCandidate
from app.projects.contracts import Project

_Basis = tuple[str, int, int, str]


@dataclass(frozen=True)
class _Witness:
    coordinates: bytes

    @property
    def count(self) -> int:
        return len(self.coordinates) // 16


class HorizonWitnesses:
    """Хранит не больше 80 КБ координат суммарно, только до закрытия процесса."""

    def __init__(
        self, *, max_positions: int = MAX_PATTERN_CANDIDATES, max_entries: int = 8
    ) -> None:
        if max_positions < 1 or max_entries < 1:
            raise ValueError("Лимиты координат и записей должны быть положительными")
        self.max_positions = max_positions
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[_Basis, int], _Witness] = OrderedDict()
        self._position_count = 0
        self._lock = RLock()

    @staticmethod
    def _basis(project: Project, request: PatternPreviewRequest) -> _Basis | None:
        # В смешанной композиции порода зависит от исходного индекса кандидата;
        # перенос только координат изменил бы состав уже проверенной раскладки.
        if (
            not isinstance(request, FillPatternRequest)
            or request.composition == "mixed"
            # Естественный поиск строит согласованную семью с первого запуска.
            # В фиксированной сетке прежний шаг не заменяет текущий запрос.
            or request.layout == "natural"
            or request.placement_mode != "count"
        ):
            return None
        parameters = request.model_dump_json(exclude={"scenario_horizon_year"})
        return (
            project.id,
            project.state_version,
            project.geometry_version,
            sha256(parameters.encode()).hexdigest(),
        )

    def better_positions(
        self, project: Project, request: PatternPreviewRequest, accepted_count: int
    ) -> list[PatternCandidate]:
        basis = self._basis(project, request)
        if basis is None or project.plan is None:
            return []
        year = project.plan.planning_horizon_year
        with self._lock:
            eligible = [
                (key, value)
                for key, value in self._entries.items()
                if key[0] == basis and key[1] > year and value.count > accepted_count
            ]
            if not eligible:
                return []
            key, witness = max(eligible, key=lambda item: item[1].count)
            self._entries.move_to_end(key)
        return [
            PatternCandidate(x, y) for x, y in iter_unpack("dd", witness.coordinates)
        ]

    def remember(
        self, project: Project, request: PatternPreviewRequest, preview: PatternPreview
    ) -> None:
        basis = self._basis(project, request)
        change_set = preview.change_set
        if (
            basis is None
            or project.plan is None
            or change_set is None
            or not change_set.can_apply
            or not change_set.additions
            or len(change_set.additions) > self.max_positions
        ):
            return
        witness = _Witness(
            b"".join(pack("dd", item.x, item.y) for item in change_set.additions)
        )
        key = (basis, project.plan.planning_horizon_year)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None and existing.count >= witness.count:
                self._entries.move_to_end(key)
                return
            obsolete = [
                old
                for old in self._entries
                if old == key or (old[0][0] == basis[0] and old[0][1:3] != basis[1:3])
            ]
            for old in obsolete:
                self._position_count -= self._entries.pop(old).count
            self._entries[key] = witness
            self._position_count += witness.count
            while (
                self._position_count > self.max_positions
                or len(self._entries) > self.max_entries
            ):
                _, removed = self._entries.popitem(last=False)
                self._position_count -= removed.count
