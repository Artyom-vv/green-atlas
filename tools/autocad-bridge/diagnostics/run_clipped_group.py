"""Native split-curve proposals for explicit survey-clipped groups; no auto acceptance."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from run_direct_queries import digest, quoted, run_core
from run_product_query_batch import stage_package


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    for name in ("baseline-receipt", "case", "bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error("Output must be empty")
    if shutil.disk_usage(root).free < 500_000_000:
        parser.error("Insufficient free space")
    case = json.loads(args.case.read_text())
    drawing, files = stage_package(json.loads(args.baseline_receipt.read_text()), root)
    bundle = root / "ClippedGroup.dbx"
    shutil.copytree(args.bundle, bundle)
    subprocess.run(["codesign", "--verify", "--strict", str(bundle)], check=True)
    request = root / "request.txt"
    request.write_text("\n".join([
        str(root / "native.json"), case["boundary"], str(len(case["routes"])),
        *case["routes"], str(len(case["points"])),
        *[" ".join(map(str, point)) for point in case["points"]], "",
    ]))
    script = root / "query.scr"
    script.write_text(
        f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload {quoted(bundle)})\nGAGROUPCLIP\n{request}\n'
        f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)\n'
        '_QUIT\n_Y\n\n'
    )
    receipt = {
        "scope": "native boundary split proposals, not confirmed obstacle semantics",
        "case": case, "files": files,
        "native_binary_sha256": digest(bundle / "Contents/MacOS/GreenAtlasBridge"),
    }
    try:
        receipt["engine"] = run_core(root, drawing, script, 180)
        receipt["native"] = json.loads((root / "native.json").read_text())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        receipt["error"] = str(error)
    finally:
        receipt["sources_unchanged"] = all(
            digest(Path(row["source"])) == row["sha256"] == digest(Path(row["staged"]))
            for row in files
        )
        (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in receipt.items() if key != "files"}, ensure_ascii=False))
    return int("error" in receipt or not receipt["sources_unchanged"])


if __name__ == "__main__":
    raise SystemExit(main())
