"""Общая интерполяция ограниченных биологических прогнозов по заданным временным опорным
значениям.
"""

from __future__ import annotations

from collections.abc import Sequence
from math import isfinite

from app.species.contracts import GrowthEnvelopeForecast

ForecastAnchorSequence = Sequence[GrowthEnvelopeForecast] | None


def calculation_growth_radii(
    canopy: GrowthEnvelopeForecast, roots: GrowthEnvelopeForecast
) -> tuple[float, float]:
    """Выбирает рабочие габариты на уже выбранном горизонте плана.

    Компоновка использует верхнюю границу кроны и нижнюю границу корней.
    Это сценарное решение для размещения; исходный биологический диапазон
    сохраняется, а нормативные отступы проверяются независимо.
    """
    return canopy.radius_max_m, roots.radius_min_m


def forecast_at(
    items: ForecastAnchorSequence,
    year: int | float,
) -> GrowthEnvelopeForecast | None:
    """Интерполирует прогноз внутри опорного интервала, не продолжая его за границы.

    Версионированные опорные значения задают линейную интерполяцию. Вне их
    интервала результат отсутствует: потребитель обязан показать пробел данных,
    а не принять ближайшее известное значение за прогноз.
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
            # Горизонт API задаётся целым годом; после разбора запроса число
            # может прийти как 23.0, поэтому подпись приводится к целому.
            "horizon_year": int(year),
            "radius_min_m": lower.radius_min_m
            + (upper.radius_min_m - lower.radius_min_m) * ratio,
            "radius_max_m": lower.radius_max_m
            + (upper.radius_max_m - lower.radius_max_m) * ratio,
            "confidence": "low" if year > 10 else lower.confidence,
        }
    )
