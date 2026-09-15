"""Existing placement defaults and limits, separate from calculation code."""

from datetime import timedelta

TREE_LAYOUT_RADIUS_M = 1.6
SHRUB_LAYOUT_RADIUS_M = 0.65
GROWTH_REVIEW_HORIZON_YEAR = 20
CHANGE_SET_PREVIEW_TTL = timedelta(minutes=15)
MAX_CHANGE_SET_PREVIEWS = 128
MAX_APPLIED_CHANGE_SET_RESULTS = 128
MAX_SPACING_INDEXES = 32
