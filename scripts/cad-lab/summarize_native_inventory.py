"""Summarize GA AutoLISP TSV; definition counts are not instance coverage."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path


def summarize(path: Path) -> dict:
    types = Counter()
    layers = {}
    xrefs = []
    definitions = 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t", quoting=csv.QUOTE_NONE)
        if rows.fieldnames != ["record", "owner", "handle", "type", "layer", "reference"]:
            raise ValueError("Unexpected inventory header")
        for row in rows:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed inventory row")
            if row["record"] == "block":
                definitions += 1
                if int(row["type"]) & 4:
                    xrefs.append({"name": row["owner"], "path": row["reference"],
                                  "flags": int(row["type"])})
            elif row["record"] == "entity":
                types[row["type"]] += 1
                layers.setdefault(row["layer"], Counter())[row["type"]] += 1
            else:
                raise ValueError("Unknown record type")
    return {"scope": "native database definitions, not expanded instance coverage",
            "definitions": definitions, "entities": sum(types.values()),
            "entity_types": dict(sorted(types.items())), "xref_definitions": xrefs,
            "layers": {key: dict(sorted(value.items())) for key, value in sorted(layers.items())},
            "geometry_extracted": False, "calculation_qualified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.inventory), ensure_ascii=False, indent=2))
