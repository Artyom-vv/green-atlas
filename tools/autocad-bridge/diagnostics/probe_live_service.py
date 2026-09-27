"""Read-only spatial audit of the SAME live adapter used by the demo service.

The grid only discovers candidates; a clear answer is not ground-truth proof.
It never changes layers, CAD geometry, working zones or plantings.
"""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from app.composition import create_runtime
from app.native_query.live_runtime import LiveBinding, binding_path


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument(
        "--grid", nargs=5, type=float, metavar=("X0", "Y0", "X1", "Y1", "STEP")
    )
    args = parser.parse_args()
    binding = LiveBinding.model_validate_json(binding_path(args.database).read_bytes())
    runtime = create_runtime(args.database)
    started = time.monotonic()
    try:
        project = runtime.application.get(binding.project_id)
        engine = runtime.application.manual.evaluation.geometry
        controls = {
            "known_building": (15994.606306, -5245.249309),
            "known_road": (16092.001603, -5397.977498),
            "small_building": (15935.0, -4960.0),
        }
        points = list(controls.values())
        if args.grid:
            x0, y0, x1, y1, step = args.grid
            if step <= 0 or x1 <= x0 or y1 <= y0:
                parser.error("Invalid grid")
            points += [
                (x0 + i * step, y0 + j * step)
                for i in range(int((x1 - x0) / step) + 1)
                for j in range(int((y1 - y0) / step) + 1)
            ]
        if len(points) > 2000:
            parser.error("Use a local grid of at most 2000 points")
        engine.prepare_positions(project, points)
        results = []
        for x, y in points:
            obstacle = engine.position_violation(project, x, y, 0.5, "shrub")
            advisory = engine.placement_advisory_detail(project, x, y, 0.5)
            details = []
            if (x, y) in controls.values():
                native = engine.native
                for row in native._rows(project, x, y, 0.5):
                    layer = native._layers.get(row.item.layer)
                    if layer and layer.mapped_kind in {
                        "building",
                        "site_border",
                        "road",
                    }:
                        details.append(
                            {
                                "item": row.item.model_dump(),
                                "role": layer.mapped_kind,
                                "preparation": row.measurement.preparation_error
                                if row.measurement
                                else None,
                                "interior_known": row.measurement.interior_known
                                if row.measurement
                                else None,
                                "answer": row.answer.model_dump() if row.answer else None,
                            }
                        )
            results.append(
                {
                    "x": x,
                    "y": y,
                    "details": details,
                    "control": next(
                        (name for name, point in controls.items() if point == (x, y)),
                        None,
                    ),
                    "rejection": obstacle.__dict__ if obstacle else None,
                    "advisory": advisory.__dict__,
                }
            )
        receipt = {
            "project_id": project.id,
            "snapshot_sha256": binding.session.snapshot_sha256,
            "elapsed_s": time.monotonic() - started,
            "results": results,
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        counts = Counter(
            r["rejection"]["code"] if r["rejection"] else r["advisory"]["code"]
            for r in results
        )
        print(
            json.dumps(
                {
                    "elapsed_s": receipt["elapsed_s"],
                    "counts": counts,
                    "controls": results[:3],
                    "clear_candidates": [
                        r
                        for r in results
                        if not r["rejection"]
                        and r["advisory"]["code"] == "SOURCE_GEOMETRY_PARTIAL"
                    ][:20],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
