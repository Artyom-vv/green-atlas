"""Render another camera from a frozen native Blender scene without new objects."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-blend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--camera", type=float, nargs=3, required=True)
    parser.add_argument("--target", type=float, nargs=3, required=True)
    parser.add_argument("--lens", type=float, required=True)
    parser.add_argument("--samples", type=int, default=64)
    parser.add_argument("--resolution-x", type=int, default=1280)
    parser.add_argument("--resolution-y", type=int, default=720)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    source = args.source_blend.resolve()
    if not source.is_file():
        parser.error(f"Scene missing: {source}")
    if Path(bpy.data.filepath).resolve() != source:
        bpy.ops.wm.open_mainfile(filepath=str(source))
    scene = bpy.context.scene
    camera = scene.camera
    if camera is None:
        raise RuntimeError("Frozen scene has no camera")
    camera.location = tuple(args.camera)
    camera.rotation_euler = (Vector(args.target) - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.lens = args.lens
    scene.cycles.samples = args.samples
    scene.cycles.seed = 271828
    scene.render.resolution_x = args.resolution_x
    scene.render.resolution_y = args.resolution_y
    scene.render.resolution_percentage = 100
    args.output.parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = str(args.output.resolve())
    bpy.ops.render.render(write_still=True)
    receipt = {
        "schema": "green-atlas.native-view-receipt.v1",
        "source_blend": str(source), "source_blend_sha256": sha256(source),
        "output": str(args.output.resolve()), "output_sha256": sha256(args.output),
        "camera": list(args.camera), "target": list(args.target), "lens_mm": args.lens,
        "resolution": [args.resolution_x, args.resolution_y], "samples": args.samples,
        "seed": scene.cycles.seed, "engine": scene.render.engine,
        "new_scene_objects": 0,
    }
    args.output.with_suffix(".receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
