"""Archive lifecycle A/B evidence; no CAD parsing, project mutation or admission.

The entity ledger includes block definitions and forwarded records, not unique
physical instances. Identity equality is deliberately not geometry equality.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from run_direct_queries import digest


def geometry(reply: dict) -> dict:
    return {
        key: ([{k: v for k, v in obj.items() if k != "prepare_ms"}
               for obj in value] if key == "objects" else value)
        for key, value in reply.items()
        if key not in {"request_id", "request_sha256", "elapsed_ms", "database_modified_flags"}
    }


def archive(roots: list[Path], output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "files"
    evidence.mkdir()
    summary = {
        "scope": "Core lifecycle A/B and selected native queries; not ordinary project acceptance",
        "baseline": str(roots[0]), "runs": [],
    }
    baseline_reply = json.loads((roots[0] / "native.json").read_bytes())
    baseline_inventory = json.loads((roots[0] / "entities-before.json").read_bytes())
    for root in roots:
        receipt = json.loads((root / "receipt.json").read_bytes())
        reply = json.loads((root / "native.json").read_bytes())
        row = {
            "root": str(root), "engine": receipt["engine"],
            "worker_sha256": receipt["binary_sha256"],
            "demand_load": receipt.get("diagnostic_demand_load"),
            "field_evaluation_disabled": receipt.get("diagnostic_disable_field_evaluation", False),
            "preload_modules": receipt.get("preload_modules", []),
            "native_dbmod": reply["database_modified_flags"],
            "query_reply_validated": receipt.get("reply_validated", False),
            "sources_unchanged_at_run": receipt.get("sources_unchanged", False),
            "sources_unchanged_now": all(digest(Path(f[k])) == f["sha256"]
                                         for f in receipt["files"] for k in ("source", "staged")),
            "selected_native_answers_equal_baseline": geometry(reply) == geometry(baseline_reply),
            "artifact_hashes": {},
        }
        for name in ("receipt.json", "native.json", "request.txt", "query.scr", "core.log",
                     "side-graph-before.json", "side-graph-after.json", "loaded-modules.txt",
                     "entities-before.json", "entities-after.json", "build-command.json"):
            source = root / name
            if source.is_file():
                sha = digest(source)
                row["artifact_hashes"][name] = sha
                target = evidence / sha
                if not target.exists():
                    shutil.copy2(source, target)
                if digest(target) != sha:
                    raise ValueError("Archived evidence differs")
        inventory_path = root / "entities-before.json"
        if inventory_path.is_file():
            before = json.loads(inventory_path.read_bytes())
            after = json.loads((root / "entities-after.json").read_bytes())
            row["entity_identity_ledger_equal_baseline"] = before == baseline_inventory
            row["entity_identity_ledger_unchanged_during_query"] = before == after
            row["entity_open_errors"] = sum(e["status"] != 0 for d in before["databases"] for e in d["entities"])
            row["proxy_entities"] = sum(e.get("class") == "AcDbProxyEntity" for d in before["databases"] for e in d["entities"])
        summary["runs"].append(row)
    summary["baseline_entity_entries_per_database"] = [
        {"name": d["name"], "entries": len(d["entities"])} for d in baseline_inventory["databases"]]
    summary["selected_objects"] = len(baseline_reply["objects"])
    summary["query_points_per_object"] = baseline_reply["point_count"]
    (output / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--run", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = archive([args.baseline, *args.run], args.output)
    print(json.dumps({"output": str(args.output), "runs": len(report["runs"]),
                      "selected_answers_equal": all(r["selected_native_answers_equal_baseline"] for r in report["runs"]),
                      "originals_unchanged": all(r["sources_unchanged_now"] for r in report["runs"])}))
