"""Count unresolved AutoCAD instances in existing native snapshot sidecars.

This streams Green Atlas's admitted evidence, never opens DWG, and does not
infer a missing CAD surface from a visual path or a layer name.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import ijson


def inspect(path: Path) -> dict:
    reasons: Counter[tuple[str, str]] = Counter()
    layers: Counter[tuple[str, str, str]] = Counter()
    unresolved = 0
    with path.open("rb") as source:
        for record in ijson.items(source, "coverage.item", use_float=True):
            if record.get("status") != "unresolved":
                continue
            unresolved += 1
            entity_type = str(record.get("entity_type", ""))
            reason = str(record.get("reason", ""))
            layer = str(record.get("layer", ""))
            reasons[(entity_type, reason)] += 1
            layers[(entity_type, reason, layer)] += 1
    return {
        "snapshot": str(path.resolve()),
        "unresolved": unresolved,
        "reasons": [
            {"entity_type": kind, "reason": reason, "count": count}
            for (kind, reason), count in reasons.most_common()
        ],
        "layers": [
            {
                "entity_type": kind,
                "reason": reason,
                "layer": layer,
                "count": count,
            }
            for (kind, reason, layer), count in layers.most_common()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    snapshots = sorted(args.root.rglob("*.green-atlas.snapshot.json"))
    if not snapshots:
        parser.error("no AutoCAD snapshots found")
    result = {
        "schema": "green-atlas.native-coverage-gap-counts/1",
        "scope": "existing admitted AutoCAD sidecars; not a fresh capture",
        "streets": [inspect(path) for path in snapshots],
    }
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")


if __name__ == "__main__":
    main()
