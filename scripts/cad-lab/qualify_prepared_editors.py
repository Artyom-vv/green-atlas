"""Sequential full-editor checks for every already assembled independent group.

This does not resolve ambiguous references, bind more sources, or change originals.
Each group gets a separate new database and one 4-GiB/300-second process tree.
"""

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))
from app.cad_import.cache import file_sha256
from app.cad_import.process import run_converter
from app.cad_intake.prepare_policy import PREPARE_POLICY


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qualification", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    groups = json.loads(args.qualification.read_text(encoding="utf8"))
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    groups.sort(key=lambda group: group.get("assembly", {}).get("bytes", 0))
    for group in groups:
        assembly = group.get("assembly", {})
        if not assembly.get("all_active_references_resolved"):
            continue
        key = hashlib.sha256(group["root"].encode()).hexdigest()[:16]
        folder = output / key
        folder.mkdir(exist_ok=True)
        receipt = folder / "process.json"
        if receipt.exists():
            results.append(json.loads(receipt.read_text(encoding="utf8")))
            continue
        source = Path(assembly["output"]).resolve(strict=True)
        if file_sha256(source) != assembly["sha256"]:
            raise ValueError(f"Prepared source changed: {source}")
        result = {"street": group["street"], "root": group["root"], "source": str(source),
                  "source_sha256": assembly["sha256"], "source_bytes": source.stat().st_size,
                  "directory": str(folder), "mapping_verified": False}
        print(json.dumps({"starting": key, "street": group["street"]}, ensure_ascii=False), flush=True)
        try:
            run = run_converter(
                [sys.executable, str(ROOT / "scripts/cad-lab/probe_prepared_editor.py"), str(source), str(folder)],
                folder / "report.json", folder / "probe.log", PREPARE_POLICY,
                environment={**os.environ, "PYTHONUTF8": "1"},
            )
            result["process"] = asdict(run)
        except Exception as error:
            result["process_error"] = str(error)
        if (folder / "report.json").exists():
            report = json.loads((folder / "report.json").read_text(encoding="utf8"))
            result["project_id"] = report.get("project_id")
            result["elapsed_seconds"] = report.get("elapsed_seconds")
            result["published"] = report.get("published")
            result["intake_status"] = report.get("intake", {}).get("status")
            result["preparation_status"] = report.get("preparation", {}).get("status")
        result["status"] = (
            "editable_draft" if (result.get("published") or {}).get("original_bytes_preserved")
            else "committed_verification_incomplete" if result.get("preparation_status") == "completed"
            else "not_published"
        )
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        results.append(result)
        (output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({"finished": key, "status": result["status"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
