"""Read-only search replay; defaults to the user's 15-rowan request."""

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--audit-database", type=Path)
    parser.add_argument("--zone", default="manual-zone-1790240522285-1")
    parser.add_argument("--kind", choices=("tree", "shrub"), default="tree")
    parser.add_argument("--count", type=int, default=15)
    args = parser.parse_args()
    with httpx.Client(timeout=240) as client:
        before = client.get(args.url).raise_for_status().json()
        request = {
            "type": "fill",
            "layout": "natural",
            "plant_kind": args.kind,
            "composition": "trees" if args.kind == "tree" else "shrubs",
            "base_plan_version": before["plan"]["version"],
            "zone_ids": [args.zone],
            "placement_mode": "count",
            "target_count": args.count,
            "zone_distribution": "equal",
            "spacing_m": 6 if args.kind == "tree" else 2.15,
            "edge_offset_m": 1 if args.kind == "tree" else 0.65,
            "angle_deg": 0,
            "seed": 47,
            "size_class": "standard",
            "spacing_policy": "balanced",
            "species_revision_id": (
                "sorbus-aucuparia@2026-08-28.1"
                if args.kind == "tree"
                else "spiraea-japonica@2026-08-28.1"
            ),
        }
        start = time.monotonic()
        if args.audit_database:
            # An explicit diagnostic run may finish the entire bounded sample.
            # This is not the interactive service deadline or a plan mutation.
            from functools import partial
            from unittest.mock import patch

            from app.composition import create_runtime
            from app.planning.candidate_search import search_candidates
            from app.planning.pattern_contracts import FillPatternRequest

            runtime = create_runtime(args.audit_database)
            try:
                with patch(
                    "app.planning.pattern_application.search_candidates",
                    partial(search_candidates, time_limit_s=600),
                ):
                    result = runtime.application.preview_pattern(
                        before["id"], FillPatternRequest.model_validate(request)
                    ).model_dump(mode="json")
            finally:
                runtime.close()
        else:
            response = client.post(args.url + "/plan/patterns/preview", json=request)
            response.raise_for_status()
            result = response.json()
        elapsed = time.monotonic() - start
        after = client.get(args.url).raise_for_status().json()
        assert before["plan"] == after["plan"], "Read-only preview changed the plan"
        unchanged_fields = ("plan", "planting_zones", "layers")
        assert all(before[key] == after[key] for key in unchanged_fields)
        record = {
            "request": request,
            "result": result,
            "elapsed_s": elapsed,
            "diagnostic_extended_deadline": bool(args.audit_database),
            "basis": {
                k: before[k] for k in ("id", "state_version", "geometry_version")
            },
            "plan_unchanged": True,
            "unchanged_sha256": {
                key: hashlib.sha256(json.dumps(before[key], sort_keys=True).encode()).hexdigest()
                for key in unchanged_fields
            },
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "elapsed_s": elapsed,
                    **{
                        k: result.get(k)
                        for k in (
                            "accepted_count",
                            "generated_count",
                            "search_stop_reason",
                        )
                    },
                    "reasons": Counter(p["code"] for p in result["skipped"]),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
