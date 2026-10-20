"""Ограниченный детерминированный отбор объектов для карты без изменения исходного
снимка.
"""

from array import array
from collections import Counter, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from heapq import heappush, heapreplace
from sys import getsizeof

from app.geometry.viewport.cancellation import check_viewport_cancelled

CONTEXT_KINDS = (
    "site_border",
    "allowed",
    "forbidden",
    "building",
    "road",
    "utility",
    "existing_green",
    "planting_area",
)
Candidate = tuple[int, dict]
GroupKey = tuple[str, str]
# Один обычный набор и одно окно дополнения без повторного полного обхода исходника.
VIEWPORT_CANDIDATE_WINDOW_FACTOR = 2
DEFAULT_SELECTION_SPOOL_BYTES = 16 * 1024 * 1024


def feature_group(feature: dict) -> GroupKey:
    properties = feature.get("properties", {})
    return (
        str(properties.get("kind", "source")),
        str(properties.get("source_layer", "")),
    )


def context_priority(kind: str) -> int:
    return 0 if kind in CONTEXT_KINDS else 1


def _kind_order(kind: str) -> tuple[int, int, str]:
    return (
        context_priority(kind),
        CONTEXT_KINDS.index(kind) if kind in CONTEXT_KINDS else 0,
        kind,
    )


def _schedule(counts: Counter[GroupKey], limit: int) -> Iterator[GroupKey]:
    """Чередовать виды и их слои без резервирования всех найденных объектов."""
    remaining = counts.copy()
    emitted = 0
    for priority in (0, 1):
        kinds = deque(
            sorted(
                {kind for kind, _ in counts if context_priority(kind) == priority},
                key=_kind_order,
            )
        )
        layers = {
            kind: deque(sorted(group for group in counts if group[0] == kind))
            for kind in kinds
        }
        while kinds and emitted < limit:
            kind = kinds.popleft()
            group = layers[kind].popleft()
            yield group
            emitted += 1
            remaining[group] -= 1
            if remaining[group]:
                layers[kind].append(group)
            if layers[kind]:
                kinds.append(kind)


@dataclass
class _RankedCandidate:
    key: tuple[str, int]
    candidate: Candidate

    def __lt__(self, other: "_RankedCandidate") -> bool:
        # Корень heap содержит худшего сохранённого кандидата и совместим с Python 3.11.
        return self.key > other.key


@dataclass(frozen=True)
class ViewportSelection:
    candidates: list[Candidate]
    total_by_kind: dict[str, int]
    total_matches: int


@dataclass
class SelectionWork:
    """Наблюдаемый объём работы; бюджетные байты не являются замером RSS."""

    metadata_passes: int = 0
    fallback_reads: int = 0
    spool_peak_bytes: int = 0


