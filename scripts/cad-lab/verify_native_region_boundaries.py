"""Compare native definition boundaries against native AREA measurements.

Diagnostic only: does not qualify INSERT transforms or publish source geometry.
"""
import argparse
from collections import defaultdict, Counter
import json
import math
from pathlib import Path

import ezdxf
from shapely import set_precision
from shapely.geometry import LineString, mapping
from shapely.ops import polygonize_full, unary_union


def verify(directory: Path, sagitta: float, include_geometry: bool = False):
    if not math.isfinite(sagitta) or sagitta <= 0:
        raise ValueError("Sagitta must be finite and positive")
    manifest = json.loads((directory / "manifest.json").read_text())
    raw = (directory / "native-measurements.tsv").read_text().splitlines()
    # Preserve the observed UI-paste corruption explicitly; do not silently
    # normalize arbitrary malformed reports.
    delimiter = "\t" if raw[0] == "layer\tarea\tperimeter" else "_.hidepalettes "
    if raw[0] != delimiter.join(["layer", "area", "perimeter"]):
        raise ValueError("Unknown measurement header")
    measurements = {}
    for line in raw[1:]:
        layer, area, perimeter = line.split(delimiter)
        if layer in measurements:
            raise ValueError("Duplicate measurement")
        values = (float(area), float(perimeter))
        if not all(math.isfinite(v) and v > 0 for v in values):
            raise ValueError("Native measurements must be finite and positive")
        measurements[layer] = values
    document = ezdxf.readfile(directory / "native-boundaries.dxf")
    groups = defaultdict(list)
    for entity in document.modelspace():
        groups[entity.dxf.layer].append(entity)
    expected = {r["target_layer"] for r in manifest["rows"]}
    if expected != set(groups) or expected != set(measurements):
        raise ValueError("Boundary/measurement/manifest coverage mismatch")
    rows = []
    for layer, entities in groups.items():
        lines = []
        for entity in entities:
            kind = entity.dxftype()
            if kind == "ARC":
                points = list(entity.flattening(sagitta))
            elif kind == "LINE":
                points = [entity.dxf.start, entity.dxf.end]
            else:
                raise ValueError(f"Unsupported native boundary {kind}")
            if any(abs(v.z) > 1e-9 for v in points):
                raise ValueError("Non-XY control requires explicit projection")
            lines.append(set_precision(LineString([(v.x, v.y) for v in points]), 1e-10))
        polygons, cuts, dangles, invalid = polygonize_full(unary_union(lines))
        area, perimeter = measurements[layer]
        # These are single-loop controls. Multi-face/hole results remain rejected
        # rather than summing overlapping polygonized rings as physical area.
        simple = len(polygons.geoms) == 1 and not polygons.geoms[0].interiors
        actual_area = polygons.area
        actual_perimeter = polygons.length
        area_error = abs(actual_area - area)
        perimeter_error = abs(actual_perimeter - perimeter)
        clean = all(g.is_empty for g in [cuts, dangles, invalid])
        passed = (simple and clean and polygons.is_valid and area > 0
                  and area_error <= max(1e-8, perimeter * sagitta * 2)
                  and perimeter_error <= max(1e-8, perimeter * sagitta * 10))
        rows.append(dict(layer=layer, status="local_comparison_pass" if passed else "review",
                         native_area=area, native_perimeter=perimeter,
                         area=actual_area, perimeter=actual_perimeter,
                         area_error=area_error, perimeter_error=perimeter_error,
                         polygons=len(polygons.geoms), clean=clean, simple=simple))
        if include_geometry and passed:
            rows[-1]["geometry"] = mapping(polygons.geoms[0])
    return dict(scope="local definitions only; not full street or geometry admission",
                source_sha256=manifest["source_sha256"], sagitta=sagitta,
                endpoint_grid=1e-10, measurement_delimiter=delimiter,
                counts=dict(Counter(r["status"] for r in rows)), rows=rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--sagitta", type=float, default=1e-6)
    args = parser.parse_args()
    if args.sagitta <= 0:
        parser.error("sagitta must be positive")
    with args.output.open("x", encoding="utf8") as stream:
        result = verify(args.directory, args.sagitta)
        json.dump(result, stream, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}))
