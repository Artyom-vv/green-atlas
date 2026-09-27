"""Choose explicit real CAD handles and query points, not an import fallback.

Existing native evidence is used ONLY to choose points/illustrate the location.
Its projected polygon is not an independent oracle for the new native query.
The experiment opens the original DWG through AutoCAD and uses its entities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from shapely.geometry import Polygon


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    evidence = next((root / "fixtures/cad/lct-dataset").rglob(
        "03_10004141_Проектные решения.dwg.green-atlas.geometry.json"))
    probe = json.loads(evidence.read_bytes())
    source = evidence.parent / "00.1_10004141_Топография.dwg"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    handles = ["7BF", "78B3", "6375"]
    selected = {item["handle"]: item for item in probe["paths"]
                if item["source_layer"] == "Здания" and item["instance_chain"] == ["202E"]
                and item["handle"] in handles}
    cases = []
    rows = [str(source), str(output / "native-report.json"), str(len(handles))]
    for handle in handles:
        item = selected[handle]
        coordinates = item["coordinates"]
        polygon = Polygon(coordinates)
        center = polygon.representative_point()
        left, bottom, right, top = polygon.bounds
        # Deliberately far from corners; offsets merely select test points.
        a, b = max(zip(coordinates, coordinates[1:]),
                   key=lambda pair: math.dist(pair[0], pair[1]))
        length = math.dist(a, b)
        mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, a[2]]
        nx, ny = -(b[1] - a[1]) / length, (b[0] - a[0]) / length
        points = [
            ("interior_candidate", [center.x, center.y, a[2]]),
            ("outside_bounds", [right + 5, top + 5, a[2]]),
            ("edge_midpoint", mid),
            ("edge_side_a", [mid[0] + nx * .25, mid[1] + ny * .25, a[2]]),
            ("edge_side_b", [mid[0] - nx * .25, mid[1] - ny * .25, a[2]]),
        ]
        case = {"name": handle, "source_handle": handle,
                "host_instance_chain": item["instance_chain"],
                "source_layer": item["source_layer"],
                "previous_closed_flag": item["closed"],
                "previous_endpoint_gap": math.dist(coordinates[0], coordinates[-1]),
                "coordinates_for_location_only": coordinates,
                "points": [{"label": label, "xyz": xyz} for label, xyz in points]}
        cases.append(case)
        rows.extend([handle, "1", handle, str(len(points))])
        rows.extend(" ".join(format(v, ".17g") for v in point) for _, point in points)
    receipt = {"source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "host_source": probe["source"], "previous_evidence": str(evidence),
               "cases": cases, "scope": "native queries in source XREF database; host transform not yet qualified"}
    (output / "request.txt").write_text("\n".join(rows) + "\n")
    (output / "cases.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({"request": str(output / "request.txt"), "source": str(source),
                      "handles": handles}, ensure_ascii=False))


if __name__ == "__main__":
    main()
