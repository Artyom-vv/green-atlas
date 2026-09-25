"""Launch the existing Green Atlas HTTP/UI flow with native-only geometry.

Creates a separately named test project. Old source geometry is used ONLY for
display/coordinates; actual checks open the full declared CAD package in AutoCAD.
The mismatch in capture age is explicit, not a qualified GAOPEN import.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

import uvicorn
from app import composition
from app.composition import create_runtime
from app.dxf_import.layer_contracts import Layer, LayerKind
from app.native_query.pilot_display import (
    synchronize_pilot_display,
    validate_pilot_working_zones,
)
from app.native_query.pilot_provider import NativePilotGeometryEngine
from app.planning.contracts import Plan
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from ui_native_runner import PilotWindowRunner


def rectangle(label, bounds):
    x0, y0, x1, y1 = bounds
    return PlantingZoneAssignment(label=label, geometry={"type": "Polygon", "coordinates": [
        [[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]]})


def add_native_boundaries(project):
    """Explicit reviewed pilot mappings, visible in the normal layer list.

    These two full-host boundaries were absent from the historic display capture.
    The calculation territory remains explicitly 6E16/16020, not their interiors.
    """
    names = {layer.source_name for layer in project.layers}
    changed = False
    for name in ("01_10004141_Границы работ|ДВ_ГП_П_Граница работ", "_ГП_Граница проектирования"):
        if name not in names:
            project.layers.append(Layer(id=str(uuid4()), source_name=name,
                suggested_kind=LayerKind.SITE_BORDER, mapped_kind=LayerKind.SITE_BORDER,
                mapping_confirmed=True, mapping_review_required=False,
                object_count=1, color="#2563eb", suggestion_reasons=[
                    "Явное сопоставление native-пилота: граница работ, не физическое препятствие"]))
            changed = True
    return changed


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ("display-db", "baseline", "bundle", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--display-project", required=True)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # AutoCAD's SCR decoder misreads Cyrillic paths as an ANSI codepage.
    # Keep command/request paths ASCII; CAD filenames still travel in argv.
    session_path = root / "native-session.json"
    if session_path.exists():
        native_root = Path(json.loads(session_path.read_text())["root"])
    else:
        native_root = Path(tempfile.mkdtemp(prefix="ga-native-ui-", dir="/private/tmp"))
        session_path.write_text(json.dumps({"root": str(native_root)}))
    runner = PilotWindowRunner(args.baseline, args.bundle, native_root)
    manifest = root / "project.json"
    database = root / "projects.sqlite3"
    if manifest.exists():
        project_id = json.loads(manifest.read_text())["project_id"]
    else:
        with sqlite3.connect(f"file:{args.display_db.resolve()}?mode=ro", uri=True) as connection:
            row = connection.execute("SELECT payload FROM projects WHERE id=?", (args.display_project,)).fetchone()
        if row is None:
            raise ValueError("Display project not found")
        project = Project.model_validate_json(row[0])
        project.id = project_id = str(uuid4())
        project.name = "Кустанайская • AutoCAD Native API • ТЕСТ"
        project.plan = Plan()
        add_native_boundaries(project)
        project.source_review = None
        project.site_area_m2 = project.planning_area_m2 = project.allowed_area_m2 = None
        project.geometry_version += 1
        project.planting_zones = [
            rectangle("Здание и парковка — отрицательный контроль", (15958,-5290,16050,-5210)),
            rectangle("Проезжая часть — отрицательный контроль", (16075,-5420,16115,-5365)),
            rectangle("Окно предыдущих native-проверок", (15880,-5030,15975,-4930)),
        ]
        if project.geometry:
            synchronize_pilot_display(project)
            project.geometry.calculation_scope = "available_data"
            project.geometry.allowed_area_m2 = None
        if project.source_file:
            project.source_file.accept_partial_geometry = True
        repository = SqliteProjectRepository(database)
        repository.create(project)
        repository.close()
        manifest.write_text(json.dumps({"project_id": project_id, "source_sha256": runner.identity,
            "display_project": args.display_project,
            "scope": "Native API pilot on saved full CAD; display from earlier capture; no GAOPEN/capture parity claim",
            "zones": [zone.model_dump() for zone in project.planting_zones]}, ensure_ascii=False, indent=2))
    repository = SqliteProjectRepository(database)
    current = repository.get(project_id)
    boundaries_changed = add_native_boundaries(current)
    display_changed = synchronize_pilot_display(current)
    if boundaries_changed or display_changed:
        current.geometry_version += 1
        repository.save(current)
    repository.close()
    engine = NativePilotGeometryEngine(project_id, runner.identity, runner)
    composition._runtime = create_runtime(database, geometry_engine=engine,
                                         zone_validator=validate_pilot_working_zones)
    from app.main import app
    print(f"NATIVE_UI_PROJECT={project_id}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
