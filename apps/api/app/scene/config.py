"""Параметры представления сцены и явные приближения размеров; новые свидетельства они
не создают.
"""

MIN_HORIZON_YEAR = 0
MAX_HORIZON_YEAR = 40
CONTEXT_SIMPLIFY_TOLERANCE_M = 0.15
CONTEXT_KINDS = frozenset(
    {
        "site_border",
        "building",
        "road",
        "water",
        "existing_green",
        "lawn",
        "utility",
        "restricted",
        "allowed",
        "planting_area",
    }
)

HEIGHT_SCALE = {
    # Нулевой год также является опорным значением интерполяции. Это определяет
    # каждый год публичного диапазона 0–40, включая годы 1–4 до первой
    # опоры взрослого роста.
    "slow": {0: 0.14, 5: 0.25, 10: 0.45, 20: 0.72, 30: 0.88, 40: 1.0},
    "moderate": {0: 0.14, 5: 0.32, 10: 0.58, 20: 0.84, 30: 0.93, 40: 1.0},
    "fast": {0: 0.14, 5: 0.42, 10: 0.7, 20: 0.92, 30: 0.97, 40: 1.0},
}
INITIAL_HEIGHTS = {
    "sapling": (1.5, 2.5),
    "standard": (2.5, 4.5),
    "large": (4.5, 7.0),
}
