#!/usr/bin/env python3
"""Create a deterministic DXF seed and AutoLISP builder for native controls.

The Python stage creates only ordinary closed polylines. AutoCAD creates the
REGION/SUBTRACT topology and block graph, so the positive control does not
depend on ezdxf manufacturing ACIS payloads.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import ezdxf


def prepare(output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=False)
    drawing = output / "native-region-positive.dxf"
    document = ezdxf.new("R2018")
    document.units = 6
    document.layers.add("GA_REGION_CONTROL", color=3)
    modelspace = document.modelspace()
    outer = modelspace.add_lwpolyline(
        [(0, 0), (20, 0), (20, 20), (0, 20)],
        close=True,
        dxfattribs={"layer": "GA_REGION_CONTROL"},
    )
    hole = modelspace.add_lwpolyline(
        [(5, 5), (15, 5), (15, 15), (5, 15)],
        close=True,
        dxfattribs={"layer": "GA_REGION_CONTROL"},
    )
    simple = modelspace.add_lwpolyline(
        [(40, 0), (52, 0), (52, 8), (40, 8)],
        close=True,
        dxfattribs={"layer": "GA_REGION_CONTROL"},
    )
    document.saveas(drawing)

    builder = output / "build-native-region-positive.lsp"
    builder.write_text(
        "\n".join(
            [
                "(setvar \"CMDECHO\" 0)",
                f'(setq ga_outer (handent "{outer.dxf.handle}"))',
                f'(setq ga_hole (handent "{hole.dxf.handle}"))',
                f'(setq ga_simple (handent "{simple.dxf.handle}"))',
                '(command "_.REGION" ga_outer "")',
                "(setq ga_outer_region (entlast))",
                '(command "_.REGION" ga_hole "")',
                "(setq ga_hole_region (entlast))",
                '(command "_.SUBTRACT" ga_outer_region "" ga_hole_region "")',
                "(setq ga_holed_region (entlast))",
                '(command "_.REGION" ga_simple "")',
                "(setq ga_simple_region (entlast))",
                '(command "_.-BLOCK" "GA_HOLED" "0,0,0" ga_holed_region "")',
                '(command "_.-INSERT" "GA_HOLED" "10,20,0" "2" "1.5" "30")',
                "(setq ga_child_insert (entlast))",
                '(command "_.-BLOCK" "GA_PARENT" "0,0,0" ga_child_insert "")',
                '(command "_.-INSERT" "GA_PARENT" "100,200,0" "1" "1" "15")',
                "(setq ga_root_insert (entlast))",
                '(princ "\\nGREEN_ATLAS_CONTROL_READY")',
                "(princ)",
            ]
        ),
        encoding="utf-8",
    )
    manifest = {
        "drawing": str(drawing.resolve()),
        "builder": str(builder.resolve()),
        "seed_handles": {
            "outer": outer.dxf.handle,
            "hole": hole.dxf.handle,
            "simple": simple.dxf.handle,
        },
        "expected": {
            "regions": 2,
            "holed_region_loops": 2,
            "nested_region_chain_depth": 2,
            "simple_region_chain_depth": 0,
            "nested_region_area": 900,
            "nested_region_perimeter": 210,
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
