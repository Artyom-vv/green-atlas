"""Домен выпуска: канонический JSON без копирования расчётной геометрии.

Стандартный JSONEncoder отвечает за синтаксис и обход дерева. Адаптер раскрывает
только модели геометрии, заимствуя большой GeoJSON и сериализуя высоты по одной.
"""

from __future__ import annotations

from io import BytesIO
from json import JSONEncoder
from typing import Any

from app.geometry.contracts import DxfVerticalPrimitive, GeometrySnapshot
from app.shared.messages import message


def manifest_bytes(
    metadata: dict[str, Any], geometry: GeometrySnapshot | None
) -> bytes:
    if "geometry" in metadata:
        raise ValueError(message("release.geometry_separate"))
    encoder = _GeometryEncoder(
        ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    stream = BytesIO()
    for chunk in encoder.iterencode({**metadata, "geometry": geometry}):
        stream.write(chunk.encode("utf-8"))
    stream.write(b"\n")
    return stream.getvalue()


class _GeometryEncoder(JSONEncoder):
    def default(self, value: object) -> object:
        if isinstance(value, GeometrySnapshot):
            return {
                **value.model_dump(
                    mode="json", exclude={"feature_collection", "vertical_primitives"}
                ),
                "feature_collection": value.feature_collection,
                "vertical_primitives": value.vertical_primitives,
            }
        if isinstance(value, DxfVerticalPrimitive):
            return value.model_dump(mode="json")
        return super().default(value)
