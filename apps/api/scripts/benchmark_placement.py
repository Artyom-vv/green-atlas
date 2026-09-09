"""Read a saved project without writing its DB; benchmark previews in memory.

Run from repository root with PYTHONPATH=apps/api. No exports, profiles or
database copies are written. Output can be recorded in the implementation log.
"""
import argparse
import cProfile
import json
import pstats
import sqlite3
import time
from pathlib import Path

from app.application import ProjectApplication
from app.contracts import FillPatternRequest, Project
from app.geometry.adapters import ShapelyGeometryEngine
from app.planning.patterns import ShapelyCandidateGenerator
from app.projects.adapters import InMemoryProjectRepository
from app.validation.adapters import RuleBasedPlanValidator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project_id")
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--distribution", choices=["equal", "available"], default="available")
    parser.add_argument("--runs", type=int, default=2)
    args = parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        payload = db.execute("select payload from projects where id=?", (args.project_id,)).fetchone()[0]
    project = Project.model_validate_json(payload)
    repository = InMemoryProjectRepository()
    repository.create(project)
    application = ProjectApplication(
        repository=repository, operation_repository=None, history=None, dxf_reader=None,
        geometry=ShapelyGeometryEngine(), geometry_query=None,
        validator=RuleBasedPlanValidator(), writer=None,
        candidate_generator=ShapelyCandidateGenerator(),
    )
    request = FillPatternRequest(
        base_plan_version=project.plan.version,
        zone_ids=[zone.id for zone in project.planting_zones],
        species_revision_id="tilia-cordata@2026-08-28.1", layout="natural",
        placement_mode="count", target_count=12, spacing_m=8,
        zone_distribution=args.distribution,
    )
    for run in range(args.runs):
        profile = cProfile.Profile()
        if args.profile:
            profile.enable()
        start = time.perf_counter()
        preview = application.preview_pattern(project.id, request)
        elapsed = time.perf_counter() - start
        profile.disable()
        print(json.dumps({
            "run": run, "seconds": elapsed, "profiled": args.profile,
            "features": len(project.geometry.feature_collection["features"]),
            "existing": len(project.plan.objects), "distribution": args.distribution,
            "requested": preview.requested_count, "accepted": preview.accepted_count,
            "zones": [zone.model_dump() for zone in preview.zone_allocations],
            "cached_previews": len(application._change_set_previews),
        }, ensure_ascii=False), flush=True)
        if args.profile:
            pstats.Stats(profile).sort_stats("cumulative").print_stats(15)


if __name__ == "__main__":
    main()
