"""Navigate large tool results without cutting JSON mid-record."""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResultPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_index: int = Field(ge=0)
    path: list[str | int] = Field(default_factory=list, max_length=40)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=30)
    fields: list[str] = Field(default_factory=list, max_length=8)


def page_result(artifacts: dict[int, Any], query: ResultPage) -> dict:
    if query.event_index not in artifacts:
        raise ValueError("Результат не найден в текущей проверке")
    node = artifacts[query.event_index]
    for token in query.path:
        if isinstance(node, dict) and isinstance(token, str) and token in node:
            node = node[token]
        elif isinstance(node, list) and type(token) is int and 0 <= token < len(node):
            node = node[token]
        else:
            raise ValueError("Путь не найден в результате")

    def entry(value, path):
        if query.fields and isinstance(value, dict):
            return {"path": path, "fields": {key: scalar_or_reference(value[key], [*path, key]) for key in query.fields if key in value},
                    "missing_fields": [key for key in query.fields if key not in value]}
        return scalar_or_reference(value, path)

    def scalar_or_reference(value, path):
        if isinstance(value, (dict, list)):
            return {"type": "object" if isinstance(value, dict) else "array", "size": len(value), "path": path}
        if isinstance(value, str) and len(value) > 1000:
            return {"type": "text", "characters": len(value), "path": path}
        return {"value": value}

    result = {"event_index": query.event_index, "path": query.path}
    if isinstance(node, dict):
        keys = list(node)
        result.update(type="object", total=len(keys), items=[{"key": key, **entry(node[key], [*query.path, key])}
                      for key in keys[query.offset:query.offset + query.limit]])
    elif isinstance(node, list):
        result.update(type="array", total=len(node), items=[{"index": i, **entry(node[i], [*query.path, i])}
                      for i in range(query.offset, min(len(node), query.offset + query.limit))])
    elif isinstance(node, str):
        # Text uses the same explicit offset/limit, measured in characters.
        result.update(type="text", total=len(node), text=node[query.offset:query.offset + query.limit])
    else:
        return {**result, "type": "scalar", "value": node, "next_offset": None}
    end = query.offset + query.limit
    result["next_offset"] = end if end < result["total"] else None
    return result
