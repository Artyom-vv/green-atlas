"""Existing deterministic recommendation presets, owned by their scenario."""

from typing import Literal, TypedDict


class RecommendationProfile(TypedDict):
    species: str
    spacing: float
    layout: Literal["regular", "staggered", "natural"]
    edge: float
    seed: int


RECOMMENDATION_PROFILES: dict[str, RecommendationProfile] = {
    "balanced": {
        "species": "betula-pendula@2026-08-28.1",
        "spacing": 8.0,
        "layout": "staggered",
        "edge": 2.0,
        "seed": 17,
    },
    "shade": {
        "species": "tilia-cordata@2026-08-28.1",
        "spacing": 11.0,
        "layout": "staggered",
        "edge": 3.0,
        "seed": 23,
    },
    "continuity": {
        "species": "sorbus-aucuparia@2026-08-28.1",
        "spacing": 6.0,
        "layout": "staggered",
        "edge": 1.5,
        "seed": 31,
    },
    "low_future_conflict": {
        "species": "sorbus-aucuparia@2026-08-28.1",
        "spacing": 9.0,
        "layout": "regular",
        "edge": 3.0,
        "seed": 41,
    },
}
