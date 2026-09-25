"""Read-only native → saved project → PositionChecker trace for selected handles.

Use the project's Python environment. This diagnostic does not infer a missing
surface or call the manual-placement application, which also checks spacing.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon, box, shape

from app.geometry.domain import PositionChecker, rule_distance
from app.projects.contracts import Project


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_project(database: Path, project_id: str) -> Project:
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            "SELECT payload FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    require(row is not None, f"Project {project_id} not found")
    return Project.model_validate_json(row[0])


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--native-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    fixture = json.loads(args.fixture.read_bytes())
    native = json.loads(args.native_report.read_bytes())
    require(fixture["source"] == native["source"], "Different DWG source paths")
    require(fixture["source_sha256"] == native["source_sha256"], "Different DWG hashes")
    require(native["source_unchanged"] is True, "Native source changed")
    with Path(native["source"]).open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    require(digest == native["source_sha256"], "Current DWG changed")
    require(len(native["cases"]) == len(fixture["cases"]), "Case count changed")

    project = load_project(args.database, args.project_id)
    require(project.geometry is not None, "Project has no calculated geometry")
    checker = PositionChecker(project)
    features = project.geometry.feature_collection["features"]
    provenance = project.source_file.cad_snapshot_provenance if project.source_file else None
    referenced_source = next((
        reference for reference in provenance.live_references or []
        if Path(reference.stored_path) == Path(native["source"])
    ), None) if provenance else None
    result = {
        "schema": "green-atlas.contour-loss-trace/1",
        "scope": "saved-project PositionChecker, not full manual/automatic placement",
        "project_id": args.project_id,
        "project_state_version": project.state_version,
        "project_geometry_version": project.geometry_version,
        "project_source_review_pending": project.source_review is not None,
        "project_source_sha256": project.source_file.content_sha256 if project.source_file else None,
        "project_snapshot_plugin_version": provenance.plugin_version if provenance else None,
        "project_live_capture_path": (
            provenance.live_capture.original_path
            if provenance and provenance.live_capture else None
        ),
        "native_DWG_listed_as_live_reference": referenced_source is not None,
        "native_DWG_reference_record": referenced_source.record_handle if referenced_source else None,
        "native_source_sha256": digest,
        "native_report": str(args.native_report.resolve()),
        "local_safe_area_scope": "16x16 m window, mapped constraints only; generator and selected area not checked",
        "cases": [],
    }
    for expected, observed in zip(fixture["cases"], native["cases"], strict=True):
        handle = expected["name"]
        require(observed["name"] == handle, "Case identity changed")
        matches = [
            feature for feature in features
            if feature.get("properties", {}).get("source_handle") == handle
            and feature.get("properties", {}).get("source_instance_chain")
            == expected["host_instance_chain"]
            and feature.get("properties", {}).get("source_layer", "").endswith("|Здания")
        ]
        require(len(matches) == 1, f"Expected one building instance for {handle}")
        feature = matches[0]
        geometry = shape(feature["geometry"])
        point = next(point for point in expected["points"] if point["label"] == "interior_candidate")
        x, y, _ = point["xyz"]
        center = Point(x, y)
        checks = {}
        for plant_kind in ("shrub", "tree"):
            violation = checker.check(x, y, 0.1, plant_kind)
            local = box(x - 8, y - 8, x + 8, y + 8)
            safe = checker.hard_safe_area(local, 0.1, plant_kind)
            checks[plant_kind] = {
                "required_building_setback_m": rule_distance("building", plant_kind),
                "position_checker_violation": violation.code if violation else None,
                "position_checker_actual_m": violation.actual if violation else None,
                "local_safe_area_covers_point": safe.covers(center),
            }
        # A straight last-to-first chord is deliberately only a counterfactual:
        # the native source did not author it, and a valid polygon is not proof.
        would_be_polygon = (
            Polygon(geometry.coords)
            if isinstance(geometry, LineString) and len(geometry.coords) >= 3
            else None
        )
        result["cases"].append({
            "handle": handle,
            "host_instance_chain": expected["host_instance_chain"],
            "layer": feature["properties"]["source_layer"],
            "native_class": observed["entities"][0]["class"],
            "native_authored_closed": observed["entities"][0].get("closed"),
            "native_endpoint_gap_units": observed["entities"][0].get("endpoint_gap_units"),
            "native_conversion_status": observed["conversion_status"],
            "native_region_count": len(observed["regions"]),
            "project_feature_id": feature.get("id"),
            "project_kind": feature["properties"].get("kind"),
            "project_geometry_type": geometry.geom_type,
            "project_geometry_area_m2": geometry.area,
            "point_kind": "visual/interior candidate; independently verified only where native region exists",
            "point_xy": [x, y],
            "project_feature_covers_point": geometry.covers(center),
            "distance_to_project_feature_m": geometry.distance(center),
            "counterfactual_straight_closure_valid": (
                would_be_polygon.is_valid and would_be_polygon.area > 0
                if would_be_polygon is not None else None
            ),
            "counterfactual_straight_closure_area_m2": (
                would_be_polygon.area if would_be_polygon is not None else None
            ),
            "counterfactual_straight_closure_covers_point": (
                would_be_polygon.covers(center) if would_be_polygon is not None else None
            ),
            "position_checks": checks,
        })
    with args.output.open("x") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
