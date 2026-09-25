"""Read-only real-capture hybrid search + AutoCAD placement preview.

No database/source writes, no Apply, no domain checkpoint writes. The preview
uses the same planning/evaluation services as the API, with an in-memory reader.
"""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import monotonic
from unittest.mock import Mock
from uuid import uuid4

from app.data_passport import build_data_passport
from app.native_query.live_client import LiveQueryClient, LiveSession
from app.native_query.live_provider import LiveNativeGeometryEngine
from app.planning.changes import ChangeSetApplication
from app.planning.evaluation import PlanEvaluation
from app.planning.pattern_application import PatternApplication
from app.planning.pattern_contracts import FillPatternRequest
from app.planning.patterns import ShapelyCandidateGenerator
from app.planning.rules import pattern_growth_radii
from app.projects.contracts import Project
from app.species.catalog import CATALOG
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project")
    parser.add_argument("zone")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--species", choices=[s.species_id for s in CATALOG if s.kind == "shrub"])
    parser.add_argument("--size-class", choices=["unspecified", "sapling", "standard", "large"], default="standard")
    parser.add_argument("--domain-only", action="store_true")
    parser.add_argument("--session-receipt", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    def read():
        with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            return db.execute("SELECT CAST(payload AS BLOB) FROM projects WHERE id=?", (args.project,)).fetchone()[0]

    raw = read()
    project = Project.model_validate(json.loads(raw))
    session = project.source_file.native_session
    if args.session_receipt:
        current = LiveSession.model_validate(json.loads(args.session_receipt.read_text())["session"])
        for name in ("source_sha256", "snapshot_sha256", "inventory_sha256", "units_code", "watched_databases", "unavailable_xrefs"):
            if getattr(session, name) != getattr(current, name):
                raise ValueError(f"Несовпадение захвата: {name}")
        session = current
        project.source_file.native_session = current
    engine = LiveNativeGeometryEngine(session, LiveQueryClient(pid=session.pid))
    engine._domain_checkpoints = None
    zone = next(z for z in project.planting_zones if z.id == args.zone)
    revision = next((s for s in CATALOG if s.species_id == args.species), None)
    request = FillPatternRequest(base_plan_version=project.plan.version, zone_ids=[zone.id],
                                 plant_kind="shrub", layout="natural", placement_mode="count",
                                 target_count=args.count, spacing_m=2.15, layout_radius_m=.65, edge_offset_m=.65,
                                 species_revision_id=revision.id if revision else None, size_class=args.size_class)
    growth = pattern_growth_radii(request) or (None, None)
    started = monotonic()
    while True:
        value = engine.automatic_safe_geometry(project, zone.geometry, .65, "shrub", *growth)
        summary = value["ga_search_domain"]
        print(json.dumps({"stage": "domain", "processed": summary["processed_objects"],
                          "total": summary["total_objects"], "elapsed_s": monotonic() - started}), flush=True)
        if summary["stop_reason"] == "resolution":
            break
    cold_s = monotonic() - started
    start = monotonic()
    engine.automatic_safe_geometry(project, zone.geometry, .65, "shrub", *growth)
    warm_s = monotonic() - start
    result = {"project_id": project.id, "state_version": project.state_version,
              "geometry_version": project.geometry_version, "zone": zone.id,
              "cold_s": cold_s, "warm_s": warm_s,
              "domain": {k: v for k, v in summary.items() if k not in {"geometry", "unresolved_geometry", "pending_geometry", "source_issues"}},
              "issue_samples": summary["source_issues"][:15],
              "request": request.model_dump()}
    if not args.domain_only:
        repository = Mock()
        repository.get.return_value = project
        evaluation = PlanEvaluation(engine, lambda: str(uuid4()))
        changes = ChangeSetApplication(
            repository=repository, evaluation=evaluation,
            validation=PlanValidation(RuleBasedPlanValidator(geometry=engine)),
            history_application=Mock(), history=Mock(), edit_lock=RLock(),
            invalidate_spacing=lambda _: None, now=lambda: datetime.now(timezone.utc),
            new_id=lambda: str(uuid4()),
        )
        patterns = PatternApplication(repository, ShapelyCandidateGenerator(), evaluation, changes)
        start = monotonic()
        while True:
            preview = patterns.preview_pattern(project.id, request)
            if all(d.stop_reason == "resolution" for d in preview.search_domains):
                break
        result["preview"] = {
            "elapsed_s": monotonic() - start, "requested": args.count,
            "generated": preview.generated_count, "accepted": preview.accepted_count,
            "effective_spacing_m": preview.effective_spacing_m,
            "can_apply": preview.change_set.can_apply if preview.change_set else False,
            "rejections": {r.code: r.count for r in preview.reason_summary},
            "points": [{"x": p.x, "y": p.y} for p in preview.change_set.additions] if preview.change_set else [],
        }
        if preview.change_set:
            result["preview"]["statuses"] = dict(Counter(r.code for r in preview.change_set.candidate_results))
    passport = build_data_passport(project, geometry=engine)
    result["passport"] = {"gaps": passport.gaps, "incomplete_layers": passport.incomplete_layers,
        "entries": [{"kind": e.kind, "status": e.status, "note": e.note,
                     "geometry_coverage": e.geometry_coverage.model_dump() if e.geometry_coverage else None}
                    for e in passport.entries]}
    result.update({"total_s": monotonic() - started, "saved_project": False,
                   "payload_unchanged": hashlib.sha256(read()).digest() == hashlib.sha256(raw).digest()})
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.output:
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
