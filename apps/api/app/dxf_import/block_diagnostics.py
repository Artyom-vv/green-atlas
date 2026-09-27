"""Addresses of failed block components without modifying the CAD document."""

from dataclasses import dataclass
from typing import Any


def source_handle(entity: Any) -> str:
    original = getattr(entity, "origin_of_copy", None)
    source = original if original is not None else entity
    return str(source.dxf.get("handle", "") or "?")


@dataclass(frozen=True)
class BlockGeometryFailure:
    source_layer: str
    entity_type: str
    source_handle: str
    insert_path: tuple[str, ...]
    reason: str

    def warning(self) -> str:
        return (
            f"Блок {' / '.join(self.insert_path)}, "
            f"объект {self.entity_type} #{self.source_handle}, "
            f"слой «{self.source_layer}»: {self.reason}. "
            "Исходный объект сохранён; расчётное представление требует проверки."
        )


def record_failure(
    failures: list[BlockGeometryFailure] | None,
    entity: Any,
    inherited_layer: str,
    insert_path: tuple[str, ...],
    reason: str,
) -> None:
    if failures is None:
        return
    layer = str(entity.dxf.get("layer", "0") or "0")
    failures.append(BlockGeometryFailure(
        source_layer=inherited_layer if layer == "0" else layer,
        entity_type=entity.dxftype(),
        source_handle=source_handle(entity),
        insert_path=insert_path,
        reason=reason,
    ))
