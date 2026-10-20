"""Границы локальных запросов измерений; они не задают нормативное разрешение или допуск
геометрии.
"""

from app.regulations.placement_config import MAX_SETBACK_M, PLACEMENT_CONFIG

# Локальный просмотр непрочитанных объектов отделён от сетевых отступов.
UNRESOLVED_REVIEW_HORIZON_M = (
    PLACEMENT_CONFIG.working_geometry.unresolved_review_horizon_m
)
# Предварительный отбор охватывает наибольший поддержанный отступ здания, дороги или сети.
BASE_QUERY_REACH_M = MAX_SETBACK_M

# Бюджеты удержания производных результатов, без изменения измерений и отступов.
MAX_CACHED_MEASUREMENT_POINTS = 20_000
MAX_CACHED_MEASUREMENT_ROWS = 100_000
MAX_CACHED_MASK_COORDINATES = 250_000
MAX_CACHED_MASK_WKB_BYTES = 2 * 1024 * 1024
MAX_CACHED_CONSTRAINT_BATCHES = 256
MAX_CACHED_CONSTRAINT_BYTES = 16 * 1024 * 1024
# Одна порция ограничивает объём декодирования при попадании в кеш.
MAX_CACHED_CONSTRAINT_COORDINATES = 500_000
# В сумме удерживается только WKB, без графов GEOS; лимит байтов остаётся прежним.
# Несколько порций большого окна не должны вытеснять друг друга после 500 тыс.
MAX_CACHED_CONSTRAINT_TOTAL_COORDINATES = 1_000_000
MAX_CACHED_CONSTRAINT_ISSUES = 32_768
MAX_CACHED_CONSTRAINT_ROUTES = 131_072


def query_reach_m(*plant_radii: float) -> float:
    return max(BASE_QUERY_REACH_M, UNRESOLVED_REVIEW_HORIZON_M, *plant_radii)


def review_reach_m(*plant_radii: float) -> float:
    return max(UNRESOLVED_REVIEW_HORIZON_M, *plant_radii)
