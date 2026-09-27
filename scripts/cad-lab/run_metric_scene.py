"""One command: processed input manifest -> metric packet -> inspectable .blend."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--blender", type=Path, default=Path("/Applications/Blender.app/Contents/MacOS/Blender"))
    parser.add_argument("--packet-only", action="store_true")
    parser.add_argument("--preview", action="store_true", help="Also render a low-sample overview PNG")
    args = parser.parse_args()
    output = args.output.resolve()
    subprocess.run([sys.executable, str(ROOT / "scripts/cad-lab/assemble_metric_scene.py"),
                    "--manifest", str(args.manifest.resolve()), "--output", str(output)], check=True)
    if not args.packet_only:
        if not args.blender.is_file():
            raise SystemExit(f"Blender missing: {args.blender}")
        command = [str(args.blender), "--background", "--python",
                   str(ROOT / "scripts/cad-lab/open_metric_scene_blender.py"),
                   "--", str(output / "scene.json"), str(output / "scene.blend")]
        if args.preview:
            command.append(str(output / "overview.png"))
        subprocess.run(command, check=True)
    receipt = json.loads((output / "receipt.json").read_text())
    print(json.dumps({"packet": str(output / "scene.json"),
                      "blend": str(output / "scene.blend") if not args.packet_only else None,
                      "summary": receipt["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
