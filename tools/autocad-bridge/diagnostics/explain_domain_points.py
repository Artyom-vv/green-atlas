"""Small read-only sample, not a replay of the complete search domain."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from app.composition import create_runtime
from app.planning.rules import growth_radii


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--spread", action="store_true", help="Sample twelve widely separated unresolved cells")
    args = parser.parse_args()
    envelope = json.loads(args.checkpoint.read_text())
    assert hashlib.sha256(envelope["payload"].encode()).hexdigest() == envelope["sha256"]
    checkpoint = json.loads(envelope["payload"])
    runtime = create_runtime(args.database)
    try:
        project = runtime.application.get(args.project)
        before = project.model_dump(mode="json")
        engine = runtime.application.geometry.native
        points = []
        for state in ("excluded", "unresolved"):
            cells = sorted(checkpoint[state], key=lambda c: -c["size"])
            # Three spatially separated examples, not three adjacent siblings
            chosen = []
            if args.spread and state == "unresolved":
                pool = list(cells)
                spread = [pool.pop(0)]
                while pool and len(spread) < 12:
                    cell = max(pool, key=lambda c: min((c['x']-s['x'])**2+(c['y']-s['y'])**2 for s in spread))
                    pool.remove(cell)
                    spread.append(cell)
                cells = spread
            for cell in cells:
                x, y = cell["x"] + cell["size"] / 2, cell["y"] + cell["size"] / 2
                if all((x - a) ** 2 + (y - b) ** 2 > 30**2 for a, b in chosen):
                    chosen.append((x, y))
                    points.append({"origin": state, "x": x, "y": y, "cell_size": cell["size"]})
                if len(chosen) == (12 if args.spread and state == "unresolved" else 3):
                    break
        for plant in (project.plan.objects[:2] if project.plan else []):
            points.append({"origin": "saved_plant", "x": plant.x, "y": plant.y})
        reports = []
        canopy, roots = growth_radii(["sorbus-aucuparia@2026-08-28.1"], "standard")
        for point in points:
            start = time.monotonic()
            result = engine.explain_position(project, point["x"], point["y"], 1.6, "tree", canopy, roots)
            report = {**point, "seconds": time.monotonic() - start, **result.model_dump(mode="json")}
            reports.append(report)
            print(json.dumps({**point, "seconds": report["seconds"], "state": result.state,
                              "causes": len(result.causes), "codes": sorted({c.code for c in result.causes})}, ensure_ascii=False), flush=True)
        assert before == runtime.application.get(args.project).model_dump(mode="json")
        args.receipt.write_text(json.dumps({"project": args.project, "project_unchanged": True,
                                          "source_sha256": engine.session.source_sha256,
                                          "points": reports}, ensure_ascii=False, indent=2))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
