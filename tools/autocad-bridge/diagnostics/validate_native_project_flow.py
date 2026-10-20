"""Read-only end-to-end diagnostic for an admitted AutoCAD snapshot.

No project is published. Any simulated layer confirmations and acceptance of
partial source geometry live only in this process and are reported explicitly.
"""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.cad_bridge.compiler import compile_live_document
from app.cad_bridge.provider import (
    _native_shape,
    build_project_from_snapshot,
)
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.layers.contracts import (
    BoundaryCandidate,
    BoundaryCandidateStatus,
    LayerKind,
)
from app.dxf_import.source.editor_source import open_source_editor
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.checking.domain import PositionChecker
from app.operations.progress import WorkProgress
from app.planning.patterns.contracts import FillPatternRequest
from app.planning.patterns.generators import generate_fill
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from diagnostic_native_building import inject_diagnostic_native_building
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union
from validate_closure_candidate import validated_candidate


def inject_diagnostic_native_site(project: Project, report_path: Path, source_sha: str) -> dict:
    """Simulate one reviewed native REGION; never publish this synthetic layer.

    The source handle must already exist in the admitted AutoCAD capture. The
    original path remains visible as a reference, but only the native REGION
    candidate is used as the simulated site calculation boundary.
    """
    summary, polygon = validated_candidate(report_path, "open_site_3B3A")
    if summary["source_sha256"] != source_sha:
        raise ValueError("Native REGION report does not match the source DWG")
    if project.source_geometry is None:
        raise ValueError("Source geometry is missing")
    source_handle = summary["source_handle"]
    source_features = project.source_geometry.feature_collection["features"]
    matches = [
        feature for feature in source_features
        if feature.get("properties", {}).get("source_handle") == source_handle
        and not feature.get("properties", {}).get("source_instance_chain")
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one original source path for native REGION")
    source_feature = matches[0]
    source_layer_name = source_feature["properties"]["source_layer"]
    source_layer = next(
        layer for layer in project.layers if layer.source_name == source_layer_name
    )
    # Only the explicit native candidate supplies a territory surface here.
    # No open path, small closed control, or unrelated site layer is promoted.
    for layer in project.layers:
        if layer.mapped_kind == LayerKind.SITE_BORDER:
            layer.mapped_kind = LayerKind.IGNORE
            layer.mapping_confirmed = True
    site_layer = source_layer.model_copy(deep=True)
    site_layer.id = f"diagnostic-native-region-{source_handle}"
    site_layer.source_name = f"__diagnostic_native_region_{source_handle}__"
    site_layer.suggested_kind = LayerKind.SITE_BORDER
    site_layer.mapped_kind = LayerKind.SITE_BORDER
    site_layer.mapping_review_required = False
    site_layer.mapping_confirmed = True
    site_layer.object_count = 1
    site_layer.bounds = polygon.bounds
    site_layer.entity_types = {"REGION": 1}
    site_layer.projected_geometry_types = {"REGION": 1}
    site_layer.unsupported_geometry_types = {}
    site_layer.unreadable_geometry_count = 0
    site_layer.geometry_complete = True
    site_layer.boundary_candidate = BoundaryCandidate(
        status=BoundaryCandidateStatus.USABLE,
        basis="diagnostic_native_region_operator_simulation",
        area_m2=polygon.area,
        component_count=1,
    )
    project.layers.append(site_layer)
    source_features.append({
        "type": "Feature",
        "id": site_layer.id,
        "properties": {
            "kind": LayerKind.SITE_BORDER.value,
            "source_layer": site_layer.source_name,
            "source_handle": source_handle,
            "source_geometry_provider": "diagnostic_native_region_only",
            "source_diagnostic_generated": True,
            "source_native_area_units2": summary["native_region_area_m2"],
        },
        "geometry": mapping(polygon),
    })
    return {
        **summary,
        "original_layer": source_layer_name,
        "diagnostic_layer": site_layer.source_name,
        "original_path_retained": True,
        "original_site_mapping_simulated_as_ignore": True,
        "root_instance_verified": True,
        "instance_transform_verified": True,
        "project_coordinates_qualified": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--calculate", action="store_true")
    parser.add_argument(
        "--site-layer",
        help="Simulate operator selection of one source-backed territory layer",
    )
    parser.add_argument(
        "--native-site-candidate-report", type=Path,
        help="Simulate operator approval of a validated native REGION report; no project is saved",
    )
    parser.add_argument(
        "--native-building-candidate-report", type=Path,
        help="Compare one validated native building REGION before/after an unsaved review simulation",
    )
    parser.add_argument(
        "--native-building-candidate-case", default="open_building_78B3",
    )
    parser.add_argument(
        "--simulate-area-proposal", metavar="HANDLE",
        help="Compare a signed native area proposal before/after unsaved approval",
    )
    parser.add_argument(
        "--live-capture", action="store_true", required=True,
        help="Required: admit the native live-document capture exactly as the local app does",
    )
    args = parser.parse_args()
    if args.native_site_candidate_report and args.site_layer:
        parser.error("Select one territory evidence route, not both")
    if args.native_site_candidate_report and not args.calculate:
        parser.error("Native site candidate requires --calculate")
    if args.native_building_candidate_report and not args.calculate:
        parser.error("Native building candidate requires --calculate")
    if args.simulate_area_proposal and not (args.live_capture and args.calculate):
        parser.error("Signed proposal simulation requires --live-capture and --calculate")
    if args.simulate_area_proposal and args.native_building_candidate_report:
        parser.error("Choose one building candidate evidence route")
    result: dict = {
        "schema": "green-atlas.read-only-native-project-flow/1",
        "source": str(args.source.resolve()),
        "snapshot": str(args.snapshot.resolve()),
        "published": False,
        "simulated_user_confirmations": False,
        "simulated_partial_acceptance": False,
        "calculation_attempted": False,
    }
    try:
        source_bytes = args.source.read_bytes()
        digest = hashlib.sha256(source_bytes).hexdigest()
        result["source_sha256"] = digest
        capture_bytes = args.snapshot.read_bytes()
        snapshot = compile_live_document(
            capture_bytes, autocad_version="2027.0.1", target="macos-x86_64"
        )
        assert snapshot.source.live_capture is not None
        if snapshot.source.live_capture.original_disk_sha256 != digest:
            raise ValueError("Live capture does not match the original DWG bytes")
        result["snapshot_payload_sha256"] = snapshot.summary.payload_sha256
        result["snapshot_plugin_version"] = snapshot.extraction.plugin_version
        result["native_unresolved_instances"] = snapshot.summary.unresolved
        result["native_area_proposals"] = [
            {
                "id": proposal.id,
                "source_handle": proposal.source.handle,
                "source_instance_chain": proposal.source.instance_chain,
                "source_layer": proposal.layer,
                "closure_gap_wcs_xy_units": proposal.closure_gap_wcs_xy_units,
                "native_area_units2": proposal.preview.native_area_units2,
                "content_sha256": proposal.content_sha256,
                "active_for_calculation": False,
            }
            for proposal in snapshot.area_proposals or []
        ]
        result["missing_references"] = sorted({
            (record.unresolved_reference.block_name,
             record.unresolved_reference.stored_path)
            for record in snapshot.coverage
            if record.unresolved_reference is not None
        })
        project = build_project_from_snapshot(
            Project(name="Read-only diagnostic"),
            snapshot,
            filename=args.source.name,
            imported_at=datetime.now(UTC).isoformat(),
            source_sha256=hashlib.sha256(capture_bytes).hexdigest(),
            source_size=len(capture_bytes),
            capacity=SourceGeometryCapacity.process_bounded(),
        )
        assert project.source_geometry is not None
        assert project.source_file is not None
        imported_features = project.source_geometry.feature_collection["features"]
        result["imported_features"] = len(imported_features)
        result["imported_layers"] = len(project.layers)
        result["boundary_candidates"] = [
            {
                "layer": layer.source_name,
                **layer.boundary_candidate.model_dump(mode="json"),
            }
            for layer in project.layers
            if layer.boundary_candidate is not None
        ]
        result["imported_bounds"] = project.source_file.bounds
        # The project retains this list by reference. A later
        # diagnostic-only candidate must never be counted as a source object.
        source_backed_features = list(imported_features)
        result["feature_kinds"] = dict(
            Counter(
                feature["properties"]["kind"]
                for feature in imported_features
            )
        )
        result["site_border_features"] = [
            {
                "layer": feature["properties"]["source_layer"],
                "handle": feature["properties"].get("source_handle"),
                "geometry_type": feature["geometry"]["type"],
                "bounds": list(shape(feature["geometry"]).bounds),
                "area_m2": shape(feature["geometry"]).area,
            }
            for feature in imported_features
            if feature["properties"]["kind"] == "site_border"
        ]
        result["building_geometry_types"] = dict(
            Counter(
                feature["geometry"]["type"]
                for feature in imported_features
                if feature["properties"]["kind"] == "building"
            )
        )
        building_polygons = [
            shape(feature["geometry"])
            for feature in imported_features
            if feature["properties"]["kind"] == "building"
            and feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
        ]
        building_surface = unary_union(building_polygons)
        explicit_building_lines = [
            shape(feature["geometry"])
            for feature in imported_features
            if feature["properties"]["kind"] == "building"
            and feature["geometry"]["type"] == "LineString"
            and "Здания" in feature["properties"]["source_layer"]
            and "Части" not in feature["properties"]["source_layer"]
        ]
        exploratory_faces = list(polygonize(unary_union(explicit_building_lines)))
        result["exploratory_building_layer_faces"] = {
            "count": len(exploratory_faces),
            "total_area_m2": sum(face.area for face in exploratory_faces),
            "area_outside_native_building_polygons_m2": sum(
                face.difference(building_surface).area for face in exploratory_faces
            ),
            "production_geometry_changed": False,
        }
        result["open_explicit_building_paths"] = []
        for feature in imported_features:
            properties = feature["properties"]
            layer_name = properties["source_layer"]
            if (
                properties["kind"] != "building"
                or feature["geometry"]["type"] != "LineString"
                or "Здания" not in layer_name
                or "Части" in layer_name
            ):
                continue
            line = shape(feature["geometry"])
            points = list(line.coords)
            result["open_explicit_building_paths"].append(
                {
                    "layer": layer_name,
                    "handle": properties.get("source_handle"),
                    "vertex_count": len(points),
                    "length_m": line.length,
                    "endpoint_gap_m": shape(
                        {"type": "Point", "coordinates": points[0]}
                    ).distance(shape({"type": "Point", "coordinates": points[-1]})),
                    "length_outside_existing_building_polygons_m": (
                        line.difference(building_surface.buffer(0.001)).length
                    ),
                }
            )
        result["road_geometry_types"] = dict(
            Counter(
                feature["geometry"]["type"]
                for feature in imported_features
                if feature["properties"]["kind"] == "road"
            )
        )
        result["existing_green_geometry_types"] = dict(
            Counter(
                feature["geometry"]["type"]
                for feature in imported_features
                if feature["properties"]["kind"] == "existing_green"
            )
        )
        result["incomplete_layers"] = [
            {
                "source_name": layer.source_name,
                "unsupported_geometry_types": layer.unsupported_geometry_types,
            }
            for layer in project.layers
            if not layer.geometry_complete
        ]
        result["mapping_review_layers"] = [
            layer.source_name
            for layer in project.layers
            if layer.mapping_review_required and not layer.mapping_confirmed
        ]
        result["warnings"] = project.source_file.warnings
        if args.native_site_candidate_report:
            result["diagnostic_native_site_candidate"] = inject_diagnostic_native_site(
                project, args.native_site_candidate_report, digest
            )
        editor = open_source_editor(project)
        assert editor.geometry is not None
        assert editor.source_review is not None
        result["editor_map_ready"] = editor.map_ready
        result["editor_features"] = len(editor.geometry.feature_collection["features"])
        result["editor_source_review_issues"] = dict(
            Counter(issue.code for issue in editor.source_review.issues)
        )
        print("source editor opened", flush=True)
        if args.calculate:
            result["simulated_user_confirmations"] = True
            result["simulated_partial_acceptance"] = True
            result["calculation_attempted"] = True
            if args.site_layer:
                selected = next(
                    (layer for layer in project.layers
                     if layer.source_name == args.site_layer), None
                )
                if selected is None or selected.boundary_candidate is None or (
                    selected.boundary_candidate.status
                    != BoundaryCandidateStatus.USABLE
                ):
                    raise ValueError("Selected territory layer has no usable source area")
                result["simulated_site_layer_selection"] = {
                    "layer": args.site_layer,
                    "candidate": selected.boundary_candidate.model_dump(mode="json"),
                }
                selected.mapped_kind = LayerKind.SITE_BORDER
                selected.mapping_confirmed = True
            for layer in project.layers:
                if layer.mapping_review_required:
                    layer.mapping_confirmed = True
            project.source_file.accept_partial_geometry = True

            candidate_polygon: BaseGeometry | None = None
            candidate_point = None
            if args.simulate_area_proposal:
                matches = [
                    proposal for proposal in snapshot.area_proposals or []
                    if proposal.source.handle == args.simulate_area_proposal
                ]
                if len(matches) != 1:
                    raise ValueError("Expected exactly one signed native area proposal for handle")
                proposal = matches[0]
                _, factor = DXF_UNIT_FACTORS[snapshot.source.units_code]
                candidate_polygon = _native_shape(proposal.preview, factor)
                candidate_summary = {
                    "source_handle": proposal.source.handle,
                    "source_sha256": digest,
                    "native_region_area_m2": proposal.preview.native_area_units2 * factor * factor,
                    "proposal_id": proposal.id,
                    "proposal_sha256": proposal.content_sha256,
                    "closure_gap_wcs_xy_units": proposal.closure_gap_wcs_xy_units,
                }
            elif args.native_building_candidate_report:
                candidate_summary, candidate_polygon = validated_candidate(
                    args.native_building_candidate_report,
                    args.native_building_candidate_case,
                )
                if candidate_summary["source_sha256"] != digest:
                    raise ValueError("Native building report does not match source DWG")
            if candidate_polygon is not None:
                candidate_point = candidate_polygon.representative_point()
                baseline_geometry = ShapelyGeometryEngine().calculate(project)
                project.geometry = baseline_geometry
                before = PositionChecker(project).check(
                    candidate_point.x, candidate_point.y, 0.65, "shrub"
                )
                baseline_building_areas = [
                    shape(feature["geometry"])
                    for feature in baseline_geometry.feature_collection["features"]
                    if feature["properties"].get("rule_id")
                    == "pp743-3.6.3-building"
                ]
                result["diagnostic_native_building_candidate"] = (
                    inject_diagnostic_native_building(
                        project, candidate_summary, candidate_polygon
                    )
                )
                result["diagnostic_native_building_before"] = {
                    "point": [candidate_point.x, candidate_point.y],
                    "radius_m": 0.65,
                    "kind": "shrub",
                    "violation_rule_id": before.rule_id if before else None,
                    "known_building_forbidden_area_m2": unary_union(
                        baseline_building_areas
                    ).area,
                }
                project.geometry = None

            def progress(update: WorkProgress) -> None:
                if update.fraction is None or update.fraction > 0.8:
                    print(update.stage, flush=True)

            geometry = ShapelyGeometryEngine().calculate(project, progress)
            result["calculation_scope"] = geometry.calculation_scope
            result["calculated_features"] = len(geometry.feature_collection["features"])
            result["site_area_m2"] = geometry.site_area_m2
            result["allowed_area_m2"] = geometry.allowed_area_m2
            result["calculated_kinds"] = dict(
                Counter(
                    feature["properties"]["kind"]
                    for feature in geometry.feature_collection["features"]
                )
            )
            result["derived_areas"] = [
                {
                    "kind": feature["properties"]["kind"],
                    "rule_id": feature["properties"].get("rule_id"),
                    "area_m2": shape(feature["geometry"]).area,
                }
                for feature in geometry.feature_collection["features"]
                if feature["properties"].get("kind")
                in {"site_surface", "forbidden", "allowed"}
            ]
            # Witnesses come from the real admitted native polygons, not a
            # hand-drawn rectangle or a repaired line. This checks the same
            # placement guard used by the app after the simulated review.
            project.geometry = geometry
            checker = PositionChecker(project)
            if candidate_point is not None:
                after = checker.check(
                    candidate_point.x, candidate_point.y, 0.65, "shrub"
                )
                added_building_areas = [
                    shape(feature["geometry"])
                    for feature in geometry.feature_collection["features"]
                    if feature["properties"].get("rule_id")
                    == "pp743-3.6.3-building"
                ]
                result["diagnostic_native_building_after"] = {
                    "point": [candidate_point.x, candidate_point.y],
                    "violation_rule_id": after.rule_id if after else None,
                    "known_building_forbidden_area_m2": unary_union(
                        added_building_areas
                    ).area,
                }
            building_center_checks: Counter[str] = Counter()
            building_center_misses: list[dict[str, Any]] = []
            for feature in source_backed_features:
                if (
                    feature["properties"]["kind"] != "building"
                    or feature["geometry"]["type"] not in {"Polygon", "MultiPolygon"}
                ):
                    continue
                center = shape(feature["geometry"]).representative_point()
                violation = checker.check(center.x, center.y, 0.65, "shrub")
                rule = violation.rule_id if violation is not None else "no_violation"
                building_center_checks[rule] += 1
                if rule != "pp743-3.6.3-building" and len(building_center_misses) < 20:
                    building_center_misses.append(
                        {
                            "handle": feature["properties"].get("source_handle"),
                            "point": [center.x, center.y],
                            "rule_id": rule,
                        }
                    )
            result["native_building_center_checks"] = {
                "rule_counts": dict(building_center_checks),
                "other_rule_examples": building_center_misses,
                "radius_m": 0.65,
                "kind": "shrub",
                "real_source_polygons_only": True,
            }
            if args.site_layer or args.native_site_candidate_report:
                site_surfaces = [
                    shape(feature["geometry"])
                    for feature in geometry.feature_collection["features"]
                    if feature["properties"].get("kind") == "site_surface"
                ]
                if not site_surfaces:
                    raise ValueError("Selected source layer produced no site surface")
                site_surface = unary_union(site_surfaces)
                # Simulate the operator choosing the source-backed territory as
                # a working zone. Nothing is stored in a user project.
                project.planting_zones = [PlantingZoneAssignment(
                    id="read-only-source-site", label="Read-only source territory",
                    geometry=mapping(site_surface),
                )]
                project.source_review = None  # Normal post-calculation state.
                radius = 0.65
                safe_shape = shape(ShapelyGeometryEngine().automatic_safe_geometry(
                    project, mapping(site_surface), radius, "shrub"
                ))
                candidates = generate_fill(
                    FillPatternRequest(
                        base_plan_version=1, plant_kind="shrub",
                        zone_ids=["read-only-source-site"],
                        placement_mode="count", target_count=1,
                        layout="natural", spacing_m=2.15, edge_offset_m=0,
                    ),
                    [PlantingZoneAssignment(
                        id="read-only-source-site", label="Safe native territory",
                        geometry=mapping(safe_shape),
                    )],
                )
                witness = candidates[0] if candidates else None
                violation = (
                    PositionChecker(project).check(
                        witness.x, witness.y, radius, "shrub"
                    ) if witness else None
                )
                result["source_site_auto_shrub_probe"] = {
                    "work_zone_simulated": True,
                    "source_site_area_m2": site_surface.area,
                    "safe_area_m2": safe_shape.area,
                    "candidate_count": len(candidates),
                    "candidate": [witness.x, witness.y] if witness else None,
                    "violation_rule_id": violation.rule_id if violation else None,
                }
            print("calculation completed", flush=True)
        result["status"] = "complete"
    except Exception as error:  # noqa: BLE001 - preserve diagnostic failure evidence
        result["status"] = "failed"
        result["error_type"] = type(error).__name__
        result["error"] = str(error)
        print(f"failed: {type(error).__name__}: {error}", flush=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if result["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
