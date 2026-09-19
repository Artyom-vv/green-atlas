"""Check actual product reader against native WCS controls, then trial calculation.

Layer suggestions are deliberately NOT called approved classification. This
does not save or modify a user project or authorize normative planting.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import time

from shapely.geometry import Polygon, shape

from app.dxf_import.adapters import EzdxfReader
from app.geometry.adapters import ShapelyGeometryEngine
from app.projects.contracts import Project


def run(candidate: Path, controls: Path, output: Path):
    preparation = json.loads((candidate.parent / "preparation.json").read_text())
    if not preparation["original_graphics_preserved"]:
        raise ValueError("Preparation preservation gate failed")
    expected = json.loads(controls.read_text())
    with candidate.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != preparation["output_sha256"]:
            raise ValueError("Prepared DXF changed after verification")
    if expected["evidence"]["source_sha256"] != preparation["source_sha256"]:
        raise ValueError("WCS controls belong to a different source")
    by_original = {f["properties"]["source_handle"]: f for f in expected["features"]}
    if (len(by_original) != len(expected["features"])
            or set(by_original) != {r["source_handle"] for r in preparation["replacements"]}):
        raise ValueError("WCS control coverage mismatch")
    started = time.monotonic()
    result = EzdxfReader().read_prepared_file(candidate)
    by_component = defaultdict(list)
    for feature in result.geometry.feature_collection["features"]:
        by_component[feature["properties"].get("source_component_handle")].append(feature)
    compared = []
    for row in preparation["replacements"]:
        matches = by_component[row["replacement_handle"]]
        if len(matches) != 1:
            raise ValueError(f"Replacement coverage mismatch: {row['source_handle']}: {len(matches)}")
        native = shape(by_original[row["source_handle"]]["geometry"])
        actual = shape(matches[0]["geometry"])
        hausdorff = native.hausdorff_distance(actual)
        area_error = abs(native.area-actual.area)
        # Product adapter quantizes XY to six decimals in metres. Compare the
        # expected quantized polygon exactly, plus an independent displacement
        # bound, instead of accepting an arbitrary widened geometry tolerance.
        rounded = Polygon([(round(x, 6), round(y, 6)) for x, y in native.exterior.coords])
        quantization_bound = math.sqrt(2) * 0.5e-6 + 1e-10
        passed = (actual.is_valid and actual.equals(rounded)
                  and hausdorff <= quantization_bound)
        compared.append(dict(source_handle=row["source_handle"], passed=passed,
                             hausdorff_m=hausdorff, area_error_m2=area_error))
    project = Project(name="Diagnostic native planar Kustanayskaya", layers=result.layers,
                      source_geometry=result.geometry, coordinate_reference=result.coordinate_reference)
    for layer in project.layers:
        layer.mapped_kind = layer.suggested_kind
    calculation_start = time.monotonic()
    try:
        geometry = ShapelyGeometryEngine().calculate(project)
        calculation = dict(status="calculated_with_unreviewed_suggestions",
                           features=len(geometry.feature_collection["features"]),
                           site_area_m2=geometry.site_area_m2, allowed_area_m2=geometry.allowed_area_m2)
    except ValueError as error:
        calculation = dict(status="rejected", reason=str(error))
    calculation["seconds"] = time.monotonic()-calculation_start
    report = dict(scope="product reader comparison and unreviewed mapping trial; not normative acceptance",
                  elapsed_seconds=time.monotonic()-started,
                  features=len(result.geometry.feature_collection["features"]),
                  incomplete_layers=[l.source_name for l in result.layers if not l.geometry_complete],
                  layers=[dict(name=l.source_name, suggested_kind=l.suggested_kind,
                               objects=l.object_count, geometry_complete=l.geometry_complete)
                          for l in result.layers],
                  compared_regions=compared, all_regions_match=all(r["passed"] for r in compared),
                  comparison_policy="Exact topology against native WCS rounded to reader's 6-decimal metre grid; displacement <= sqrt(2)*0.5e-6 + 1e-10 m",
                  warnings=result.warnings, calculation=calculation)
    with output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k not in {"layers", "compared_regions", "warnings"}}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "controls", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    run(args.candidate, args.controls, args.output)
