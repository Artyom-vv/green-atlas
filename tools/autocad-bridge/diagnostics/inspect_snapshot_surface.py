"""Explain one AutoCAD-admitted sampled surface rejected by planar projection."""

import argparse
import json
import math
from pathlib import Path

import ijson
from shapely.geometry import LineString, Polygon
from shapely.strtree import STRtree
from shapely.validation import explain_validity


def remove_exact_backtracks(points: list) -> tuple[list, list[float]]:
    """Counterfactual only: A→B→A has zero signed area."""

    retained = []
    removed_lengths = []
    for point in points:
        if len(retained) >= 2 and retained[-2] == point:
            removed_lengths.append(math.dist(retained[-2], retained[-1]))
            retained.pop()
        else:
            retained.append(point)
    return retained, removed_lengths


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--handle", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with args.snapshot.open("rb") as source:
        geometry = next(
            (
                item
                for item in ijson.items(source, "geometry.item")
                if item["identity"]["handle"] == args.handle
            ),
            None,
        )
    if geometry is None or geometry["kind"] != "region":
        raise ValueError("Requested native region is absent")
    outer = next(loop for loop in geometry["loops"] if loop["role"] == "outer")
    holes = [loop for loop in geometry["loops"] if loop["role"] == "hole"]
    outer_xy = [point[:2] for point in outer["coordinates"]]
    hole_xy = [[point[:2] for point in loop["coordinates"]] for loop in holes]
    polygon = Polygon(outer_xy, hole_xy)
    outer_polygon = Polygon(outer_xy)
    counterfactual_outer, removed_lengths = remove_exact_backtracks(outer_xy)
    counterfactual = Polygon(counterfactual_outer, hole_xy)
    segments = [
        LineString([outer_xy[index], outer_xy[index + 1]])
        for index in range(len(outer_xy) - 1)
    ]
    tree = STRtree(segments)
    nonadjacent_intersections = []
    for index, segment in enumerate(segments):
        for candidate_index in tree.query(segment):
            other_index = int(candidate_index)
            if other_index <= index + 1:
                continue
            if index == 0 and other_index == len(segments) - 1:
                continue
            other = segments[other_index]
            if not segment.intersects(other):
                continue
            intersection = segment.intersection(other)
            nonadjacent_intersections.append(
                {
                    "segment_a": index,
                    "segment_b": other_index,
                    "intersection_type": intersection.geom_type,
                    "xy": list(intersection.coords[0])
                    if intersection.geom_type == "Point"
                    else None,
                }
            )
            if len(nonadjacent_intersections) >= 8:
                break
        if len(nonadjacent_intersections) >= 8:
            break
    result = {
        "schema": "green-atlas.sampled-surface-diagnostic/1",
        "scope": "read-only admitted AutoCAD snapshot; no geometry repair",
        "snapshot": str(args.snapshot.resolve()),
        "handle": args.handle,
        "identity": geometry["identity"],
        "native_area_units2": float(geometry["native_area_units2"]),
        "achieved_tolerance_m": float(geometry["achieved_tolerance_m"]),
        "outer_points": len(outer_xy),
        "hole_points": [len(items) for items in hole_xy],
        "outer_polygon_valid": outer_polygon.is_valid,
        "outer_polygon_validity": explain_validity(outer_polygon),
        "complete_polygon_valid": polygon.is_valid,
        "complete_polygon_validity": explain_validity(polygon),
        "sampled_shoelace_area_units2": polygon.area,
        "absolute_area_delta_units2": abs(
            polygon.area - float(geometry["native_area_units2"])
        ),
        "nonadjacent_outer_intersections": nonadjacent_intersections,
        "counterfactual_exact_backtrack_removal": {
            "removed_count": len(removed_lengths),
            "removed_spur_lengths": removed_lengths,
            "polygon_valid": counterfactual.is_valid,
            "polygon_validity": explain_validity(counterfactual),
            "area_units2": counterfactual.area,
            "absolute_area_delta_vs_native_units2": abs(
                counterfactual.area - float(geometry["native_area_units2"])
            ),
            "production_geometry_changed": False,
        },
    }
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
