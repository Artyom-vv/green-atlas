"""Repeat the historical controlled Blender render from processed demo inputs.

This is a Kustanayskaya recovery harness. The product path remains the AutoCAD
plugin; this tool does not establish a general CAD ingestion contract.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = ROOT / "artifacts/metric-scene-20260923/rebuild"
DEFAULT_DXF = next((ROOT / ".runtime/kustanayskaya-mac-ready-20260917/dxf").rglob("03_10004141_Проектные решения.dxf"), None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_BASE / "output/scene.json")
    parser.add_argument("--surfaces", type=Path, default=DEFAULT_BASE / "inputs/surfaces-audit/authored-surfaces.geojson")
    parser.add_argument("--alignment", type=Path, default=DEFAULT_BASE / "inputs/candidate-osm-alignment.json")
    parser.add_argument("--map-roads", type=Path, default=DEFAULT_BASE / "inputs/segment.geojson")
    parser.add_argument("--map-buildings", type=Path, default=DEFAULT_BASE / "inputs/building.geojson")
    parser.add_argument("--project-dxf", type=Path, default=DEFAULT_DXF)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/metric-scene-20260923/native-rebuild")
    parser.add_argument("--blender", type=Path, default=Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--resolution-x", type=int, default=1280)
    parser.add_argument("--resolution-y", type=int, default=720)
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--skip-aerial", action="store_true")
    args = parser.parse_args()
    for path in (args.packet, args.surfaces, args.alignment, args.map_roads, args.map_buildings, args.project_dxf):
        if path is None or not path.is_file():
            parser.error(f"Required processed demo input is missing: {path}")
    args.output.mkdir(parents=True, exist_ok=True)
    inputs = args.output / "controlled-inputs"
    scene = args.output / "controlled-scene"
    render = args.output / "final-native"
    subprocess.run([
        sys.executable, str(ROOT / "scripts/cad-lab/restore_controlled_render_inputs.py"),
        "--packet", str(args.packet), "--alignment", str(args.alignment),
        "--map-roads", str(args.map_roads), "--map-buildings", str(args.map_buildings),
        "--output", str(inputs),
    ], cwd=ROOT, check=True)
    subprocess.run([
        sys.executable, str(ROOT / "scripts/cad-lab/compile_kustanayskaya_controlled_scene.py"),
        "--world", str(inputs / "world.json"), "--surfaces", str(args.surfaces),
        "--placement-audit", str(inputs / "placement-audit.json"),
        "--alignment", str(args.alignment), "--project-dxf", str(args.project_dxf),
        "--material-packet", str(inputs / "material-packet.json"), "--output", str(scene),
    ], cwd=ROOT, check=True)
    packet = json.loads((scene / "scene-packet.json").read_text())
    if args.compile_only:
        print(json.dumps({"scene_packet": str(scene / "scene-packet.json"), "audit": packet["audit"]}, ensure_ascii=False))
        return
    if not args.blender.is_file():
        parser.error(f"Blender missing: {args.blender}")
    subprocess.run([
        str(args.blender), "--background", "--python", str(ROOT / "scripts/cad-lab/render_nspd_scene_blender.py"), "--",
        "--packet", str(scene / "scene-packet.json"), "--output", str(render),
        "--engine", "cycles", "--samples", str(args.samples),
        "--adaptive-threshold", "0.012",
        "--resolution-x", str(args.resolution_x), "--resolution-y", str(args.resolution_y),
        "--appearance-profile", "native_v1", "--botaniq-lawn", "--lawn-microgeometry",
        "--lighting-profile", "stable_daylight",
        "--scene-state", "project", "--skip-diagnostics",
    ], cwd=ROOT, check=True)
    receipt = json.loads((render / "render-receipt.json").read_text())
    if not args.skip_aerial:
        subprocess.run([
            str(args.blender), "--background", str(render / "street-geometry-prototype.blend"),
            "--python", str(ROOT / "scripts/cad-lab/render_native_view_blender.py"), "--",
            "--source-blend", str(render / "street-geometry-prototype.blend"),
            "--output", str(render / "aerial-native.png"),
            "--camera", "70", "-70", "105", "--target", "-3", "20", "0",
            "--lens", "42", "--samples", str(args.samples),
            "--resolution-x", str(args.resolution_x), "--resolution-y", str(args.resolution_y),
        ], cwd=ROOT, check=True)
    print(json.dumps({
        "image": str(render / "street-geometry-prototype.png"),
        "aerial_image": None if args.skip_aerial else str(render / "aerial-native.png"),
        "blend": str(render / "street-geometry-prototype.blend"),
        "vegetation": packet["audit"]["vegetation_rendered"],
        "curbs": packet["audit"]["curb_segments"],
        "renderer": receipt.get("engine") or receipt.get("render", {}).get("engine"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
