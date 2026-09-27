"""Audit AutoCAD building-path endpoint adjacency; never repair source geometry."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import ijson


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--screen", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    screened = json.loads(args.screen.read_text(encoding="utf-8"))["streets"]
    output = {
        "schema": "green-atlas.building-endpoint-audit/1",
        "scope": "read-only AutoCAD sidecar endpoint screening; not a closure rule",
        "streets": [],
    }
    for street in screened:
        scale = street.get("source.metres_per_unit")
        if scale is None:
            with Path(street["snapshot_path"]).open("rb") as source:
                scale = next(ijson.items(source, "source"))["metres_per_unit"]
        scale = float(scale)
        records = [
            record
            for record in street["building_like_records"]
            if record["section"] == "paths"
            and "здания" in record["source_layer"].casefold()
            and "части" not in record["source_layer"].casefold()
        ]
        open_paths = [record for record in records if not record["closed"]]
        endpoints = [
            (path, endpoint, path[label])
            for path in open_paths
            for endpoint, label in (("start", "first"), ("end", "last"))
        ]
        rows = []
        for path, endpoint, point in endpoints:
            candidates = [
                (math.dist(point[:2], other[:2]) * scale, other_path, other_end)
                for other_path, other_end, other in endpoints
                if other_path is not path
                and other_path["source_layer"] == path["source_layer"]
            ]
            distance, nearest_path, nearest_end = (
                min(candidates, key=lambda item: item[0])
                if candidates
                else (None, None, None)
            )
            rows.append(
                {
                    "handle": path["handle"],
                    "layer": path["source_layer"],
                    "endpoint": endpoint,
                    "vertex_count": path.get("vertex_count"),
                    "own_endpoint_gap_m": math.dist(path["first"][:2], path["last"][:2])
                    * scale,
                    "nearest_other_endpoint_m": distance,
                    "nearest_other_handle": nearest_path["handle"]
                    if nearest_path
                    else None,
                    "nearest_other_end": nearest_end,
                }
            )
        output["streets"].append(
            {
                "source": street.get("source.path"),
                "source_sha256": street.get("source.sha256"),
                "source_matches_disk": street.get("source_sha256_matches_current_file"),
                "explicit_building_open_paths": len(open_paths),
                "explicit_building_closed_paths": len(records) - len(open_paths),
                "endpoint_adjacency_under_1mm": sum(
                    row["nearest_other_endpoint_m"] is not None
                    and row["nearest_other_endpoint_m"] < 0.001
                    for row in rows
                ),
                "endpoint_adjacency_under_10mm": sum(
                    row["nearest_other_endpoint_m"] is not None
                    and row["nearest_other_endpoint_m"] < 0.01
                    for row in rows
                ),
                "endpoint_adjacency_under_100mm": sum(
                    row["nearest_other_endpoint_m"] is not None
                    and row["nearest_other_endpoint_m"] < 0.1
                    for row in rows
                ),
                "nearest_endpoint_buckets": dict(
                    Counter(
                        "none"
                        if row["nearest_other_endpoint_m"] is None
                        else "under_1mm"
                        if row["nearest_other_endpoint_m"] < 0.001
                        else "under_10mm"
                        if row["nearest_other_endpoint_m"] < 0.01
                        else "under_100mm"
                        if row["nearest_other_endpoint_m"] < 0.1
                        else "under_1m"
                        if row["nearest_other_endpoint_m"] < 1
                        else "1m_or_more"
                        for row in rows
                    )
                ),
                "endpoints": rows,
            }
        )
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
