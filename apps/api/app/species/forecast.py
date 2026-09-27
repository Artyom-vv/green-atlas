"""Shared interpolation for bounded biological forecast anchors."""

from __future__ import annotations

from math import isfinite
from typing import Sequence

from app.species.contracts import GrowthEnvelopeForecast


def forecast_at(
    items: Sequence[GrowthEnvelopeForecast] | None,
    year: int | float,
) -> GrowthEnvelopeForecast | None:
    """Return a forecast at ``year`` without extrapolating beyond anchors.

    Forecasts are stored as a small set of versioned anchor values. Years
    between anchors are a transparent linear interpolation; years outside the
    anchor interval have no forecast rather than silently reusing the nearest
    value. The caller can then present that gap as missing data.
    """

    if not items or not isfinite(year):
        return None

    sorted_items = sorted(items, key=lambda item: item.horizon_year)
    if year < sorted_items[0].horizon_year or year > sorted_items[-1].horizon_year:
        return None

    lower = next(
        (item for item in reversed(sorted_items) if item.horizon_year <= year),
        sorted_items[0],
    )
    upper = next(
        (item for item in sorted_items if item.horizon_year >= year),
        sorted_items[-1],
    )
    if lower.horizon_year == upper.horizon_year:
        return lower.model_copy(deep=True)

    ratio = (year - lower.horizon_year) / (upper.horizon_year - lower.horizon_year)
    return lower.model_copy(
        update={
            # API horizons are integer years; callers still may pass ``23.0``
            # after query parsing, so normalize the interpolated label.
            "horizon_year": int(year),
            "radius_min_m": lower.radius_min_m + (upper.radius_min_m - lower.radius_min_m) * ratio,
            "radius_max_m": lower.radius_max_m + (upper.radius_max_m - lower.radius_max_m) * ratio,
            "confidence": "low" if year > 10 else lower.confidence,
        }
    )
