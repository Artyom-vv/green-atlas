"""Resume content-addressed offline conversion/inspection, preserving street paths.

Only reads the original dataset. No project writes or CAD fidelity claims.
Each native conversion and SDK inspection is isolated and resource-bounded.
"""

from collections import defaultdict, deque
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.cad_import.cache import file_sha256
from app.cad_import.policy import ConversionPolicy
from app.cad_import.process import run_converter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--memory-mib", type=int, default=2048)
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    output = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf8"))
    source_root = Path(manifest["root"])
    rust = ROOT / ".runtime/cad-patch-lab/acds-followup/acadrust-baseline-probe.exe"
    libre = ROOT / ".runtime/cad-patch-lab/indexed-acds-bin/dwg2dxf.exe"
    policy = ConversionPolicy(timeout_seconds=args.seconds, max_memory_bytes=args.memory_mib * 1024 * 1024,
                              max_log_bytes=16 * 1024 * 1024)
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT / "apps/api")}
    queues = defaultdict(deque)
    files = sorted(manifest["files"], key=lambda r: (
        r["role_hint"] != "source",
        not any(word in Path(r["path"]).stem.casefold() for word in ("генплан", "генераль", "_гп", "геопод", "топограф")),
        r["bytes"],
    ))
    for row in files:
        queues[row["street"]].append(row)
    order = []
    seen = set()
    while any(queues.values()):
        for street in manifest["streets"]:
            queue = queues[street["street"]]
            if not queue:
                continue
            row = queue.popleft()
            if row["sha256"] not in seen:
                order.append(row)
                seen.add(row["sha256"])
    (output / "tools.json").write_text(json.dumps({
        "acadrust": {"path": str(rust), "sha256": file_sha256(rust)},
        "libredwg": {"path": str(libre), "sha256": file_sha256(libre)},
        "policy": policy.__dict__, "scope": "offline diagnostic copies; no project publication",
    }, indent=2), encoding="utf8")

    for index, row in enumerate(order, 1):
        directory = output / "drawings" / row["sha256"]
        receipt = directory / "result.json"
        if receipt.exists():
            continue
        directory.mkdir(parents=True, exist_ok=True)
        source = Path(row["source_path"]) if row.get("source_path") else source_root / row["path"]
        started = time.monotonic()
        result = {"source": row, "attempts": [], "status": "not_read", "fidelity_verified": False}
        if file_sha256(source) != row["sha256"]:
            raise ValueError(f"Source changed: {row['path']}")
        dxf = source if source.suffix.lower() == ".dxf" else None
        if dxf is None:
            for name, exe in (("acadrust", rust), ("libredwg", libre)):
                target = directory / f"{name}.dxf"
                if target.exists():
                    result["attempts"].append({"tool": name, "error": "Unfinished previous output; not reused"})
                    continue
                command = [str(exe), str(source), str(target)] if name == "acadrust" else [str(exe), "-v1", "-o", str(target), str(source)]
                try:
                    run = run_converter(command, target, directory / f"{name}.log", policy, environment=environment)
                    result["attempts"].append({"tool": name, **run.__dict__})
                    if target.is_file() and target.stat().st_size and run.exit_code == 0:
                        dxf = target
                        break
                except Exception as error:
                    result["attempts"].append({"tool": name, "error": str(error)})
        if dxf is not None:
            result["dxf"] = str(dxf)
            result["dxf_bytes"] = dxf.stat().st_size
            result["dxf_sha256"] = file_sha256(dxf)
            inventory = directory / "inventory.json"
            try:
                run = run_converter(
                    [sys.executable, str(Path(__file__).with_name("audit_street_drawing.py")), str(dxf), str(inventory)],
                    inventory, directory / "inspection.log", policy, environment=environment,
                )
                result["inspection_process"] = run.__dict__
                if run.exit_code == 0 and inventory.is_file():
                    result["status"] = "readable"
                else:
                    result["error"] = "Converted/native DXF not read; inspect process log"
            except Exception as error:
                result["error"] = str(error)
        result["source_unchanged"] = file_sha256(source) == row["sha256"]
        result["seconds"] = time.monotonic() - started
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        print(json.dumps({"done": index, "total_unique": len(order), "street": row["street"],
                          "file": row["path"], "status": result["status"], "seconds": round(result["seconds"], 2)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
