"""Build a small independent-DXF release control for native AutoCAD reopen."""

from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from io import StringIO
from pathlib import Path

import ezdxf

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/api"))

from app.dxf_import.encoding import decode_text_dxf
from app.exporting.adapters import DxfRoundTripWriter
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project


def encode(document) -> bytes:
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode(document.output_encoding)


def main(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "primary": output / "primary.dxf",
        "auxiliary": output / "auxiliary.dxf",
        "merged": output / "merged-planting-plan.dxf",
        "receipt": output / "receipt.json",
    }
    if any(path.exists() for path in paths.values()):
        raise FileExistsError("Control output already exists; keep the prior evidence")

    primary = ezdxf.new("R2013", setup=True)
    primary.units = ezdxf.units.M
    primary.layers.add("PRIMARY_CONTEXT", color=1)
    primary.modelspace().add_lwpolyline(
        [(0, 0), (40, 0), (40, 30), (0, 30)],
        close=True,
        dxfattribs={"layer": "PRIMARY_CONTEXT"},
    )
    primary_content = encode(primary)

    auxiliary = ezdxf.new("R2013", setup=True)
    auxiliary.units = ezdxf.units.M
    auxiliary.layers.add("AUX_NETWORK", color=3)
    auxiliary.modelspace().add_line(
        (5, 10), (35, 10), dxfattribs={"layer": "AUX_NETWORK"}
    )
    symbol = auxiliary.blocks.new("AUX_SYMBOL")
    symbol.add_circle((0, 0), 1, dxfattribs={"layer": "AUX_NETWORK"})
    auxiliary.modelspace().add_blockref("AUX_SYMBOL", (20, 20))
    auxiliary.layout("Layout1").add_text("Auxiliary sheet retained")
    auxiliary_content = encode(auxiliary)

    project = Project(
        name="Multi DXF native control",
        plan=Plan(
            objects=[
                PlanObject(
                    id="control-tree",
                    kind="tree",
                    x=20,
                    y=15,
                    radius=2,
                )
            ]
        ),
    )
    _artifact, merged_content = DxfRoundTripWriter().create(
        project,
        primary_content,
        {"networks/auxiliary.dxf": auxiliary_content},
    )
    paths["primary"].write_bytes(primary_content)
    paths["auxiliary"].write_bytes(auxiliary_content)
    paths["merged"].write_bytes(merged_content)

    reopened = ezdxf.read(StringIO(decode_text_dxf(merged_content)))
    source_marker = (
        f"GREEN_ATLAS_SOURCE_{sha256(auxiliary_content).hexdigest()[:12].upper()}"
    )
    receipt = {
        "primary_sha256": sha256(primary_content).hexdigest(),
        "auxiliary_sha256": sha256(auxiliary_content).hexdigest(),
        "merged_sha256": sha256(merged_content).hexdigest(),
        "merged_bytes": len(merged_content),
        "modelspace_types": dict(
            sorted(
                {
                    kind: len(reopened.modelspace().query(kind))
                    for kind in {entity.dxftype() for entity in reopened.modelspace()}
                }.items()
            )
        ),
        "layers": sorted(layer.dxf.name for layer in reopened.layers),
        "layouts": list(reopened.layouts.names_in_taborder()),
        "source_marker_count": sum(
            entity.is_alive and entity.has_xdata(source_marker)
            for entity in reopened.entitydb.values()
        ),
        "originals_unchanged": True,
        "native_reopen": "pending",
    }
    paths["receipt"].write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    main(arguments.output)
