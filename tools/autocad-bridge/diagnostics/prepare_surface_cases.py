"""Prepare bounded native surface experiments from existing dataset evidence.

No DWG parser or geometry repair. Existing accepted loops select positive-control
points only; they are not an independent oracle for previously rejected objects.
Rejected objects initially receive an explicitly unqualified probe at the origin:
this pass measures native conversion availability, NOT their spatial correctness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from shapely.geometry import Polygon


CASES = {
    "4.": ("41C2", ["2013E", "2016E", "46BB1", "4830D"]),
    "6.": ("26259", ["739B8", "BE06C", "26260", "2626A", "138AA6", "143E46"]),
    "16.": ("14F316", ["14F2CA", "14F2CD", "14F2CF", "14F2D2"]),
}
# Original control 14F30F has a self-intersecting accepted sampled polygon.
# Preserve it as a separate, unqualified follow-up, not a silently dropped case.
FOLLOWUPS = {"16.": ["14F30F"]}


def control_points(region: dict) -> list[dict]:
    outer = [loop for loop in region["loops"] if loop["role"] == "outer"]
    holes = [loop for loop in region["loops"] if loop["role"] == "hole"]
    assert len(outer) == 1 and holes
    shell = outer[0]["coordinates"]
    shape = Polygon(shell, [loop["coordinates"] for loop in holes])
    assert shape.is_valid and not shape.is_empty
    filled = shape.representative_point()
    hole = Polygon(holes[0]["coordinates"]).representative_point()
    assert not shape.covers(hole)
    _, _, right, top = shape.bounds
    z = shell[0][2]
    return [
        {"label": "known_filled_area", "xyz": [filled.x, filled.y, z], "expected": "face"},
        {"label": "known_hole", "xyz": [hole.x, hole.y, z], "expected": "outside"},
        {"label": "outside_bounds", "xyz": [right + 5, top + 5, z], "expected": "outside"},
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--streets", nargs="+", choices=CASES, default=list(CASES))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    baseline = root / "artifacts/geometry-audit-20260922/report.json"
    audit = json.loads(baseline.read_bytes())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"scope": "native definition-space surface availability; no host transforms",
                "baseline": str(baseline), "drawings": []}
    for drawing in audit["drawings"]:
        prefix = drawing["street"].split()[0]
        if prefix not in args.streets:
            continue
        control, failures = CASES[prefix]
        followups = FOLLOWUPS.get(prefix, [])
        source = Path(drawing["source"])
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        assert sha == drawing["sha256"], "source differs from baseline"
        evidence = Path(str(source) + ".green-atlas.geometry.json")
        data = json.loads(evidence.read_bytes())
        wanted = {control, *failures, *followups}
        coverage = {}
        for item in data["coverage"]:
            if item["handle"] in wanted:
                coverage.setdefault(item["handle"], item)
        region = next(item for item in data["regions"]
                      if item["handle"] == control and not item["instance_chain"])
        cases = []
        for handle in [control, *failures, *followups]:
            record = coverage[handle]
            assert not record.get("xref_dependency_id"), "handle belongs to another database"
            positive = handle == control
            assert record["status"] == ("native" if positive or handle in followups else "unresolved")
            points = control_points(region) if positive else [
                {"label": "unqualified_origin_probe", "xyz": [0, 0, 0], "expected": None}]
            cases.append({"name": handle, "baseline": record,
                          "scope": ("known_hole_control" if positive else
                                    "sampled_projection_invalid_followup" if handle in followups else
                                    "conversion_only"),
                          "previous_area": region["native_area_units2"] if positive else None,
                          "points": points})
        directory = output / prefix.rstrip(".")
        directory.mkdir()
        rows = [str(source), str(directory / "native-report.json"), str(len(cases))]
        for case in cases:
            rows.extend([case["name"], "1", case["name"], str(len(case["points"]))])
            rows.extend(" ".join(format(v, ".17g") for v in point["xyz"])
                        for point in case["points"])
        (directory / "request.txt").write_text("\n".join(rows) + "\n")
        receipt = {"street": drawing["street"], "source": str(source),
                   "source_sha256": sha, "evidence": str(evidence), "cases": cases}
        (directory / "cases.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        manifest["drawings"].append({"street": drawing["street"],
                                     "request": str(directory / "request.txt"),
                                     "handles": [case["name"] for case in cases]})
        print(json.dumps(manifest["drawings"][-1], ensure_ascii=False), flush=True)
        del data
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
