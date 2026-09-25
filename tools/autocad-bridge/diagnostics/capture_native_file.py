"""Replay a frozen DWG/DXF in a fresh isolated AutoCAD with an explicit bundle."""

import argparse
import json
import shutil
from pathlib import Path

from run_dxf_roundtrip import (
    DEFAULT_CORE,
    digest,
    lisp,
    references_unchanged,
    run_core,
    stage_references,
    summarize,
)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--timeout", type=int, default=400)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--xref", type=Path, action="append", default=[],
                        help="Stage a known companion DWG by its stored basename; no name inference")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if source.suffix.lower() not in {".dwg", ".dxf"}:
        parser.error("Only native DWG/DXF inputs")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    before = digest(source)
    drawing = root / ("Drawing" + source.suffix.lower())
    shutil.copy2(source, drawing)
    references = stage_references(args.xref, root)
    bundle = root / "GreenAtlasBridge.dbx"
    shutil.copytree(args.bundle, bundle)
    receipt = {"source": str(source), "source_sha256": before,
               "references": references,
               "native_binary_sha256": digest(bundle / "Contents/MacOS/GreenAtlasBridge")}
    script = (
        f'(setvar "TRUSTEDPATHS" {lisp(str(root) + "/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload {lisp(bundle)})\n'
        + ("GAGEOMETRYSELFTEST\n" if args.self_test else "")
        + "GAEXPORTREGIONPROBE\n_QUIT\n_Y\n"
    )
    try:
        receipt["engine"] = run_core(args, root, drawing, script, "capture")
        receipt["capture"] = summarize(Path(str(drawing) + ".green-atlas.geometry.json"))
    except (OSError, ValueError, RuntimeError) as error:
        receipt["error"] = str(error)
    finally:
        receipt["source_unchanged"] = before == digest(source) == digest(drawing)
        receipt["references_unchanged"] = references_unchanged(references)
        (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "capture"}, ensure_ascii=False))
    return 1 if "error" in receipt or not receipt["source_unchanged"] or not receipt["references_unchanged"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
