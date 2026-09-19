"""Definition identity of SDK virtual components, not a semantic classifier."""
from typing import Any


def component_definition_provenance(entity: Any) -> dict[str, str]:
    original = getattr(entity, "origin_of_copy", None)
    if original is None or original.doc is None:
        return {}
    handle = original.dxf.get("handle")
    owner = original.doc.entitydb.get(original.dxf.get("owner", ""))
    if not handle or owner is None or owner.dxftype() != "BLOCK_RECORD":
        return {}
    return {
        "source_component_handle": str(handle),
        "source_component_block": str(owner.dxf.name),
    }
