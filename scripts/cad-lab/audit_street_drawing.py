"""Read one whole drawing for a street audit; never normalize or edit it."""

from collections import Counter
import json
from pathlib import Path
import sys
import time

import ezdxf


def inspect(path: Path) -> dict:
    started = time.monotonic()
    doc = ezdxf.readfile(path)
    definitions = {
        block.name: block for block in doc.blocks
        if block.block.is_xref or block.block.is_xref_overlay
    }
    reached = set()
    cycles = set()
    visited_blocks = set()
    active_types = Counter()

    def visit(layout, ancestry=()):
        for entity in layout:
            active_types[entity.dxftype()] += 1
            if entity.dxftype() != "INSERT":
                continue
            name = entity.dxf.name
            if name in definitions:
                reached.add(name)
            if name in ancestry:
                cycles.add(name)
                continue
            if name in visited_blocks:
                continue
            visited_blocks.add(name)
            child = doc.blocks.get(name)
            if child is not None:
                visit(child, (*ancestry, name))

    visit(doc.modelspace())
    return {
        "units": doc.units,
        "dxf_version": doc.dxfversion,
        "model_entities": len(doc.modelspace()),
        "model_types": dict(Counter(e.dxftype() for e in doc.modelspace())),
        "reachable_definition_types": dict(active_types),
        "count_note": "Each reachable local block definition counted once, not exploded instances",
        "cycles": sorted(cycles),
        "layers": [layer.dxf.name for layer in doc.layers],
        "xrefs": [
            {"block": name, "requested": block.block.dxf.get("xref_path", ""),
             "active": name in reached, "overlay": block.block.is_xref_overlay,
             "cached_entities": len(block)}
            for name, block in definitions.items()
        ],
        "seconds": time.monotonic() - started,
    }


if __name__ == "__main__":
    source, output = map(Path, sys.argv[1:])
    result = inspect(source)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
