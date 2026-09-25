"""Compare native capture coverage/area identities, not a CAD parser or safety oracle."""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

import ijson


def stream_capture(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rb")
    if not path.is_file() and Path(str(path) + ".gz").is_file():
        return gzip.open(str(path) + ".gz", "rb")
    return path.open("rb")


def rows(path: Path, collection: str):
    with stream_capture(path) as stream:
        yield from ijson.items(stream, collection + ".item", use_float=True)


def identity(row: dict) -> tuple:
    return row["handle"], tuple(row["instance_chain"]), row["entity_type"], row["source_layer"]


def region_identity(row: dict) -> tuple:
    return row["handle"], tuple(row["instance_chain"]), tuple(row.get("source_handles", []))


def region_measurements(path: Path) -> dict:
    # Only two scalar measurements are compared here. Retaining all loop
    # coordinates from both captures would turn a 1 GB audit into a multi-GB
    # graph and make the diagnostic itself fail on a full referenced street.
    return {
        region_identity(row): {
            field: row.get(field)
            for field in ("native_area_units2", "native_perimeter_units")
        }
        for row in rows(path, "regions")
    }


def compare(before: Path, after: Path) -> dict:
    old = {identity(row): row for row in rows(before, "coverage")}
    new = {identity(row): row for row in rows(after, "coverage")}
    changes = []
    transitions = Counter()
    for key in sorted(old.keys() & new.keys()):
        left, right = old[key], new[key]
        transitions[(left["status"], right["status"])] += 1
        if left["status"] != right["status"]:
            changes.append({
                "handle": key[0], "instance_chain": key[1], "entity_type": key[2],
                "source_layer": key[3], "before": left["status"], "after": right["status"],
                "before_reason": left.get("reason"), "after_reason": right.get("reason"),
            })
    old_regions = region_measurements(before)
    new_regions = region_measurements(after)
    # Measurements stay visible even when the structure gate passes. It does
    # not certify numerical equivalence, containment, transforms or planting.
    measurement_delta = {}
    for field in ["native_area_units2", "native_perimeter_units"]:
        deltas = [abs(old_regions[k][field] - new_regions[k][field])
                  for k in old_regions.keys() & new_regions.keys()
                  if isinstance(old_regions[k].get(field), (float, int))
                  and isinstance(new_regions[k].get(field), (float, int))]
        measurement_delta[field] = max(deltas, default=0.0)
    missing_regions = sorted(old_regions.keys() - new_regions.keys())
    added_regions = sorted(new_regions.keys() - old_regions.keys())
    return {
        "scope": "coverage and region identity consistency; not geometric or product acceptance",
        "same_instance_identities": old.keys() == new.keys(),
        "missing_instances": sorted(old.keys() - new.keys()),
        "added_instances": sorted(new.keys() - old.keys()),
        "status_changes": changes,
        "transitions": [{"before": a, "after": b, "count": n}
                        for (a, b), n in sorted(transitions.items())],
        "missing_regions": missing_regions,
        "added_regions": added_regions,
        "maximum_native_measurement_delta_units": measurement_delta,
        "structure_consistent": old.keys() == new.keys() and not changes
        and not missing_regions and not added_regions,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.before, args.after)
    with args.output.open("x") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({k: report[k] for k in ["same_instance_identities", "structure_consistent"]}))
