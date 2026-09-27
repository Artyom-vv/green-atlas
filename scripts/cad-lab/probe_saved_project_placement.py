"""Replay a saved project's automatic placement without modifying the project.

Run with ``PYTHONPATH=apps/api apps/api/.venv/bin/python``. The SQLite connection
is read-only; the real generation, preview and validation services run in memory.
No preview is applied or published to the live API process.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from unittest.mock import Mock
from uuid import uuid4

from app.geometry.adapters import ShapelyGeometryEngine
from app.planning.changes import ChangeSetApplication
from app.planning.evaluation import PlanEvaluation
from app.planning.pattern_application import PatternApplication
from app.planning.pattern_contracts import FillPatternRequest
from app.planning.patterns import ShapelyCandidateGenerator
from app.projects.contracts import Project
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation


def probe(args: argparse.Namespace) -> dict:
    uri = f"file:{args.db.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            "SELECT payload FROM projects WHERE id = ?", (args.project_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"Project {args.project_id} not found in {args.db}")
    project = Project.model_validate_json(row[0])
    if project.plan is None:
        raise ValueError("Project has no plan")
    if not project.planting_zones:
        raise ValueError("Project has no planting zones")
    zone = next(
        (item for item in project.planting_zones if item.id == args.zone_id),
        None,
    ) if args.zone_id else project.planting_zones[0]
    if zone is None:
        raise ValueError(f"Planting zone {args.zone_id} not found")

    repository = Mock()
    repository.get.return_value = project
    new_id = lambda: str(uuid4())
    evaluation = PlanEvaluation(ShapelyGeometryEngine(), new_id)
    changes = ChangeSetApplication(
        repository=repository,
        evaluation=evaluation,
        validation=PlanValidation(RuleBasedPlanValidator()),
        history_application=Mock(),
        history=Mock(),
        edit_lock=RLock(),
        invalidate_spacing=lambda _project_id: None,
        now=lambda: datetime.now(timezone.utc),
        new_id=new_id,
    )
    patterns = PatternApplication(
        repository, ShapelyCandidateGenerator(), evaluation, changes
    )
    plan_object_count = len(project.plan.objects)
    preview = patterns.preview_pattern(
        project.id,
        FillPatternRequest(
            base_plan_version=project.plan.version,
            plant_kind=args.kind,
            zone_ids=[zone.id],
            placement_mode="count",
            target_count=args.count,
            spacing_m=args.spacing,
            layout_radius_m=args.radius,
            edge_offset_m=args.edge_offset,
            size_class=args.size_class,
            species_revision_id=args.species,
        ),
    )
    change = preview.change_set
    staged = changes._previews[change.id].plan if change else None
    validation_issues = Counter(
        (item.severity, item.code) for item in staged.issues
    ) if staged else Counter()
    if len(project.plan.objects) != plan_object_count:
        raise AssertionError("Read-only preview mutated the source plan")
    result = {
        "project_id": project.id,
        "zone_id": zone.id,
        "requested": preview.requested_count,
        "generated": preview.generated_count,
        "accepted": preview.accepted_count,
        "shortfall": preview.capacity_shortfall,
        "can_apply": change.can_apply if change else False,
        "candidate_statuses": {
            f"{status}/{code}": count
            for (status, code), count in sorted(Counter(
                (item.status, item.code) for item in change.candidate_results
            ).items())
        } if change else {},
        "skipped": {item.code: item.count for item in preview.reason_summary},
        "validation_issues": {
            f"{severity}/{code}": count
            for (severity, code), count in sorted(validation_issues.items())
        },
        "source_plan_objects": plan_object_count,
        "staged_plan_objects": len(staged.objects) if staged else None,
        "source_calculation_scope": (
            project.geometry.calculation_scope if project.geometry else None
        ),
        "live_project_modified": False,
    }
    if change and change.can_apply and any(
        severity == "error" for severity, _code in validation_issues
    ):
        raise AssertionError(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--zone-id")
    parser.add_argument("--kind", choices=("tree", "shrub"), default="shrub")
    parser.add_argument("--species")
    parser.add_argument("--size-class", default="unspecified")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--spacing", type=float, default=3)
    parser.add_argument("--radius", type=float)
    parser.add_argument("--edge-offset", type=float, default=1)
    arguments = parser.parse_args()
    print(json.dumps(probe(arguments), ensure_ascii=False, indent=2))
