"""Exercise product zone/planting services in an isolated database.

Suggested mappings are NOT approved semantics. Never applies the preview or
modifies user projects; keeps the complete response for review.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

from app.composition import create_runtime
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.layer_contracts import LayerKind
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.domain import PositionChecker
from app.planning.pattern_contracts import FillPatternRequest
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from shapely.geometry import mapping, shape
from shapely.ops import unary_union


def run(source: Path, output: Path, site_layer: str | None = None):
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    imported = EzdxfReader().read_prepared_file(source)
    project = Project(name="Isolated planting diagnostic", layers=imported.layers,
                      source_geometry=imported.geometry,
                      coordinate_reference=imported.coordinate_reference)
    for layer in project.layers:
        if site_layer is not None and layer.suggested_kind == LayerKind.SITE_BORDER:
            layer.mapped_kind = LayerKind.IGNORE
        else:
            layer.mapped_kind = layer.suggested_kind
        if site_layer is not None and layer.source_name == site_layer:
            layer.mapped_kind = LayerKind.SITE_BORDER
    project.geometry = ShapelyGeometryEngine().calculate(project)
    project.map_ready = True
    areas = [f for f in project.geometry.feature_collection["features"]
             if f["properties"].get("kind") == "allowed"]
    report = {"scope": "unreviewed mapping diagnostic, NOT normative acceptance",
              "source_sha256": digest, "applied": False,
              "selected_site_layer": site_layer}
    runtime = create_runtime(output / "isolated.sqlite3")
    try:
        runtime.project_repository.create(project)
        if not areas:
            report["status"] = "no_trial_area"
        else:
            area = shape(areas[0]["geometry"])
            borders = [shape(f["geometry"]) for f in project.geometry.feature_collection["features"]
                       if f["properties"].get("kind") == "site_surface"]
            if not borders:
                borders = [shape(f["geometry"]) for f in project.geometry.feature_collection["features"]
                           if f["properties"].get("kind") == "site_border"]
            if borders:
                site = unary_union(borders).buffer(0)
                report["area_boundary_check"] = {
                    "covers": site.covers(area),
                    "outside_area_m2": area.difference(site).area,
                    "outside_length_m": area.difference(site).length,
                }
            zone = PlantingZoneAssignment(label="Diagnostic calculated area",
                                         geometry=mapping(area))
            report["diagnostic_inset_m"] = 0
            saved = runtime.application.save_planting_zones(project.id, [zone])
            saved = runtime.application.create_manual_plan(project.id)
            checker = PositionChecker(saved)
            report["tree_space_check"] = {
                "trial_zone_area_m2": shape(zone.geometry).area,
                "space_for_1_5m_radius_m2": checker.hard_safe_area(
                    shape(zone.geometry), 1.5, "tree").area,
                "site_layers": sorted({f["properties"].get("source_layer", "")
                    for f in project.geometry.feature_collection["features"]
                    if f["properties"].get("kind") == "site_border"}),
            }
            preview = runtime.application.preview_pattern(project.id, FillPatternRequest(
                base_plan_version=saved.plan.version, zone_ids=[zone.id],
                placement_mode="count", target_count=20))
            report["status"] = "preview_returned"
            report["preview"] = preview.model_dump(mode="json")
    except ValueError as error:
        report.update(status="rejected", reason=str(error))
    finally:
        runtime.close()
    report["source_unchanged"] = digest == hashlib.sha256(source.read_bytes()).hexdigest()
    report["seconds"] = time.monotonic() - started
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "preview"}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--site-layer")
    args = parser.parse_args()
    run(args.source, args.output, args.site_layer)
