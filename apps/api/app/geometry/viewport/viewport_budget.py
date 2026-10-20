"""Бюджет производного представления карты; превышение лимита не изменяет полноту
расчётной геометрии.
"""

import json
from dataclasses import dataclass
from typing import Any

from app.geometry.geojson_size import coordinate_count

DEFAULT_VIEWPORT_COORDINATES = 250_000
DEFAULT_VIEWPORT_FEATURE_BYTES = 8 * 1024 * 1024

# Это предел удержания производных индексов, а не полноты чертежа. Один большой
# открытый чертёж остаётся в кеше целиком; следующие проекты вытесняют его.
DEFAULT_VIEWPORT_CACHE_PROJECTS = 2
DEFAULT_VIEWPORT_CACHE_FEATURES = 100_000
DEFAULT_VIEWPORT_CACHE_COORDINATES = 500_000
DEFAULT_VIEWPORT_CACHE_SIMPLIFIED_BYTES = 8 * 1024 * 1024


def feature_size(feature: dict[str, Any]) -> tuple[int, int]:
    return (
        coordinate_count(feature.get("geometry")),
        len(json.dumps(feature, ensure_ascii=False, separators=(",", ":")).encode()),
    )


@dataclass
class ViewportBudget:
    max_coordinates: int = DEFAULT_VIEWPORT_COORDINATES
    max_feature_bytes: int = DEFAULT_VIEWPORT_FEATURE_BYTES
    coordinates: int = 0
    feature_bytes: int = 2  # Бюджет включает разделители JSON-массива; метаданные ответа учитываются отдельно.
    features: int = 0

    def __post_init__(self) -> None:
        if self.max_coordinates < 1 or self.max_feature_bytes < 2:
            raise ValueError("Viewport geometry budgets must be positive")

    def admit(self, size: tuple[int, int]) -> list[str]:
        coordinates, encoded_bytes = size
        array_bytes = encoded_bytes + int(self.features > 0)
        reasons = []
        if self.coordinates + coordinates > self.max_coordinates:
            reasons.append("coordinates")
        if self.feature_bytes + array_bytes > self.max_feature_bytes:
            reasons.append("bytes")
        if reasons:
            return reasons
        self.coordinates += coordinates
        self.feature_bytes += array_bytes
        self.features += 1
        return []
