"""Read-only forensic audit; never a geometry provider or source-file repair.

Correlates failed native entity handles with a few raw DXF fields and checks
whether exact XREF basenames exist in the supplied package. Does not import
ezdxf, create geometry, guess matches, or rewrite drawings/snapshots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


def records(path: Path):
    record = []
    with path.open(encoding="utf-8", errors="strict") as stream:
        while code := stream.readline():
            value = stream.readline().strip()
            code = int(code.strip())
            if code == 0:
                if record:
                    yield record
                record = []
            record.append((code, value))
        if record:
            yield record


def audit(source: Path, probe_path: Path, package: Path):
    probe = json.loads(probe_path.read_bytes())
    failures = [row for row in probe["coverage"] if row["status"] == "unresolved"]
    polylines = {row["handle"] for row in failures if row["entity_type"] == "AcDbPolyline"}
    classification = {}
    hatch_layouts = Counter()
    for record in records(source):
        if record[0] == (0, "HATCH"):
            loop_types = [int(value) for code, value in record if code == 92]
            hatch_layouts["polyline_loops_only" if loop_types and all(t & 2 for t in loop_types)
                          else "edge_or_mixed_loops"] += 1
        if record[0] != (0, "LWPOLYLINE"):
            continue
        fields = defaultdict(list)
        for code, value in record:
            fields[code].append(value)
        handle = fields[5][0]
        if handle not in polylines:
            continue
        xs, ys = fields[10], fields[20]
        exact_point = (
            bool(xs) and len(xs) == len(ys)
            and len({float(x) for x in xs}) == 1
            and len({float(y) for y in ys}) == 1
            and all(float(b) == 0 for b in fields[42])
        )
        classification[handle] = "exact_zero_length" if exact_point else "requires_native_investigation"
    names = defaultdict(list)
    for path in package.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".dwg", ".dxf"}:
            names[unicodedata.normalize("NFC", path.name).casefold()].append(str(path))
    references = []
    for ref in probe["xref_dependencies"]:
        basename = ref["stored_path"].replace("\\", "/").rsplit("/", 1)[-1]
        matches = names[unicodedata.normalize("NFC", basename).casefold()]
        references.append({"block": ref["block_name"], "stored_path": ref["stored_path"],
                           "status": ref["status"], "exact_basename_candidates": matches})
    return {
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "probe_source_sha256": probe["source"]["sha256"],
        "unresolved_by_type": dict(Counter(row["entity_type"] for row in failures)),
        "hatch_definitions": dict(hatch_layouts),
        "polyline_instances": dict(Counter(classification.get(row["handle"], "not_in_source")
            for row in failures if row["entity_type"] == "AcDbPolyline")),
        "nonzero_polyline_handles": [h for h, kind in classification.items() if kind != "exact_zero_length"],
        "xref_candidates": references,
        "xref_with_candidates": sum(bool(row["exact_basename_candidates"]) for row in references),
        "note": "Basename candidates require review; no paths or geometry were changed.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("probe", type=Path)
    parser.add_argument("package", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.source, args.probe, args.package)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "xref_candidates"}, ensure_ascii=False))
