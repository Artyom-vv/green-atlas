"""Stream-compare AutoCAD topology captures without interpreting CAD geometry.

Usage: python compare_native_snapshots.py OLD.geometry.json NEW.geometry.json
The input must be two captures of the same saved DWG; the report includes
capture modes because different modes are not automatically equivalent.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import ijson


def item(path: Path, prefix: str) -> Any:
    with path.open("rb") as stream:
        return next(ijson.items(stream, prefix))


def coverage(path: Path):
    with path.open("rb") as stream:
        yield from ijson.items(stream, "coverage.item")


def identity(row: dict[str, Any]) -> tuple[str, tuple[str, ...], str, str]:
    return (
        row["handle"],
        tuple(row["instance_chain"]),
        row["entity_type"],
        row["source_layer"],
    )


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    old_path, new_path = (Path(value) for value in sys.argv[1:])
    old_source = item(old_path, "source")
    new_source = item(new_path, "source")
    if old_source["sha256"] != new_source["sha256"]:
        print("Refusing to compare captures of different source bytes", file=sys.stderr)
        return 2

    old_rows = {identity(row): row for row in coverage(old_path)}
    transitions: Counter[tuple[str, str, str, str, str]] = Counter()
    recovered_layers: Counter[str] = Counter()
    lost_layers: Counter[str] = Counter()
    building_transitions: Counter[tuple[str, str, str]] = Counter()
    recovered_samples: list[dict[str, Any]] = []
    lost_samples: list[dict[str, Any]] = []
    added = 0
    new_count = 0
    for row in coverage(new_path):
        new_count += 1
        previous = old_rows.pop(identity(row), None)
        if previous is None:
            added += 1
            continue
        old_status, new_status = previous["status"], row["status"]
        transitions[(
            previous["entity_type"], old_status, new_status,
            previous.get("method") or "", row.get("method") or "",
        )] += 1
        if old_status == "unresolved" and new_status == "native":
            recovered_layers[row["layer"]] += 1
            if len(recovered_samples) < 12:
                recovered_samples.append({
                    "handle": row["handle"], "instance_chain": row["instance_chain"],
                    "layer": row["layer"], "old_reason": previous.get("reason"),
                })
        elif old_status == "native" and new_status == "unresolved":
            lost_layers[row["layer"]] += 1
            lost_samples.append({
                "handle": row["handle"], "instance_chain": row["instance_chain"],
                "layer": row["layer"], "new_reason": row.get("reason"),
            })
        if "здан" in row["layer"].lower():
            building_transitions[(old_status, new_status, row["entity_type"])] += 1

    report = {
        "source_sha256": old_source["sha256"],
        "old_capture_mode": item(old_path, "capture_mode"),
        "new_capture_mode": item(new_path, "capture_mode"),
        "old_summary": item(old_path, "summary"),
        "new_summary": item(new_path, "summary"),
        "old_coverage_rows": len(old_rows) + new_count - added,
        "new_coverage_rows": new_count,
        "new_identities": added,
        "missing_identities": len(old_rows),
        "transitions": [
            {"entity_type": kind, "old_status": old_status,
             "new_status": new_status, "old_method": old_method,
             "new_method": new_method, "count": count}
            for (kind, old_status, new_status, old_method, new_method), count
            in transitions.most_common()
        ],
        "recovered_layers_top_20": recovered_layers.most_common(20),
        "lost_layers_top_20": lost_layers.most_common(20),
        "recovered_samples": recovered_samples,
        "lost_samples": lost_samples,
        "building_transitions": [
            {"old_status": old, "new_status": new,
             "entity_type": kind, "count": count}
            for (old, new, kind), count in building_transitions.most_common()
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
