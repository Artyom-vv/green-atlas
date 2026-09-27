"""Read-only native evidence for one rejected point, including site membership."""

import argparse
import json
from pathlib import Path

from app.composition import create_runtime


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    runtime = create_runtime(args.database)
    try:
        project = runtime.project_repository.get(args.project, lightweight=True)
        engine = runtime.application.geometry.native
        engine.prepare_positions(project, [(args.x, args.y)])
        rows = engine._rows(project, args.x, args.y, 1.6)
        record = {
            "point": [args.x, args.y],
            "basis": {
                key: getattr(project, key)
                for key in ("id", "state_version", "geometry_version")
            },
            "rows": [
                {
                    "object": row.item.model_dump(mode="json"),
                    "role": engine._layers[row.item.layer].mapped_kind,
                    "mapping_confirmed": engine._layers[
                        row.item.layer
                    ].mapping_confirmed,
                    "answer": row.answer.model_dump(mode="json")
                    if row.answer
                    else None,
                    "preparation_error": row.measurement.preparation_error
                    if row.measurement
                    else None,
                    "interior_known": row.measurement.interior_known
                    if row.measurement
                    else None,
                }
                for row in rows
            ],
        }
        args.receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "rows": len(rows),
                    "sites": [r for r in record["rows"] if r["role"] == "site_border"],
                },
                ensure_ascii=False,
            )
        )
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
