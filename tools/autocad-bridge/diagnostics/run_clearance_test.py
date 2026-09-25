"""Run clearance controls in an owned Core process, never the user's session."""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from run_direct_queries import CORE, digest, quoted, run_core


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--cases", type=Path)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="ga-clearance-", dir="/private/tmp"))
    print(str(root), flush=True)
    if shutil.disk_usage(root).free < 400_000_000:
        raise RuntimeError("Not enough disk space for isolated controls")
    source = args.source or CORE.parents[4] / "Resources/UserDataCache/en-us/Template/acadiso.dwt"
    drawing = root / ("Drawing.dxf" if source.suffix.lower() == ".dxf" else "Drawing.dwg")
    before = digest(source)
    subprocess.run(["/bin/cp", "-c", "-p", str(source), str(drawing)], check=True)
    refs = []
    if args.source:
        for path in sorted((source.parent / "00.3_Ссылки").glob("*.dwg")):
            target = root / "00.3_Ссылки" / path.name
            target.parent.mkdir(exist_ok=True)
            subprocess.run(["/bin/cp", "-c", "-p", str(path), str(target)], check=True)
            refs.append((path, target, digest(path)))
    cases = json.loads(args.cases.read_bytes()) if args.cases else []
    bundle = root / "Clearance.dbx"
    shutil.copytree(args.bundle, bundle)
    subprocess.run(["codesign", "--verify", "--strict", str(bundle)], check=True)
    request = root / "request.txt"
    request.write_text("\n".join([str(root / "native.json"), str(drawing) if args.source else "", str(len(cases)),
        *[value for case in cases for value in (case["route"], case["mode"], str(case["distance"]))], ""]))
    script = root / "query.scr"
    script.write_text(
        f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload {quoted(bundle)})\nGACLEARANCETEST\n{request}\n'
        f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)\n_QUIT\n_Y\n\n')
    receipt = {"scope": "isolated clearance kernel, not UI deployment", "source_sha256": before,
               "binary_sha256": digest(bundle / "Contents/MacOS/GreenAtlasBridge")}
    try:
        bootstrap = root / "Empty.dwg"
        shutil.copy2(CORE.parents[4] / "Resources/UserDataCache/en-us/Template/acadiso.dwt", bootstrap)
        receipt["engine"] = run_core(root, bootstrap, script, 180)
        receipt["result"] = json.loads((root / "native.json").read_bytes())
    finally:
        receipt["source_unchanged"] = digest(source) == before == digest(drawing)
        receipt["references_unchanged"] = all(digest(a) == sha == digest(b) for a, b, sha in refs)
        (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    return int(bool(receipt["result"].get("error")) or receipt["engine"]["exit_code"] != 0
               or not receipt["source_unchanged"] or not receipt["references_unchanged"])


if __name__ == "__main__":
    raise SystemExit(main())
