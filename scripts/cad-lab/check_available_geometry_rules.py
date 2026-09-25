"""Read-only saved-project check against its current AutoCAD session.

Samples work-zone points, not CAD display coordinates. Does not save mappings,
plantings, geometry, or domain checkpoints and does not certify a whole zone.
"""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from time import monotonic

from app.native_query.calculation_rules import calculation_rule
from app.native_query.live_client import LiveQueryClient
from app.native_query.live_provider import LiveNativeGeometryEngine
from app.projects.contracts import Project
from app.regulations.placement_config import PLACEMENT_RULES_REVISION
from shapely.geometry import Point, shape


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project")
    parser.add_argument("--zone", required=True)
    parser.add_argument("--kind", choices=["tree", "shrub"], default="shrub")
    parser.add_argument("--radius", type=float, default=.65)
    parser.add_argument("--spatial-windows", action="store_true", help="Benchmark bounded transport windows")
    args = parser.parse_args()
    if args.spatial_windows:
        import app.native_query.live_provider as provider
        provider.SINGLE_WINDOW_POINT_LIMIT = 0
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        payload = connection.execute("SELECT payload FROM projects WHERE id=?", (args.project,)).fetchone()
    if not payload:
        raise ValueError("Проект не найден")
    project = Project.model_validate_json(payload[0])
    session = project.source_file.native_session
    if session is None:
        raise ValueError("У проекта нет сеанса AutoCAD")
    zone = next(zone for zone in project.planting_zones if zone.id == args.zone)
    work = shape(zone.geometry)
    x0, y0, x1, y1 = work.bounds
    # Bounded smoke check, deliberately not a placement sampler or area estimate.
    candidates = [(x0 + (x1-x0)*i/5, y0 + (y1-y0)*j/5) for i in range(1,5) for j in range(1,5)]
    points = [p for p in candidates if work.covers(Point(p))]
    engine = LiveNativeGeometryEngine(session, LiveQueryClient(pid=session.pid))
    start = monotonic()
    engine.prepare_positions(project, points)
    prepare_s = monotonic() - start
    results = []
    for x,y in points:
        violation = engine.position_violation(project, x, y, args.radius, args.kind)
        advisory = engine.placement_advisory_detail(project, x, y, args.radius, args.kind)
        results.append({"x": x, "y": y, "violation": violation.code if violation else None,
                        "layer": violation.source_layer if violation else None,
                        "advisory": advisory.code, "advisory_layer": advisory.source_layer})
    engine.assert_current(project)
    utilities = [calculation_rule(layer, args.kind, args.radius) for layer in project.layers if layer.mapped_kind == "utility"]
    print(json.dumps({"project_id": project.id, "state_version": project.state_version,
                     "geometry_version": project.geometry_version, "plan_version": project.plan.version,
                     "plantings": len(project.plan.objects), "session_id": session.session_id,
                     "rules_revision": PLACEMENT_RULES_REVISION, "zone": zone.label,
                     "kind": args.kind, "radius": args.radius, "prepare_s": prepare_s,
                     "elapsed_s": monotonic()-start, "utility_rules": len([r for r in utilities if r]),
                     "counts": dict(Counter(r["violation"] or r["advisory"] for r in results)),
                     "points": results, "saved_project": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
