"""Transparent ordering of spatially checked alternatives, without fitted weights."""

from math import pi

from app.planning.config import GROWTH_REVIEW_HORIZON_YEAR
from app.planning.recommendation_contracts import RecommendationPreview


def crown_projection_sum(preview: RecommendationPreview) -> float:
    """Sum of forecast discs, not union coverage, real shade or a measured effect."""
    if preview.change_set is None:
        return 0.0
    return sum(
        pi * forecast.radius_max_m**2
        for plant in preview.change_set.additions
        for forecast in plant.canopy_forecast
        if forecast.horizon_year == GROWTH_REVIEW_HORIZON_YEAR
    )


def selection_key(
    preview: RecommendationPreview, profile: str, preferred: bool, species_id: str
) -> tuple:
    plants = preview.change_set.additions if preview.change_set else []
    radii = [
        f.radius_max_m
        for p in plants
        for f in p.canopy_forecast
        if f.horizon_year == GROWTH_REVIEW_HORIZON_YEAR
    ]
    if profile == "shade":
        objective = crown_projection_sum(preview)
    elif profile == "low_future_conflict":
        objective = -max(radii, default=0)
    else:
        objective = len(plants)
    # Empty alternatives never beat a feasible one. Stable ID breaks ties,
    # independent of dictionary order or the platform's random hash seed.
    return (not bool(plants), -objective, -len(plants), not preferred, species_id)


SELECTION_REASONS = {
    "shade": "Сравнены допустимые варианты по сумме прогнозных площадей крон; это приближённый критерий, не расчёт тени или инсоляции.",
    "low_future_conflict": "Среди непустых допустимых вариантов выбрана меньшая прогнозная крона; это не прогноз всех будущих конфликтов.",
    "balanced": "Среди видов, отмеченных для категории территории, выбран вариант с большим числом прошедших пространственную проверку посадок. Экологический баланс отдельно не рассчитан.",
    "continuity": "Выбран вариант с большим числом допустимых посадок. Непрерывность зелёного каркаса требует отдельной пространственной модели.",
}
