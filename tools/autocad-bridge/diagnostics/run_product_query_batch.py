"""Exercise the production worker and API codec on isolated package copies.

This is selected-instance measurement acceptance, not a full source capture or
placement acceptance. It deliberately does not change an existing project.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

from app.native_query.contracts import NativeObjectQuery
from app.native_query.protocol import decode_reply, encode_request
from run_direct_queries import digest, quoted, run_core


def stage_package(baseline: dict, root: Path) -> tuple[Path, list[dict]]:
    """Clone the explicitly inventoried immutable fixture; never resolve new paths."""
    package = Path(baseline["package"]).resolve(strict=True)
    source = Path(baseline["source"]).resolve(strict=True)
    files = []
    for record in baseline["files"]:
        original = Path(record["source"]).resolve(strict=True)
        if digest(original) != record["sha256"]:
            raise ValueError("Source differs from the selected package baseline")
        clone = root / "package" / original.relative_to(package)
        clone.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["/bin/cp", "-c", "-p", str(original), str(clone)], check=True)
        files.append({"source": str(original), "staged": str(clone), "sha256": record["sha256"]})
    return root / "package" / source.relative_to(package), files


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    for name in ("baseline-receipt", "case", "worker", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline_receipt.read_text())
    case = json.loads(args.case.read_text())
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error("Output must be empty; existing evidence is never replaced")
    if shutil.disk_usage(root).free < 500_000_000:
        parser.error("Insufficient free space")
    # Explicit previously inventoried package, independent APFS clones. Hashes
    # must match the baseline; no fresh search/substitution for missing XREFs.
    drawing, files = stage_package(baseline, root)
    bundle = root / "QueryWorker.dbx"
    shutil.copytree(args.worker, bundle)
    subprocess.run(["codesign", "--verify", "--strict", str(bundle)], check=True)
    query = NativeObjectQuery(
        request_id=uuid4().hex, source_sha256=digest(drawing), **case
    )
    report = root / "native.json"
    request_bytes = encode_request(query, report)
    request = root / "request.txt"
    request.write_bytes(request_bytes)
    script = root / "query.scr"
    script.write_text(
        f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload {quoted(bundle)})\nGAQUERYOBJECTS\n{request}\n'
        f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)\n'
        '_QUIT\n_Y\n\n'
    )
    receipt = {
        "scope": "production native command + API codec; selected instances only",
        "files": files, "native_binary_sha256": digest(bundle / "Contents/MacOS/GreenAtlasBridge"),
        "request": query.model_dump(mode="json"),
    }
    try:
        receipt["engine"] = run_core(root, drawing, script, 180)
        result = decode_reply(report.read_bytes(), query, request_bytes=request_bytes)
        receipt["validated_reply"] = result.model_dump(mode="json", by_alias=True)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        receipt["error"] = str(error)
    finally:
        receipt["sources_unchanged"] = all(
            digest(Path(r["source"])) == r["sha256"] == digest(Path(r["staged"]))
            for r in files
        )
        (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(root), "engine": receipt.get("engine"),
                      "error": receipt.get("error"), "sources_unchanged": receipt["sources_unchanged"],
                      "reply_validated": "validated_reply" in receipt}, ensure_ascii=False))
    return int("error" in receipt or not receipt["sources_unchanged"])


if __name__ == "__main__":
    raise SystemExit(main())
