"""Exercise product zone/planting services from an admitted AutoCAD snapshot.

Suggested mappings are NOT approved semantics. Never applies the preview or
modifies user projects; keeps the complete response for review.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from app.cad_bridge import CadSnapshot
from app.cad_bridge.provider import build_dxf_import_from_snapshot
from app.composition import create_runtime
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.layer_contracts import LayerKind
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.domain import PositionChecker
from app.planning.pattern_contracts import FillPatternRequest
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from shapely.geometry import mapping, shape
from shapely.ops import unary_union


def run(
    source: Path,
    snapshot_path: Path,
    output: Path,
    site_layer: str | None = None,
):
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    timings: dict[str, float] = {}

    def finish_stage(name: str, stage_started: float) -> float:
        now = time.monotonic()
        timings[name] = now - stage_started
        return now

    stage_started = time.monotonic()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    stage_started = finish_stage("hash_source", stage_started)
    snapshot = CadSnapshot.model_validate_json(snapshot_path.read_bytes())
    stage_started = finish_stage("parse_snapshot", stage_started)
    imported = build_dxf_import_from_snapshot(
        snapshot,
        source_sha256=digest,
        capacity=SourceGeometryCapacity.process_bounded(),
    )
    stage_started = finish_stage("build_import", stage_started)
    project = Project(
        name="Isolated planting diagnostic",
        layers=imported.layers,
        source_geometry=imported.geometry,
        coordinate_reference=imported.coordinate_reference,
    )
    excluded_incomplete_layers: list[dict[str, object]] = []
    for layer in project.layers:
        if site_layer is not None and layer.suggested_kind == LayerKind.SITE_BORDER:
            layer.mapped_kind = LayerKind.IGNORE
        else:
            layer.mapped_kind = layer.suggested_kind
        if site_layer is not None and layer.source_name == site_layer:
            layer.mapped_kind = LayerKind.SITE_BORDER
        # This isolated probe deliberately accepts the automatic mapping only
        # to exercise the calculation path.  The report remains non-normative;
        # production keeps the operator review step.
        layer.mapping_confirmed = True
        layer.mapping_review_required = False
        if (
            not layer.geometry_complete
            and layer.mapped_kind != LayerKind.SITE_BORDER
            and layer.mapped_kind != LayerKind.IGNORE
        ):
            excluded_incomplete_layers.append(
                {
                    "source_name": layer.source_name,
                    "suggested_kind": layer.mapped_kind.value,
                    "unresolved": dict(layer.unsupported_geometry_types),
                }
            )
            layer.mapped_kind = LayerKind.IGNORE
    stage_started = finish_stage("map_layers", stage_started)
    project.geometry = ShapelyGeometryEngine().calculate(project)
    stage_started = finish_stage("calculate_geometry", stage_started)
    project.map_ready = True
    areas = [
        f
        for f in project.geometry.feature_collection["features"]
        if f["properties"].get("kind") == "allowed"
    ]
    report = {
        "scope": "unreviewed mapping diagnostic, NOT normative acceptance",
        "source_sha256": digest,
        "applied": False,
        "selected_site_layer": site_layer,
        "geometry_provider": "autocad_snapshot_v1",
        "snapshot_payload_sha256": snapshot.summary.payload_sha256,
        "snapshot_native_geometry": len(snapshot.geometry),
        "snapshot_unresolved_instances": snapshot.summary.unresolved,
        "excluded_incomplete_layers": excluded_incomplete_layers,
        "timings": timings,
    }
    runtime = create_runtime(output / "isolated.sqlite3")
    stage_started = finish_stage("create_runtime", stage_started)
    try:
        runtime.project_repository.create(project)
        stage_started = finish_stage("persist_project", stage_started)
        if not areas:
            report["status"] = "no_trial_area"
        else:
            area = shape(areas[0]["geometry"])
            borders = [
                shape(f["geometry"])
                for f in project.geometry.feature_collection["features"]
                if f["properties"].get("kind") == "site_surface"
            ]
            if not borders:
                borders = [
                    shape(f["geometry"])
                    for f in project.geometry.feature_collection["features"]
                    if f["properties"].get("kind") == "site_border"
                ]
            if borders:
                site = unary_union(borders).buffer(0)
                report["area_boundary_check"] = {
                    "covers": site.covers(area),
                    "outside_area_m2": area.difference(site).area,
                    "outside_length_m": area.difference(site).length,
                }
            zone = PlantingZoneAssignment(
                label="Diagnostic calculated area", geometry=mapping(area)
            )
            report["diagnostic_inset_m"] = 0
            saved = runtime.application.save_planting_zones(project.id, [zone])
            stage_started = finish_stage("save_planting_zones", stage_started)
            saved = runtime.application.create_manual_plan(project.id)
            stage_started = finish_stage("create_manual_plan", stage_started)
            checker = PositionChecker(saved)
            report["tree_space_check"] = {
                "trial_zone_area_m2": shape(zone.geometry).area,
                "space_for_1_5m_radius_m2": checker.hard_safe_area(
                    shape(zone.geometry), 1.5, "tree"
                ).area,
                "site_layers": sorted(
                    {
                        f["properties"].get("source_layer", "")
                        for f in project.geometry.feature_collection["features"]
                        if f["properties"].get("kind") == "site_border"
                    }
                ),
            }
            stage_started = finish_stage("tree_space_check", stage_started)
            preview = runtime.application.preview_pattern(
                project.id,
                FillPatternRequest(
                    base_plan_version=saved.plan.version,
                    zone_ids=[zone.id],
                    placement_mode="count",
                    target_count=20,
                ),
            )
            stage_started = finish_stage("preview_pattern", stage_started)
            report["status"] = "preview_returned"
            report["preview"] = preview.model_dump(mode="json")
    except ValueError as error:
        report.update(status="rejected", reason=str(error))
    finally:
        runtime.close()
    report["source_unchanged"] = (
        digest == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    finish_stage("verify_source_unchanged", stage_started)
    report["timings"] = timings
    report["seconds"] = time.monotonic() - started
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "preview"}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--site-layer")
    args = parser.parse_args()
    run(args.source, args.snapshot, args.output, args.site_layer)
