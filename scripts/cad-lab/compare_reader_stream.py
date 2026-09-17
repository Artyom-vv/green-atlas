"""Compare unchanged byte import and SDK file import in isolated bounded workers.

Run against a DXF file or a directory. Every source is tested independently;
canonical normalized content (not just counts) must match, and bytes stay intact.
"""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.cad_import.cache import file_sha256
from app.cad_import.process import run_converter
from app.cad_intake.prepare_policy import PREPARE_POLICY
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.capacity import SourceGeometryCapacity


def worker(source: Path, output: Path, mode: str) -> None:
    original_hash = file_sha256(source)
    reader = EzdxfReader(capacity=SourceGeometryCapacity.process_bounded())
    started = time.monotonic()
    result = (
        reader.read_prepared_file(source)
        if mode == "file"
        else reader.read(source.name, source.read_bytes())
    )
    elapsed = time.monotonic() - started
    digest = sha256()
    for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).iterencode(result.model_dump(mode="json")):
        digest.update(chunk.encode("utf8"))
    assert file_sha256(source) == original_hash, "Source changed during comparison"
    output.write_text(json.dumps({"source_sha256": original_hash,
        "normalized_sha256": digest.hexdigest(), "read_seconds": elapsed}), encoding="utf8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--mode", choices=["file", "bytes"])
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    if args.mode:
        worker(source, args.output, args.mode)
        return
    args.output.mkdir(parents=True, exist_ok=True)
    sources = [source] if source.is_file() else sorted(source.rglob("*.dxf"))
    results = []
    for path in sources:
        key = sha256(str(path).encode()).hexdigest()[:16]
        record = {"source": str(path), "bytes": path.stat().st_size}
        for mode in ("bytes", "file"):
            output = args.output / f"{key}-{mode}.json"
            print(json.dumps({"source": path.name, "mode": mode}, ensure_ascii=False), flush=True)
            try:
                metrics = run_converter([sys.executable, str(Path(__file__).resolve()), str(path), str(output.resolve()), "--mode", mode],
                    output, output.with_suffix(".log"), PREPARE_POLICY,
                    environment={**os.environ, "PYTHONUTF8": "1"})
                record[mode] = {"process": asdict(metrics)}
                if output.exists():
                    record[mode].update(json.loads(output.read_text(encoding="utf8")))
            except Exception as error:
                record[mode] = {"error": str(error)}
        record["equal"] = bool(record["bytes"].get("normalized_sha256")) and record["bytes"].get("normalized_sha256") == record["file"].get("normalized_sha256")
        results.append(record)
        (args.output / "summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()
