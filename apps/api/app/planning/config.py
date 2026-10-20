"""Общие параметры размещения и лимиты предпросмотров; бюджет свободного поиска имеет
отдельного владельца.
"""

from datetime import timedelta
from typing import Annotated

from pydantic import Field

from app.regulations.placement_config import PLACEMENT_CONFIG

TREE_LAYOUT_RADIUS_M = PLACEMENT_CONFIG.layout.tree_radius_m
SHRUB_LAYOUT_RADIUS_M = PLACEMENT_CONFIG.layout.shrub_radius_m
MIN_PLANNING_HORIZON_YEAR = 0
MAX_PLANNING_HORIZON_YEAR = 40
# Сохранённый план проверяется на текущий момент; будущее задаёт только сценарий.
DEFAULT_PLANNING_HORIZON_YEAR = 0
PlanningHorizonYear = Annotated[
    int, Field(strict=True, ge=MIN_PLANNING_HORIZON_YEAR, le=MAX_PLANNING_HORIZON_YEAR)
]
CHANGE_SET_PREVIEW_TTL = timedelta(minutes=15)
MAX_CHANGE_SET_PREVIEWS = 128
MAX_APPLIED_CHANGE_SET_RESULTS = 128
MAX_SPACING_INDEXES = 32

# Максимальный размер предложения координат до проверки безопасности.
MAX_PATTERN_CANDIDATES = 5000

# Лимит альтернатив относится к регулярным заполнениям и маскам. Свободный
# поиск использует отдельную версионированную политику candidate_search.
MAX_PATTERN_ALTERNATIVE_PROBES = 5000
PATTERN_ALTERNATIVE_PROBES_PER_PLANT = 8
