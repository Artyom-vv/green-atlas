"""Компактный контекст классификатора и ограниченный запрос модели со схемой только
запрошенных ответов.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel
from pydantic.json_schema import JsonSchemaValue

from app.dxf_import.layers.contracts import Layer
from app.layer_recognition.budget import RecognitionUnavailable
from app.shared.failures import FailureReason

MODEL = "gpt-6-luna"
# Подключение через ChatGPT использует отдельный каталог CLI.
CODEX_MODEL = "gpt-5.6-luna"
REASONING_EFFORT: Literal["low"] = "low"
MAX_REQUEST_BYTES = 64 * 1024


class RecognitionPayloadTooLarge(RecognitionUnavailable):
    """Предел тела запроса превышен до обращения к платной модели."""

    def __init__(self) -> None:
        super().__init__(FailureReason.RECOGNITION_PAYLOAD_TOO_LARGE)


def compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def catalog_groups(layers: Sequence[Layer]) -> list[dict[str, object]]:
    """Группировать CAD-контекст по точному XREF-префиксу без слияния повторных слоёв."""
    rows_by_prefix: dict[str, list[list[object]]] = {}
    for layer in layers:
        prefix, separator, local_name = layer.source_name.rpartition("|")
        if separator:
            prefix += separator
        else:
            prefix, local_name = "", layer.source_name
        rows_by_prefix.setdefault(prefix, []).append(
            [local_name, layer.object_count, layer.entity_types]
        )
    return [
        {
            "xref_prefix": prefix,
            "columns": ["name", "object_count", "entity_types"],
            "rows": rows,
        }
        for prefix, rows in rows_by_prefix.items()
    ]


class _RequestedLayer(BaseModel):
    layer_id: str


class _RequestedRoles(BaseModel):
    batch: list[_RequestedLayer]


class _RequestedConflict(BaseModel):
    layer: _RequestedLayer


class _RequestedConflicts(BaseModel):
    targets: list[_RequestedConflict]


def _requested_reply_schema(
    schema: type[BaseModel], source: str, name: str
) -> JsonSchemaValue:
    result = schema.model_json_schema()
    reply_field = {"layer_roles": "proposals", "layer_conflicts": "decisions"}.get(name)
    if reply_field is None or reply_field not in result.get("properties", {}):
        return result
    identities = (
        [item.layer_id for item in _RequestedRoles.model_validate_json(source).batch]
        if name == "layer_roles"
        else [
            item.layer.layer_id
            for item in _RequestedConflicts.model_validate_json(source).targets
        ]
    )
    if not identities or any(not value for value in identities):
        raise ValueError("Не указаны идентификаторы слоёв для распознавания")
    if len(set(identities)) != len(identities):
        raise ValueError("Повторяются идентификаторы слоёв для распознавания")
    replies = result["properties"][reply_field]
    replies["minItems"] = replies["maxItems"] = len(identities)
    item_schema = replies["items"]
    if "$ref" in item_schema:
        item_schema = result["$defs"][item_schema["$ref"].rsplit("/", 1)[1]]
    item_schema["properties"]["layer_id"]["enum"] = identities
    return result


def model_request_body(
    instructions: str,
    source: str,
    schema: type[BaseModel],
    name: str,
    max_output_tokens: int,
) -> bytes:
    """Сериализовать одинаковое тело модели на desktop и relay до побочных действий."""
    if len(source.encode()) > MAX_REQUEST_BYTES:
        raise RecognitionPayloadTooLarge()
    body = compact_json(
        dict(
            model=MODEL,
            instructions=instructions,
            input=source,
            store=False,
            tools=[],
            max_output_tokens=max_output_tokens,
            service_tier="default",
            reasoning={"effort": REASONING_EFFORT},
            text={
                "format": {
                    "type": "json_schema",
                    "name": name,
                    "strict": True,
                    "schema": _requested_reply_schema(schema, source, name),
                }
            },
        )
    ).encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise RecognitionPayloadTooLarge()
    reply_field = {"layer_roles": "proposals", "layer_conflicts": "decisions"}.get(name)
    if reply_field is not None and reply_field in schema.model_fields:
        from app.layer_recognition.payload_admission import anonymous_target_ids

        anonymous_target_ids(name, source)
    return body
