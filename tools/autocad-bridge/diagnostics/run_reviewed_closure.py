"""Same-object native A/B: original open path, reviewed closure, original again.

Selected-instance test only. Never writes the source or marks a project approved.
Uses the production supervisor and an independent package copy for every query.
"""
from __future__ import annotations

import argparse
import json
from hashlib import file_digest
from pathlib import Path
from uuid import uuid4

from app.native_query.contracts import NativeObjectQuery
from app.native_query.process import run_native_query
from app.native_query.process_contracts import (
    CadPackageFile,
    NativeInputPackage,
    NativeQueryProcessConfig,
)


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--worker", required=True, type=Path)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source, worker, output = args.source.resolve(), args.worker.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    case = next(item for item in json.loads(args.cases.read_text())["cases"] if item["name"] == "78B3")
    package = NativeInputPackage(source.parent, source.name, (
        CadPackageFile(source.name, digest(source), source.stat().st_size),
    ))
    acad = Path("/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app/Contents")
    config = NativeQueryProcessConfig(
        core_executable=acad / "Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole",
        worker_bundle=worker, job_root=output,
        worker_binary_sha256=digest(worker / "Contents/MacOS/GreenAtlasBridge"),
        plugin_version="0.1.38",
        bootstrap_template=acad / "Resources/UserDataCache/en-us/Template/acadiso.dwt",
        architecture="x86_64", timeout_seconds=90,
    )
    report = {"source": str(source), "source_sha256": package.files[0].sha256,
              "scope": "selected native path only; not planting approval", "runs": []}
    for mode in ("area", "closed_area", "area"):
        query = NativeObjectQuery(
            request_id=uuid4().hex, source_sha256=package.files[0].sha256, units_code=6,
            targets=[{"route": "78B3", "capability": mode}],
            points=[item["xyz"] for item in case["points"]],
        )
        result = run_native_query(package, query, config)
        row = {"mode": mode, "elapsed_seconds": result.receipt.elapsed_seconds,
               "job_directory": str(result.receipt.job_directory),
               "exit_code": result.receipt.exit_code,
               "lifecycle_verified": result.receipt.lifecycle_verified,
               "source_unchanged": result.receipt.source_verified_after,
               "failure": result.failure.message if result.failure else None,
               "reply": result.reply.model_dump(mode="json", by_alias=True) if result.reply else None}
        report["runs"].append(row)
        (output / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({key: value for key, value in row.items() if key != "reply"}), flush=True)
        if result.failure:
            return 1
    objects = [run["reply"]["objects"][0] for run in report["runs"]]
    passed = (objects[0]["capability"] == objects[2]["capability"] == "curve"
              and objects[0]["answers"] == objects[2]["answers"]
              and objects[1]["interior_known"]
              and [answer["membership"] for answer in objects[1]["answers"][:2]] == ["occupied", "outside"])
    report["passed"] = passed
    (output / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"passed": passed}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
