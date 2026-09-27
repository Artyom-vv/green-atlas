"""Read-only audit of building curves in one saved AutoCAD-backed project.

This is an exploratory diagnostic, not a CAD parser or an automatic closure
rule. Candidate polygons are counterfactual measurements and are never saved.
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import polygonize, unary_union

from trace_project_contour import load_project, require


def gap_bucket(gap_m: float) -> str:
    if gap_m < 0.02:
        return "under_20_mm"
    if gap_m < 0.1:
        return "20_to_100_mm"
    if gap_m < 1:
        return "100_mm_to_1_m"
    if gap_m < 10:
        return "1_to_10_m"
    return "10_m_or_more"


def nearest_other(
    geometries: list[tuple[dict, BaseGeometry]], index: int, target: BaseGeometry
) -> dict:
    options = [
        (geometry.distance(target), feature["properties"].get("source_handle"))
        for other_index, (feature, geometry) in enumerate(geometries)
        if other_index != index
    ]
    distance, handle = min(options)
    return {"distance_m": distance, "handle": handle}


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--layer-suffix", default="|Здания")
    parser.add_argument("--witness-xy", nargs=2, type=float)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    project = load_project(args.database, args.project_id)
    require(project.geometry is not None, "Project has no calculated geometry")
    require(project.source_file is not None, "Project has no source file")
    selected = [
        feature for feature in project.geometry.feature_collection["features"]
        if feature.get("properties", {}).get("kind") == "building"
        and feature.get("properties", {}).get("source_layer", "").endswith(args.layer_suffix)
    ]
    require(selected, f"No building features on layer suffix {args.layer_suffix}")
    layers = {feature["properties"]["source_layer"] for feature in selected}
    require(len(layers) == 1, f"Layer suffix is ambiguous: {sorted(layers)}")
    geometries = [(feature, shape(feature["geometry"])) for feature in selected]
    types = Counter(geometry.geom_type for _, geometry in geometries)
    linework_faces = list(polygonize(unary_union([
        geometry for _, geometry in geometries
        if isinstance(geometry, LineString)
    ])))
    lines = []
    for index, (feature, geometry) in enumerate(geometries):
        if not isinstance(geometry, LineString):
            continue
        coordinates = list(geometry.coords)
        first, last = Point(coordinates[0]), Point(coordinates[-1])
        gap = first.distance(last)
        try:
            candidate = Polygon(coordinates)
            candidate_valid = candidate.is_valid and candidate.area > 0
            candidate_area = candidate.area
        except ValueError:
            candidate_valid = False
            candidate_area = None
        props = feature["properties"]
        row = {
            "handle": props.get("source_handle"),
            "instance_chain": props.get("source_instance_chain"),
            "feature_id": feature.get("id"),
            "source_closed_path": props.get("source_closed_path"),
            "vertex_count": len(coordinates),
            "line_length_m": geometry.length,
            "endpoint_gap_m": gap,
            "gap_bucket": gap_bucket(gap),
            "counterfactual_straight_closure_polygon_valid": candidate_valid,
            "counterfactual_area_m2": candidate_area,
        }
        if gap < 0.02:
            chord = LineString([coordinates[0], coordinates[-1]])
            row["endpoint_a_nearest_other_building"] = nearest_other(geometries, index, first)
            row["endpoint_b_nearest_other_building"] = nearest_other(geometries, index, last)
            row["closure_chord_nearest_other_building"] = nearest_other(geometries, index, chord)
        lines.append(row)

    provenance = project.source_file.cad_snapshot_provenance
    result = {
        "schema": "green-atlas.building-layer-gap-audit/1",
        "scope": "read-only saved project; no AutoCAD mutation; no closure or planting performed",
        "exploratory_small_gap_threshold_m": 0.02,
        "warning": "Layer membership and a small endpoint gap do not prove a building area. Counterfactual closures are not approved geometry.",
        "project_id": args.project_id,
        "project_state_version": project.state_version,
        "project_geometry_version": project.geometry_version,
        "project_source_sha256": project.source_file.content_sha256,
        "project_snapshot_plugin_version": provenance.plugin_version if provenance else None,
        "layer": next(iter(layers)),
        "feature_count": len(selected),
        "geometry_type_counts": dict(types),
        "exploratory_layer_linework_faces": len(linework_faces),
        "exploratory_layer_linework_area_m2": sum(face.area for face in linework_faces),
        "line_gap_bucket_counts": dict(Counter(row["gap_bucket"] for row in lines)),
        "small_gap_valid_counterfactual_count": sum(
            row["counterfactual_straight_closure_polygon_valid"]
            for row in lines if row["endpoint_gap_m"] < 0.02
        ),
        "line_features": sorted(lines, key=lambda row: row["endpoint_gap_m"]),
    }
    if args.witness_xy is not None:
        witness = Point(*args.witness_xy)
        result["exploratory_layer_linework_witness"] = {
            "xy": list(args.witness_xy),
            "covered_by_any_polygonized_face": any(
                face.covers(witness) for face in linework_faces
            ),
        }
    require(args.output.parent.is_dir(), "Output directory does not exist")
    with args.output.open("x") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
