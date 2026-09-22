"""Reproduce the source-backed render pipeline from source receipts to images."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / ".runtime/deterministic-render-pipeline-20260920"
DEFAULT_BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--blender", type=Path,
                        default=Path(os.environ.get("BLENDER_BIN", str(DEFAULT_BLENDER))))
    parser.add_argument("--quick", action="store_true", help="Render at 25% and 8 Cycles samples")
    parser.add_argument("--compile-only", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        sys.executable,
        str(ROOT / "scripts/cad-lab/build_class_constrained_terrain.py"),
    ], cwd=ROOT, check=True)
    subprocess.run([
        sys.executable,
        str(ROOT / "scripts/cad-lab/audit_street_objects.py"),
    ], cwd=ROOT, check=True)
    subprocess.run([
        sys.executable,
        str(ROOT / "scripts/cad-lab/compile_render_scene_packet.py"),
        "--output", str(args.output),
    ], cwd=ROOT, check=True)
    packet = args.output / "scene-packet.json"
    if args.compile_only:
        return
    if not args.blender.is_file():
        raise SystemExit(f"Blender not found: {args.blender}")
    render_output = args.output / ("quick" if args.quick else "render")
    environment = os.environ.copy()
    if args.quick:
        environment.update({"GA_RENDER_PERCENT": "25", "GA_RENDER_SAMPLES": "8"})
    subprocess.run([
        str(args.blender), "--background",
        "--python", str(ROOT / "scripts/cad-lab/render_scene_packet_blender.py"),
        "--", str(packet), str(render_output),
    ], cwd=ROOT, env=environment, check=True)
    receipt = json.loads((render_output / "render-receipt.json").read_text())
    if receipt["scene_packet_sha256"] != sha256(packet):
        raise SystemExit("Render receipt does not match the compiled scene packet")
    subprocess.run([
        sys.executable,
        str(ROOT / "scripts/cad-lab/prepare_neural_finish.py"),
        "--render-dir", str(render_output),
    ], cwd=ROOT, check=True)
    neural_contract_path = render_output / "neural-finish/contract.json"
    neural_contract = json.loads(neural_contract_path.read_text())
    if neural_contract["scene_packet_sha256"] != sha256(packet):
        raise SystemExit("Neural-finish contract does not match the compiled scene packet")
    print(json.dumps({
        "status": "ok",
        "scene_packet": str(packet),
        "scene_packet_sha256": sha256(packet),
        "render_receipt": str(render_output / "render-receipt.json"),
        "neural_finish_contract": str(neural_contract_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
