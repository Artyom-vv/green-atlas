"""Replay zone edits on a SQLite backup of the real native UI project only."""
from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from app.composition import create_runtime
from app.native_query.pilot_display import validate_pilot_working_zones
from app.native_query.pilot_provider import NativePilotGeometryEngine
from app.planting_zones.contracts import PlantingZoneAssignment
from app.planting_zones.domain import validate_planting_zones


def reject_cad_query(project, points):
    raise AssertionError("Empty-plan zone edits must not run a CAD position check")


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    copied = args.output / "projects.sqlite3"
    with closing(sqlite3.connect(f"file:{args.database.resolve()}?mode=ro", uri=True)) as source, \
            closing(sqlite3.connect(copied)) as destination:
        source.backup(destination)
    engine = NativePilotGeometryEngine(args.project, "zone-edit-replay-no-cad-query", reject_cad_query)
    runtime = create_runtime(copied, geometry_engine=engine, zone_validator=validate_pilot_working_zones)
    try:
        app = runtime.application
        project = app.get(args.project)
        if project.plan and project.plan.objects:
            raise ValueError("This reproduction expects the user's empty test plan; no plant will be removed")
        old_error = None
        try:
            validate_planting_zones(project, project.planting_zones)
        except ValueError as error:
            old_error = str(error)
        new_zone = PlantingZoneAssignment(id="diagnostic-new-zone", label="Новый контур", geometry={
            "type": "Polygon", "coordinates": [
                [[16000, -5580], [16010, -5580], [16010, -5570], [16000, -5570], [16000, -5580]]]})
        preview = app.preview_planting_zone(project.id, new_zone)
        assert preview["can_save"], preview
        saved = app.save_planting_zones(project.id, [*project.planting_zones, new_zone], preserve_plan=True)
        created = len(saved.planting_zones)
        deletions = []
        while saved.planting_zones:
            removed = saved.planting_zones[0]
            saved = app.save_planting_zones(project.id, saved.planting_zones[1:], preserve_plan=True)
            deletions.append({"label": removed.label, "remaining": len(saved.planting_zones)})
        receipt = {"scope": "isolated SQLite backup; user project untouched",
                   "source_db": str(args.database), "copied_db": str(copied),
                   "original_state_version": project.state_version, "old_full_validation_error": old_error,
                   "new_zone_preview": preview, "zones_after_creation": created,
                   "successful_deletions": deletions, "remaining_plants": len(saved.plan.objects) if saved.plan else 0,
                   "passed": True}
        (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print(json.dumps(receipt, ensure_ascii=False))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
