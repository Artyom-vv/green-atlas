"""Summarize recorded AutoCAD results; never run CAD or infer unqueried areas."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def classification(query: dict) -> str:
    if query["status"] != 0:
        return "query_error"
    if query["containment"] == "outside" and query["container_class"] is None:
        return "outside"
    if query["containment"] == "on_boundary" and query["container_class"] == "AcBrFace":
        return "face"
    return "other_native_result"


def summarize(directory: Path) -> dict:
    request = json.loads((directory / "cases.json").read_bytes())
    report_path = directory / "native-report.json"
    report = json.loads(report_path.read_bytes())
    source = Path(request["source"])
    assert report["source"] == str(source)
    assert report["source_sha256"] == request["source_sha256"]
    assert report["source_unchanged"] is True
    current_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    assert current_sha == request["source_sha256"]
    expected = {case["name"]: case for case in request["cases"]}
    assert len(report["cases"]) == len(expected)
    rows = []
    for result in report["cases"]:
        case = expected.pop(result["name"])
        entities = result.get("entities", [])
        baseline = case["baseline"]
        assert len(entities) == 1 and entities[0]["handle"] == case["name"]
        assert entities[0]["class"] == baseline["entity_type"]
        assert entities[0]["layer"] == baseline["source_layer"]
        regions = result.get("regions", [])
        available = bool(regions) and all(
            region["area_status"] == 0 and region["area_units2"] is not None
            and math.isfinite(region["area_units2"]) and region["area_units2"] > 0
            and region["brep_set_status"] == 0 for region in regions)
        checks = []
        if case["scope"] == "known_hole_control" and len(regions) == 1:
            queries = regions[0]["queries"]
            assert len(queries) == len(case["points"])
            for query, point in zip(queries, case["points"]):
                assert query["point"] == point["xyz"]
                actual = classification(query)
                checks.append({"label": point["label"], "expected": point["expected"],
                               "actual": actual, "matches": actual == point["expected"]})
        rows.append({
            "handle": case["name"], "entity_type": entities[0]["class"],
            "source_layer": entities[0]["layer"],
            "baseline_instance_chain": baseline["instance_chain"],
            "baseline_status": baseline["status"], "baseline_reason": baseline.get("reason"),
            "scope": case["scope"], "method": result["method"],
            "native_surface_available": available,
            "failure": ("getRegionArea_returned_null" if not regions
                        and result["method"] == "AcDbHatch.getRegionArea" else
                        "native_region_unavailable" if not available else None),
            "native_areas": [region["area_units2"] for region in regions],
            "elapsed_case_ms": result["elapsed_ms"],
            "control_checks": checks,
            "spatial_acceptance_of_recovered_object": "NOT_TESTED" if case["scope"] != "known_hole_control" else
                "matches_prior_accepted_loop_control_points",
        })
    assert not expected
    rejected = [row for row in rows if row["baseline_status"] == "unresolved"]
    return {
        "street": request["street"], "source": str(source), "source_sha256": current_sha,
        "native_report": str(report_path),
        "native_report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "baseline_unresolved_tested": len(rejected),
        "baseline_unresolved_with_native_surface": sum(row["native_surface_available"] for row in rejected),
        "qualified_control_points": sum(len(row["control_checks"]) for row in rows),
        "matching_control_points": sum(check["matches"] for row in rows for check in row["control_checks"]),
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = {
        "schema": "green-atlas.native-surface-comparison/1",
        "scope": "Selected real definitions, NOT entire streets or planting acceptance",
        "hatch_status_note": "conversion_status=3 for null HATCH region is assigned by the probe, not an Autodesk returned error code",
        "drawings": [summarize(directory.resolve()) for directory in args.directories],
        "limitations": [
            "Recovered objects: only positive native area and initialized BRep; origin queries have no spatial acceptance value",
            "Hole control points come from previously accepted sampled loops, not an independent visual oracle",
            "No host block/XREF transforms, distance queries, full-drawing throughput, or service placement tested",
            "14F30F control preparation found a self-intersecting prior sampled polygon; kept as unqualified follow-up",
        ],
    }
    with args.output.open("x") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    for drawing in result["drawings"]:
        print(json.dumps({key: value for key, value in drawing.items() if key not in
                          ("cases", "source", "native_report", "native_report_sha256")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
