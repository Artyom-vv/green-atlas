"""Параметры воспроизводимых рекомендаций и предложений возле зданий."""

from typing import Literal, TypedDict

BUILDING_SCREEN_CONTEXT_RADIUS_M = 40
BUILDING_SCREEN_CLEARANCE_MARGIN_M = 0.01
BUILDING_SCREEN_BUFFER_QUADRANT_SEGMENTS = 32
BUILDING_SCREEN_DEFAULT_TARGET_COUNT = 500
BUILDING_SCREEN_CLUSTER_SIZE = 3
BUILDING_SCREEN_CLUSTER_GAP_M = 24
BUILDING_SCREEN_EDGE_OFFSET_M = 0


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
