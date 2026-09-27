"""Run bounded native per-handle diagnostics on a DWG or DXF, never modify it."""

import argparse
import json
import shutil
from pathlib import Path

from run_dxf_roundtrip import DEFAULT_CORE, digest, lisp, run_core


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--command", default="GACLOSEFILE", choices=["GACLOSEFILE", "GATOPOFILE", "GASEAMFILE"])
    parser.add_argument("--handle", action="append", default=[])
    parser.add_argument("--pair", action="append", default=[], help="Two handles separated by a comma")
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    cases = [[handle] for handle in args.handle] + [pair.split(",") for pair in args.pair]
    if not cases or any(not group or any(not h or any(c not in "0123456789abcdefABCDEF" for c in h) for h in group) for group in cases):
        parser.error("Provide hexadecimal --handle or --pair values")
    if any(len(pair.split(",")) != 2 for pair in args.pair):
        parser.error("--pair requires exactly two handles")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve(strict=True)
    before = digest(source)
    bundle = root / "NativeDiagnostic.dbx"
    shutil.copytree(args.bundle, bundle)
    request = root / "cases.txt"
    result = root / "cases.json"
    records = [str(source), str(result), str(len(cases))]
    for handles in cases:
        records.extend(["entity_" + "_".join(handles), str(len(handles)), *handles, "1", "0 0 0"])
    request.write_text("\n".join(records) + "\n")
    # The diagnostic opens the input itself via Autodesk's readDwgFile/dxfIn.
    # The console's document is another copy and is never saved.
    temporary = root / ("Drawing" + source.suffix)
    shutil.copy2(source, temporary)
    script = (
        f'(setvar "TRUSTEDPATHS" {lisp(str(root) + "/")})\n'
        f'(arxload {lisp(bundle)})\n{args.command}\n{request}\n_QUIT\n_Y\n'
    )
    receipt = {"source_sha256": before}
    try:
        receipt.update(run_core(args, root, temporary, script, "probe"))
        report = json.loads(result.read_text())
        receipt["case_count"] = len(report["cases"])
    except (OSError, ValueError, RuntimeError) as error:
        receipt["error"] = str(error)
    finally:
        receipt["source_unchanged"] = before == digest(source)
        (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False))
    return 1 if "error" in receipt or not receipt["source_unchanged"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
