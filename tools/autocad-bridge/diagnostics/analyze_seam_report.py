"""Locate endpoint-orientation ambiguities in a read-only AcBr seam report.

This inspects Autodesk-reported endpoints and identities only. It never creates
or repairs geometry and is not part of the import path.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def distance(left: list[float], right: list[float]) -> float:
    return math.dist(left, right)


def analyze(edges: list[dict], tolerance: float) -> list[dict]:
    observations = []
    for reverse_first in (False, True):
        first_samples = edges[0]["samples"]
        start = first_samples[-1] if reverse_first else first_samples[0]
        cursor = first_samples[0] if reverse_first else first_samples[-1]
        for index, edge in enumerate(edges[1:], start=1):
            samples = edge["samples"]
            forward = distance(cursor, samples[0])
            reverse = distance(cursor, samples[-1])
            if (forward <= tolerance) == (reverse <= tolerance):
                previous = edges[index - 1]["native_edge_probe"]
                current = edge["native_edge_probe"]
                observations.append({
                    "reverse_first": reverse_first,
                    "edge_index": index,
                    "forward_gap_m": forward,
                    "reverse_gap_m": reverse,
                    "previous_vertices": [
                        previous["vertex1"].get("loop_local_identity"),
                        previous["vertex2"].get("loop_local_identity"),
                    ],
                    "current_vertices": [
                        current["vertex1"].get("loop_local_identity"),
                        current["vertex2"].get("loop_local_identity"),
                    ],
                    "current_start": samples[0],
                    "current_end": samples[-1],
                })
                break
            cursor = samples[-1] if forward <= tolerance else samples[0]
        else:
            observations.append({
                "reverse_first": reverse_first,
                "joined": True,
                "closure_gap_m": distance(cursor, start),
            })
    return observations


def main() -> None:
    report = json.loads(Path(sys.argv[1]).read_text())
    tolerance = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0001
    for case in report["cases"]:
        result = {"case": case["name"], "source_unchanged": report["source_unchanged"]}
        if not case.get("regions"):
            result["error"] = "no native region"
        else:
            result["regions"] = []
            for region in case["regions"]:
                for face in region["topology_probe"]["faces"]:
                    for loop in face["loops"]:
                        result["regions"].append({
                            "native_area_m2": region["area_units2"],
                            "role": loop["role"],
                            "edge_count": len(loop["edges"]),
                            "attempts": analyze(loop["edges"], tolerance),
                        })
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
