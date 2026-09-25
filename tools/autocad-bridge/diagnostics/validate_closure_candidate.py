"""Check one experimental AutoCAD REGION against the product area admission.

The input is a GACLOSEFILE report generated from a temporary in-memory clone.
This script does not parse DWG/DXF, change a project, or authorize the proposed
source edit. It only checks whether the product's existing planar projection
would accept the native region if an operator-reviewed route were later added.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from app.cad_bridge.contracts import RegionGeometry, RegionLoop, SourceIdentity
from app.cad_bridge.provider import _region_shape
from shapely.geometry.base import BaseGeometry


def validated_candidate(
    report_path: Path, case_name: str
) -> tuple[dict[str, object], BaseGeometry]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("source_unchanged") is not True:
        raise ValueError("Source DWG changed or source hash was not verified")
    cases = [case for case in report["cases"] if case["name"] == case_name]
    if len(cases) != 1 or len(cases[0]["entities"]) != 1:
        raise ValueError("Expected one explicit native source handle")
    case = cases[0]
    spur = case.get("initial_spur_candidate") or {}
    if spur.get("status") == "native_candidate" and spur.get("conversion_status") == 0:
        regions = spur["regions"]
        candidate_method = "native_initial_spur_trim_on_transient_clone"
    elif (
        case.get("method") == "AcDbPolyline.clone+native_close+AcDbRegion.createFromCurves"
        and case.get("clone_closed_after_explicit_request") is True
        and case.get("conversion_status") == 0
        and cases[0]["entities"][0].get("closed") is False
    ):
        regions = case["regions"]
        candidate_method = "native_explicit_chord_on_transient_clone"
    else:
        raise ValueError("No successful native candidate")
    if len(regions) != 1 or regions[0]["brep_set_status"] != 0:
        raise ValueError("Expected one valid native BRep")
    region = regions[0]
    topology = region["topology_probe"]
    faces = topology["faces"]
    if not topology["faces_traversed"] or len(faces) != 1:
        raise ValueError("Native face traversal incomplete or ambiguous")
    if not faces[0]["loops_traversed"] or len(faces[0]["loops"]) != 1:
        raise ValueError("Native loop traversal incomplete or ambiguous")
    if faces[0]["loops"][0]["role"] != "outer":
        raise ValueError("Native candidate needs one exterior loop")
    product = region["product_extraction"]
    if not product["resolved"] or product["error_status"] != 0:
        raise ValueError("Product native extractor rejected the candidate")
    if len(product["loops"]) != 1 or product["loops"][0]["role"] != "outer":
        raise ValueError("Product native extractor did not preserve one exterior")
    # This real fixture declares metres (INSUNITS=6); do not silently apply it
    # to drawings with different or unset units.
    if report["units_code"] != 6:
        raise ValueError("Diagnostic requires a metre-unit source")
    loop = product["loops"][0]
    source_handle = case["entities"][0]["handle"]
    geometry = RegionGeometry(
        id=f"diagnostic-region-{source_handle}",
        identity=SourceIdentity(handle=source_handle),
        kind="region",
        loops=[RegionLoop(role="outer", closed=True, coordinates=loop["coordinates"])],
        native_area_units2=product["area_units2"],
        native_perimeter_units=product["perimeter_units"],
        achieved_tolerance_m=loop["sampled_maximum_deviation_units"],
        content_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
    )
    planar = _region_shape(geometry, 1.0)
    area_error = abs(planar.area - geometry.native_area_units2)
    perimeter_error = abs(planar.length - geometry.native_perimeter_units)
    requested_tolerance_m = 0.0001  # bridge_config.h, same producer build
    area_envelope = max(1e-10, geometry.native_perimeter_units * requested_tolerance_m * 2)
    perimeter_envelope = max(1e-10, geometry.native_perimeter_units * 0.01)
    if (
        not planar.is_valid
        or planar.is_empty
        or not math.isfinite(planar.area)
        or area_error > area_envelope
        or perimeter_error > perimeter_envelope
        or geometry.achieved_tolerance_m > requested_tolerance_m * (1 + 1e-12)
    ):
        raise ValueError("Product planar area/perimeter/tolerance admission failed")
    summary = {
        "schema": "green-atlas.native-closure-product-admission/1",
        "source_sha256": report["source_sha256"],
        "source_unchanged": True,
        "source_handle": source_handle,
        "candidate_method": candidate_method,
        "source_endpoint_gap_m": case["entities"][0].get("endpoint_gap_units"),
        "native_region_area_m2": geometry.native_area_units2,
        "product_polygon_area_m2": planar.area,
        "area_error_m2": area_error,
        "area_envelope_m2": area_envelope,
        "native_perimeter_m": geometry.native_perimeter_units,
        "product_perimeter_m": planar.length,
        "perimeter_error_m": perimeter_error,
        "perimeter_envelope_m": perimeter_envelope,
        "sampling_deviation_m": geometry.achieved_tolerance_m,
        "points": len(loop["coordinates"]),
        "product_polygon_valid": planar.is_valid,
        # The report alone has no block-instance transform. A caller may use
        # this planar candidate in a project only after matching the original
        # source handle/instance in that admitted snapshot.
        "instance_transform_verified": False,
        "project_coordinates_qualified": False,
        "product_project_published": False,
        "operator_reviewed": False,
        "placement_verified": False,
    }
    return summary, planar


def validate(report_path: Path, case_name: str) -> dict[str, object]:
    summary, _ = validated_candidate(report_path, case_name)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--case", default="open_site_3B3A")
    args = parser.parse_args()
    print(json.dumps(validate(args.report, args.case), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
