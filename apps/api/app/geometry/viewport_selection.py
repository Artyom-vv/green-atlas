"""Bounded, deterministic display selection; never changes source geometry."""

from collections import Counter, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from heapq import heappush, heapreplace

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
# One ordinary window and one refill window; no repeated full-source scans.
VIEWPORT_CANDIDATE_WINDOW_FACTOR = 2


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
    """Alternate kinds, then layers within each kind, without reserving all hits."""
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
        # The heap root is the worst retained candidate, compatible with 3.11.
        return self.key > other.key


@dataclass(frozen=True)
class ViewportSelection:
    candidates: list[Candidate]
    total_by_kind: dict[str, int]
    total_matches: int


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
