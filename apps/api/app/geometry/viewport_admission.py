"""Share display resources before lending unused capacity to whole features."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from app.geometry.viewport_budget import ViewportBudget
from app.geometry.viewport_selection import Candidate, context_priority, feature_group


@dataclass(frozen=True)
class PreparedViewportFeature:
    index: int
    feature: dict
    size: tuple[int, int]


@dataclass
class _Usage:
    coordinates: int = 0
    bytes: int = 0

    def fits(self, size: tuple[int, int], coordinates: int, bytes_: int) -> bool:
        return (
            self.coordinates + size[0] <= coordinates
            and self.bytes + size[1] + 1 <= bytes_
        )

    def add(self, size: tuple[int, int]) -> None:
        self.coordinates += size[0]
        self.bytes += size[1] + 1


def admit_fair_features(
    candidates: list[Candidate],
    budget: ViewportBudget,
    prepare: Callable[[Candidate], PreparedViewportFeature],
    max_features: int,
) -> tuple[list[PreparedViewportFeature], set[str]]:
    selected: list[PreparedViewportFeature] = []
    reasons: set[str] = set()
    for start in range(0, len(candidates), max_features):
        if len(selected) == max_features:
            break
        admitted, rejected = _admit_window(
            candidates[start : start + max_features],
            budget,
            prepare,
            max_features - len(selected),
        )
        selected.extend(admitted)
        reasons.update(rejected)
    return selected, reasons


def _admit_window(
    candidates: list[Candidate],
    budget: ViewportBudget,
    prepare: Callable[[Candidate], PreparedViewportFeature],
    max_features: int,
) -> tuple[list[PreparedViewportFeature], set[str]]:
    """Equal kind/layer shares, then borrow spare bytes/vertices in fair order.

    A large first object cannot consume another class's entire initial share.
    Features remain whole; failure of the shared phase alone is not truncation.
    Physical context retains precedence over annotations and unknown classes.
    """
    selected: list[PreparedViewportFeature] = []
    reasons: set[str] = set()
    for priority in (0, 1):
        tier = [
            item
            for item in candidates
            if context_priority(feature_group(item[1])[0]) == priority
        ]
        if not tier:
            continue
        layers: dict[str, set[str]] = defaultdict(set)
        for candidate in tier:
            kind, layer = feature_group(candidate[1])
            layers[kind].add(layer)
        kind_coordinates = (budget.max_coordinates - budget.coordinates) // len(layers)
        kind_bytes = (budget.max_feature_bytes - budget.feature_bytes) // len(layers)
        usage: dict[tuple[str, str], _Usage] = defaultdict(_Usage)
        deferred: list[PreparedViewportFeature] = []
        for candidate in tier:
            if len(selected) == max_features:
                return selected, reasons
            item = prepare(candidate)
            # An intrinsically oversized object cannot borrow enough later.
            # Reject it now so a same-group refill does not lose its slot.
            oversized = set()
            if item.size[0] > budget.max_coordinates:
                oversized.add("coordinates")
            if item.size[1] + 2 > budget.max_feature_bytes:
                oversized.add("bytes")
            if oversized:
                reasons.update(oversized)
                continue
            group = feature_group(item.feature)
            layer_count = len(layers[group[0]])
            if not usage[group].fits(
                item.size, kind_coordinates // layer_count, kind_bytes // layer_count
            ):
                deferred.append(item)
                continue
            rejected = budget.admit(item.size)
            if rejected:
                # Rounding cannot normally exhaust the global budget here.
                deferred.append(item)
                continue
            usage[group].add(item.size)
            selected.append(item)
        for item in deferred:
            if len(selected) == max_features:
                return selected, reasons
            rejected = budget.admit(item.size)
            if rejected:
                reasons.update(rejected)
            else:
                selected.append(item)
    return selected, reasons
