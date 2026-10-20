"""Удержание точных объектов большой карты в JSON; краткие признаки нужны только
для отбора и обзора, полный объект восстанавливается перед выдачей.
"""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from app.geometry.viewport.viewport_selection import feature_group

_MISSING = object()
_JSON_VALUE: TypeAdapter[Any] = TypeAdapter(Any)


def _selection_signature(feature: dict) -> tuple:
    """Сохранить прежнее строковое толкование и отсутствие значений Any."""
    properties = feature.get("properties", {})
    return (
        feature_group(feature),
        str(feature.get("id", properties.get("source_handle", ""))),
        bool(properties.get("source_layer")),
        str(properties.get("kind", "ignore")),
        str(properties.get("kind", "")),
    )


@dataclass(frozen=True, slots=True)
class _FeatureMetadata:
    identity: Any
    kind: Any
    layer: Any
    handle: Any

    @classmethod
    def from_feature(cls, feature: dict) -> "_FeatureMetadata":
        properties = feature.get("properties", {})
        return cls(
            feature.get("id", _MISSING),
            properties.get("kind", _MISSING),
            properties.get("source_layer", _MISSING),
            properties.get("source_handle", _MISSING),
        )

    def feature(self) -> dict:
        properties = {
            name: value
            for name, value in (
                ("kind", self.kind),
                ("source_layer", self.layer),
                ("source_handle", self.handle),
            )
            if value is not _MISSING
        }
        feature = {"properties": properties}
        if self.identity is not _MISSING:
            feature["id"] = self.identity
        return feature


class ViewportFeatures:
    """Малые снимки сохраняют прежние словари; большой индекс удерживает JSON.

    Значения вне обычного JSON сохраняются прежним словарём, если преобразование
    изменяет их. Это сохраняет совместимость прямых вызовов с tuple, Decimal,
    нестандартными ключами и прочими значениями Any без потери объекта.
    """

    def __init__(self, *, compact: bool) -> None:
        self.compact = compact
        self._values: list[dict | bytes] = []
        self._metadata: list[_FeatureMetadata | None] = []
        self.encoded_bytes = 0
        self.encoded_count = 0

    def __len__(self) -> int:
        return len(self._values)

    def append(self, feature: dict) -> None:
        encoded = None
        summary = None
        if self.compact and isinstance(feature.get("properties", {}), dict):
            try:
                # Тот же готовый JSON-кодек уже записывает геометрию проекта.
                encoded = _JSON_VALUE.dump_json(feature)
                decoded = json.loads(encoded)
                if decoded != feature or _selection_signature(
                    decoded
                ) != _selection_signature(feature):
                    encoded = None
                else:
                    # Исходные строки тоже принадлежат полному графу. Краткие
                    # поля берём из проверенного чтения, чтобы не удерживать
                    # их контейнеры и страницы памяти исходника.
                    summary = _FeatureMetadata.from_feature(decoded)
            except (TypeError, ValueError, UnicodeError):
                encoded = None
        self._values.append(feature if encoded is None else encoded)
        self._metadata.append(summary)
        if encoded is not None:
            self.encoded_bytes += len(encoded)
            self.encoded_count += 1

    def metadata(self, index: int) -> dict:
        summary = self._metadata[index]
        if summary is not None:
            return summary.feature()
        value = self._values[index]
        assert isinstance(value, dict)
        return value

    def feature(self, index: int) -> dict:
        value = self._values[index]
        return json.loads(value) if isinstance(value, bytes) else value