class _SelectionSpool:
    """Только ordinal, группа и строка ранга, без словарей входного JSON.

    Резерв на контейнеры консервативен: 40 байт на запись и 512 на группу
    покрывают over-allocation массивов, списков и индекса групп. Строки
    учитываются через getsizeof, даже если несколько записей делят одну строку.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.indexes = array("q")
        self.group_indexes = array("I")
        self.ranks: list[str] = []
        self.groups: list[GroupKey] = []
        self.group_ids: dict[GroupKey, int] = {}
        self.retained_bytes = 1024

    def append(self, index: int, group: GroupKey, rank: str) -> bool:
        group_id = self.group_ids.get(group)
        added = 40 + getsizeof(rank)
        if group_id is None:
            added += 512 + getsizeof(group[0]) + getsizeof(group[1])
        if self.retained_bytes + added > self.max_bytes:
            return False
        if group_id is None:
            group_id = len(self.groups)
            self.group_ids[group] = group_id
            self.groups.append(group)
        self.indexes.append(index)
        self.group_indexes.append(group_id)
        self.ranks.append(rank)
        self.retained_bytes += added
        return True

    def rows(self) -> Iterator[tuple[int, GroupKey, str]]:
        for index, group_index, rank in zip(
            self.indexes, self.group_indexes, self.ranks, strict=True
        ):
            check_viewport_cancelled()
            yield index, self.groups[group_index], rank


def select_compact_fair_candidates(
    candidates: Callable[[], Iterator[Candidate]],
    max_candidates: int,
    *,
    max_spool_bytes: int = DEFAULT_SELECTION_SPOOL_BYTES,
    work: SelectionWork | None = None,
) -> ViewportSelection:
    """Тот же отбор для JSON-метаданных дискового reader, без второго чтения.

    После ранжирования admission использует только группу кандидата. Полные
    Feature по-прежнему читает материализатор. Если компактные строки окна не
    помещаются в бюджет, освобождаем их и повторяем прежний проход ранжирования.
    """
    if max_spool_bytes < 0:
        raise ValueError("Selection spool budget must not be negative")
    work = work if work is not None else SelectionWork()
    work.metadata_passes += 1
    counts: Counter[GroupKey] = Counter()
    totals: Counter[str] = Counter()
    spool: _SelectionSpool | None = _SelectionSpool(max_spool_bytes)
    for index, feature in candidates():
        check_viewport_cancelled()
        group = feature_group(feature)
        counts[group] += 1
        totals[group[0]] += 1
        if spool is not None:
            identity = feature.get(
                "id", feature.get("properties", {}).get("source_handle", "")
            )
            if spool.append(index, group, str(identity)):
                work.spool_peak_bytes = max(work.spool_peak_bytes, spool.retained_bytes)
            else:
                spool = None
    schedule = list(_schedule(counts, max_candidates))
    quotas = Counter(schedule)
    group_features = {
        group: {"properties": {"kind": group[0], "source_layer": group[1]}}
        for group in quotas
    }

    def reread() -> Iterator[tuple[int, GroupKey, str]]:
        work.metadata_passes += 1
        work.fallback_reads += 1
        for index, feature in candidates():
            check_viewport_cancelled()
            group = feature_group(feature)
            if quotas[group]:
                identity = feature.get(
                    "id", feature.get("properties", {}).get("source_handle", "")
                )
                yield index, group, str(identity)

    heaps: dict[GroupKey, list[_RankedCandidate]] = {}
    rows = spool.rows() if spool is not None else reread()
    for index, group, rank in rows:
        quota = quotas[group]
        if not quota:
            continue
        heap = heaps.setdefault(group, [])
        key = (rank, index)
        if len(heap) < quota:
            heappush(heap, _RankedCandidate(key, (index, group_features[group])))
        elif key < heap[0].key:
            heapreplace(heap, _RankedCandidate(key, (index, group_features[group])))
    queues = {
        group: deque(item.candidate for item in sorted(heap, key=lambda item: item.key))
        for group, heap in heaps.items()
    }
    check_viewport_cancelled()
    return ViewportSelection(
        [queues[group].popleft() for group in schedule],
        dict(totals),
        sum(totals.values()),
    )


def select_fair_candidates(
    candidates: Callable[[], Iterator[Candidate]], max_candidates: int
) -> ViewportSelection:
    counts: Counter[GroupKey] = Counter()
    totals: Counter[str] = Counter()
    for _, feature in candidates():
        group = feature_group(feature)
        counts[group] += 1
        totals[group[0]] += 1
    schedule = list(_schedule(counts, max_candidates))
    quotas = Counter(schedule)
    heaps: dict[GroupKey, list[_RankedCandidate]] = {}
    for candidate in candidates():
        index, feature = candidate
        group = feature_group(feature)
        quota = quotas[group]
        if not quota:
            continue
        identity = feature.get(
            "id", feature.get("properties", {}).get("source_handle", "")
        )
        ranked = _RankedCandidate((str(identity), index), candidate)
        heap = heaps.setdefault(group, [])
        if len(heap) < quota:
            heappush(heap, ranked)
        elif ranked.key < heap[0].key:
            heapreplace(heap, ranked)
    queues = {
        group: deque(item.candidate for item in sorted(heap, key=lambda item: item.key))
        for group, heap in heaps.items()
    }
    return ViewportSelection(
        [queues[group].popleft() for group in schedule],
        dict(totals),
        sum(totals.values()),
    )
