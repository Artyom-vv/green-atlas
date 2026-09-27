"""Inspect a generated DXF in a separate, memory/time-bounded Python process."""

import sys
from collections import Counter
from pathlib import Path

import ezdxf

from app.cad_import.boundary_catalog import boundary_catalog
from app.cad_import.contracts import DrawingInspection


def inspect_drawing(path: Path) -> DrawingInspection:
    document = ezdxf.readfile(path)
    return DrawingInspection(
        dxf_version=document.dxfversion,
        units=document.units,
        modelspace_entities=dict(Counter(e.dxftype() for e in document.modelspace())),
        layer_names=[layer.dxf.name for layer in document.layers],
        xrefs={
            block.name: block.block.dxf.xref_path
            for block in document.blocks
            if block.block is not None and block.block.is_xref
        },
        boundary_catalog=boundary_catalog(document),
    )


if __name__ == "__main__":
    result = inspect_drawing(Path(sys.argv[1]))
    Path(sys.argv[2]).write_text(result.model_dump_json(), encoding="utf-8")
