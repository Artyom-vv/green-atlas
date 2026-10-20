"""Канонические хеши CAD-записей и снимка; идентичность данных проверяется без второй
копии полного графа.
"""

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from app.cad_bridge.contracts import CadSnapshot

UNSIGNED_CONTENT_SHA256 = "0" * 64


def canonical_record_sha256(value: object) -> str:
    """Хешировать нормализованную запись по установленной JSON-политике снимка."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def snapshot_payload_sha256(snapshot: CadSnapshot) -> str:
    """Сохранять установленный JSON-хеш, удерживая сериализованной не более одной
    записи.
    """
    digest = hashlib.sha256()

    def write(value: Any) -> None:
        if isinstance(value, BaseModel):
            value = value.model_dump(by_alias=True, mode="json", exclude_none=True)
        digest.update(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )

    fields = {
        field.alias or name: getattr(snapshot, name)
        for name, field in CadSnapshot.model_fields.items()
        if name != "summary" and getattr(snapshot, name) is not None
    }
    digest.update(b"{")
    for index, key in enumerate(sorted(fields)):
        if index:
            digest.update(b",")
        write(key)
        digest.update(b":")
        value = fields[key]
        if isinstance(value, list):
            digest.update(b"[")
            for item_index, item in enumerate(value):
                if item_index:
                    digest.update(b",")
                write(item)
            digest.update(b"]")
        else:
            write(value)
    digest.update(b"}")
    return digest.hexdigest()


def signed_geometry_payload(
    geometry: BaseModel,
    *,
    exclude_none: bool = False,
) -> dict[str, Any]:
    payload = geometry.model_dump(mode="json", exclude_none=exclude_none)
    payload["content_sha256"] = canonical_record_sha256(
        {key: value for key, value in payload.items() if key != "content_sha256"}
    )
    return payload
