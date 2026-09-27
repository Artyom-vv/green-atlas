"""Group reachable ACIS payloads for bounded SDK qualification, not geometry.

Consumes inventory_full_acis.py output. Hash equality only deduplicates identical
payload tests; every INSERT placement remains a separate recorded occurrence.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

from ezdxf.acis import sab, sat


def summarize(inventory: Path, output: Path):
    data = json.loads(inventory.read_text(encoding="utf8"))
    output.mkdir(exist_ok=False)
    instances = Counter(item["block"] for item in data["acis_instances"])
    instances["*Model_Space"] += 1
    by_hash = defaultdict(list)
    missing_payload = []
    active_count = 0
    for row in data["acis"]:
        count = instances[row["layout"]]
        if not count:
            continue
        active_count += count
        record = {**row, "instances": count}
        if row.get("sha256"):
            by_hash[row["sha256"]].append(record)
        else:
            missing_payload.append(record)
    signatures = defaultdict(list)
    cases = []
    for digest, owners in sorted(by_hash.items()):
        path = Path(owners[0]["path"])
        case = dict(name=digest, path=str(path), sha256=digest, owners=owners,
                    instances=sum(row["instances"] for row in owners), expected=None)
        try:
            raw = sab.parse_sab(path.read_bytes()) if path.suffix == ".sab" else sat.parse_sat(
                path.read_text(encoding="utf8").splitlines())
            names = []
            for entity in raw.entities:
                if entity.name in {"Begin-of-ACIS-History-Data", "End-of-ACIS-data"}:
                    break
                names.append(entity.name)
            counts = Counter(names)
            case.update(source_faces=counts["face"], source_loops=counts["loop"],
                        source_types=dict(sorted(counts.items())),
                        units_in_mm=raw.header.units_in_mm)
            signature = tuple(sorted(name for name in counts if name.endswith(("-curve", "-surface"))))
            signatures[signature].append(digest)
        except Exception as error:
            case["parse_error"] = f"{type(error).__name__}: {error}"
        cases.append(case)
    report = dict(source=data["source"], scope="reachable ACIS structure; not geometry fidelity",
                  active_instances=active_count, unique_payloads=len(cases),
                  missing_payload=missing_payload,
                  parse_errors=sum("parse_error" in case for case in cases),
                  signatures=[dict(types=list(key), payloads=values) for key, values in signatures.items()])
    (output / "cases.json").write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    (output / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf8")
    print(json.dumps(report))


if __name__ == "__main__":
    summarize(*map(Path, sys.argv[1:]))
