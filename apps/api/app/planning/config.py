"""Existing placement defaults and limits, separate from calculation code."""

from datetime import timedelta

from app.regulations.placement_config import PLACEMENT_CONFIG

TREE_LAYOUT_RADIUS_M = PLACEMENT_CONFIG.layout.tree_radius_m
SHRUB_LAYOUT_RADIUS_M = PLACEMENT_CONFIG.layout.shrub_radius_m
GROWTH_REVIEW_HORIZON_YEAR = PLACEMENT_CONFIG.layout.growth_horizon_year
CHANGE_SET_PREVIEW_TTL = timedelta(minutes=15)
MAX_CHANGE_SET_PREVIEWS = 128
MAX_APPLIED_CHANGE_SET_RESULTS = 128
MAX_SPACING_INDEXES = 32
