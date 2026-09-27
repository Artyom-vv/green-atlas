"""Run production DXF package inspection without a DWG converter or project writes.

The profile is a JSON list of CadIntakeRequest objects with explicit relative
paths and hashes. Run under run_bounded.py for aggregate process-tree metrics.
"""

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps/api"))

from pydantic import TypeAdapter

from app.cad_import.cache import file_sha256
from app.cad_intake.adapter import ProcessPackageInspection
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadIntakeRequest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("profile", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    output = args.output.resolve()
    if output.exists() or output.is_relative_to(root):
        raise ValueError("Choose a fresh output directory outside the source")
    requests = TypeAdapter(list[CadIntakeRequest]).validate_json(args.profile.read_bytes())
    originals = {path: file_sha256(path) for path in root.rglob("*.dxf")}
    output.mkdir(parents=True)
    inspector = ProcessPackageInspection(CadIntakeConfig(
        tuple(AllowedCadRoot(key, key, root) for key in sorted({r.root_id for r in requests})),
        output / "storage", None,
    ))
    results = []
    for run in ("cold", "cached"):
        for index, request in enumerate(requests):
            started = time.monotonic()
            passport = inspector.inspect(f"{run}-{index}", request, lambda: None, lambda _: None)
            elapsed = time.monotonic() - started
            receipt = output / f"{run}-{index}-passport.json"
            receipt.write_text(passport.model_dump_json(indent=2), encoding="utf8")
            row = {
                "run": run, "entry": request.entry, "seconds": elapsed,
                "status": passport.status,
                "drawings": len(passport.drawings),
                "readable": sum(d.status == "readable" for d in passport.drawings),
                "unresolved_references": sum(r.status != "resolved" for r in passport.references),
                "receipt": receipt.name,
            }
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    if {path: file_sha256(path) for path in root.rglob("*.dxf")} != originals:
        raise ValueError("Source files changed during inspection")
    (output / "summary.json").write_text(json.dumps({
        "scope": "Production package inspection only; no editable import or geometry completeness claim",
        "converter": None, "source_files": len(originals), "source_hashes_unchanged": True,
        "results": results,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
