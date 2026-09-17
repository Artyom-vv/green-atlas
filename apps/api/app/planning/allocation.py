"""Explicit total-count allocation, independent of geometry and validation."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal


def composition_targets(
    total: int, tree_share: float
) -> dict[Literal["tree", "shrub"], int]:
    """User ratio, rounded half up once for the whole request; never a norm."""
    trees = int(
        (Decimal(total) * Decimal(str(tree_share))).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    return {"tree": trees, "shrub": total - trees}


def equal_zone_targets(zone_ids: list[str], total: int) -> dict[str, int]:
    """Remainders follow the displayed project order; never duplicate a zone."""
    unique = list(dict.fromkeys(zone_ids))
    if not unique:
        return {}
    base, remainder = divmod(total, len(unique))
    return {zone_id: base + (index < remainder) for index, zone_id in enumerate(unique)}


def spread_indices(indices: list[int], count: int) -> list[int]:
    """Pick across the whole validated sequence, not its first map strip."""
    if count <= 0:
        return []
    if len(indices) <= count:
        return indices
    if count == 1:
        return [indices[len(indices) // 2]]
    return [
        indices[round(index * (len(indices) - 1) / (count - 1))]
        for index in range(count)
    ]
