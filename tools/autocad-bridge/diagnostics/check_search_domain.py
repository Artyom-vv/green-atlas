"""Read-only ordinary pattern preview with native precomputed search domains."""

import argparse
import json
import time
from pathlib import Path

from app.composition import create_runtime
from app.native_query.live_client import LiveQueryClient
from app.planning.pattern_contracts import FillPatternRequest


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--zone", required=True)
    parser.add_argument("--kind", choices=("tree", "shrub"), default="shrub")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    calls = []
    original_measure = LiveQueryClient.measure

    def measured(client, session, query):
        started = time.monotonic()
        reply = original_measure(client, session, query)
        entry = {
            "wall_s": time.monotonic() - started,
            "native_ms": reply.elapsed_ms,
            "objects": len(query.targets),
            "points": len(query.points),
            "prepare_ms": sum(o.prepare_ms for o in reply.objects),
            "slow_preparation": sorted(
                [(o.prepare_ms, o.route) for o in reply.objects], reverse=True
            )[:3],
        }
        calls.append(entry)
        print(json.dumps(entry), flush=True)
        return reply

    if args.profile:
        LiveQueryClient.measure = measured
    runtime = create_runtime(args.database)
    try:
        before = runtime.project_repository.get(args.project, lightweight=True)
        request = FillPatternRequest(
            base_plan_version=before.plan.version,
            zone_ids=[args.zone],
            plant_kind=args.kind,
            composition="trees" if args.kind == "tree" else "shrubs",
            placement_mode="count",
            target_count=args.count,
            layout="natural",
            spacing_m=6 if args.kind == "tree" else 2.15,
            edge_offset_m=1 if args.kind == "tree" else 0.65,
            seed=47,
            species_revision_id="sorbus-aucuparia@2026-08-28.1"
            if args.kind == "tree"
            else "spiraea-japonica@2026-08-28.1",
        )
        started = time.monotonic()
        result = runtime.application.preview_pattern(args.project, request)
        elapsed = time.monotonic() - started
        after = runtime.project_repository.get(args.project, lightweight=True)
        assert before.plan == after.plan
        assert (
            before.state_version == after.state_version
            and before.geometry_version == after.geometry_version
        )
        record = {
            "request": request.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "elapsed_s": elapsed,
            "plan_unchanged": True,
            "state_version": before.state_version,
            "geometry_version": before.geometry_version,
            "native_calls": calls,
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "accepted": result.accepted_count,
                    "generated": result.generated_count,
                    "elapsed_s": elapsed,
                    "stop": result.search_stop_reason,
                    "domains": [
                        d.model_dump(exclude={"geometry", "unresolved_geometry"})
                        for d in result.search_domains
                    ],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        runtime.close()
        LiveQueryClient.measure = original_measure


if __name__ == "__main__":
    main()
