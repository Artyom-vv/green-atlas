"""Exercise the product process supervisor on a declared immutable dataset package.

This is not a GAOPEN capture receipt or a full-XREF-inventory assertion.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from app.native_query.contracts import NativeObjectQuery
from app.native_query.process import run_native_query
from app.native_query.process_contracts import (
    CadPackageFile, NativeInputPackage, NativeQueryProcessConfig,
)
from run_direct_queries import CORE, digest


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    for name in ("baseline-receipt", "case", "worker", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error("Output must be empty")
    baseline = json.loads(args.baseline_receipt.read_text())
    package_root = Path(baseline["package"]).resolve(strict=True)
    entry = Path(baseline["source"]).resolve(strict=True).relative_to(package_root).as_posix()
    files = tuple(CadPackageFile(
        path=Path(row["source"]).resolve(strict=True).relative_to(package_root).as_posix(),
        sha256=row["sha256"], size_bytes=Path(row["source"]).stat().st_size,
    ) for row in baseline["files"])
    package = NativeInputPackage(package_root, entry, files)
    query = NativeObjectQuery(
        request_id=uuid4().hex, source_sha256=next(f.sha256 for f in files if f.path == entry),
        **json.loads(args.case.read_text()),
    )
    worker = args.worker.resolve(strict=True)
    config = NativeQueryProcessConfig(
        core_executable=CORE, worker_bundle=worker, job_root=root,
        worker_binary_sha256=digest(worker / "Contents/MacOS/GreenAtlasBridge"),
        plugin_version="0.1.38", architecture="x86_64",
        bootstrap_template=CORE.parents[4] / "Resources/UserDataCache/en-us/Template/acadiso.dwt",
    )
    result = run_native_query(package, query, config)
    receipt = asdict(result.receipt)
    for key in ("log", "diagnostic_output", "diagnostic_reply"):
        receipt.pop(key)
    receipt["failure"] = asdict(result.failure) if result.failure else None
    receipt["reply_accepted"] = result.reply is not None
    receipt["diagnostic_reply_valid"] = result.receipt.diagnostic_reply is not None
    if result.receipt.diagnostic_reply is not None:
        receipt["measurement_reply"] = result.receipt.diagnostic_reply.model_dump(mode="json", by_alias=True)
    (root / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, default=str, indent=2)+"\n")
    print(json.dumps({k: receipt[k] for k in (
        "job_directory", "exit_code", "elapsed_seconds", "failure", "reply_accepted",
        "diagnostic_reply_valid", "source_verified_after", "staged_verified_after",
    )}, default=str, ensure_ascii=False))
    return int(result.failure is not None)


if __name__ == "__main__":
    raise SystemExit(main())
