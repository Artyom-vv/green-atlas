"""Inspect block expansion before creating virtual geometry.

Array multiplicity, corrupt graphs and ordinary source volume have different
admission rules. A large survey in one INSERT is not an oversized MINSERT.
"""

from dataclasses import dataclass
from typing import Any

from app.dxf_import.capacity import SourceCapacityExceeded


@dataclass(frozen=True)
class BlockExpansion:
    components: int
    blocked: bool = False


def inspect_block_expansion(
    entity: Any, *, max_components: int | None, max_array_instances: int, max_depth: int
) -> BlockExpansion:
    def visit(
        node: Any, depth: int, ancestry: frozenset[str], copies: int
    ) -> BlockExpansion:
        if depth >= max_depth:
            return BlockExpansion(max_array_instances + 1, blocked=True)
        try:
            copies *= max(1, int(node.mcount or 1))
            name = str(node.dxf.name)
            block = node.block()
        except (AttributeError, TypeError, ValueError):
            return BlockExpansion(max_array_instances + 1, blocked=True)
        if (
            copies > max_array_instances
            or not name
            or name in ancestry
            or block is None
        ):
            return BlockExpansion(max(copies, max_array_instances + 1), blocked=True)
        components = 0
        for child in block:
            if child.dxftype() == "INSERT":
                nested = visit(child, depth + 1, ancestry | {name}, copies)
                if nested.blocked:
                    return nested
                components += nested.components
            else:
                components += copies
            if max_components is not None and components > max_components:
                raise SourceCapacityExceeded(
                    f"Блок «{name}» превышает бюджет компонентов исходной геометрии: "
                    f"{components} > {max_components}. Импорт не сохранён; "
                    "частичное раскрытие блока для расчёта не допускается."
                )
        return BlockExpansion(components)

    return visit(entity, 0, frozenset(), 1)
