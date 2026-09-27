"""Select a source-constrained street camera instead of hand-tuning a view."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.prepared import prep


ROOT = Path(__file__).resolve().parents[2]


def unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy)
    return dx/length, dy/length


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--surfaces", type=Path, default=ROOT/".runtime/deterministic-render-audit-20260919/authored-surfaces.geojson")
    parser.add_argument("--scene", type=Path, default=ROOT/".runtime/deterministic-render-pipeline-20260920/scene-packet.json")
    parser.add_argument("--context", type=Path, default=ROOT/".runtime/geospatial-context-packet-20260920/context-packet.json")
    parser.add_argument("--output", type=Path, default=ROOT/".runtime/geospatial-context-packet-20260920/camera-selection.json")
    args = parser.parse_args()

    scene = json.loads(args.scene.read_text())
    context = json.loads(args.context.read_text())
    surfaces = json.loads(args.surfaces.read_text())
    origin = scene["coordinates"]["local_origin_xyz"]
    road = unary_union([
        shape(feature["geometry"])
        for feature in surfaces["features"]
        if feature["properties"]["class"] == "road"
    ])
    # Authored surfaces are in source coordinates; all other inputs below are local.
    road_local = shape({
        "type": road.geom_type,
        "coordinates": (
            [[(x-origin[0], y-origin[1]) for x, y in ring] for ring in road.__geo_interface__["coordinates"]]
            if road.geom_type == "Polygon" else
            [[[ (x-origin[0], y-origin[1]) for x, y in ring] for ring in polygon]
             for polygon in road.__geo_interface__["coordinates"]]
        ),
    })

    road_prepared = prep(road_local)
    buildings = [
        (shape(feature["geometry"]), feature["properties"]["height_m"])
        for feature in context["features"]
        if feature["properties"]["kind"] == "building" and feature["properties"].get("height_m")
    ]
    trees = [
        (row["id"], row["position_local"][0], row["position_local"][1], row["height_m"], row["species"])
        for row in scene["vegetation_candidates"]
        if row["species"] in {"Клен", "Береза", "Ель"}
    ]

    min_x, min_y, max_x, max_y = road_local.bounds
    candidates = []
    for yi in range(math.floor(min_y)+1, math.ceil(max_y), 2):
        for xi in range(math.floor(min_x)+1, math.ceil(max_x), 2):
            position = Point(xi, yi)
            if not road_prepared.covers(position):
                continue
            road_margin = road_local.boundary.distance(position)
            if road_margin < 3.0:
                continue
            camera_building_clearance = min(
                polygon.distance(position) for polygon, _height in buildings
            )
            if camera_building_clearance < 10.0:
                continue
            for angle_degrees in range(0, 360, 5):
                angle = math.radians(angle_degrees)
                direction = (math.cos(angle), math.sin(angle))
                dx, dy = direction
                road_forward_continuity = 0.0
                for distance in range(2, 62, 2):
                    if not road_prepared.covers(Point(xi+dx*distance, yi+dy*distance)):
                        break
                    road_forward_continuity = float(distance)
                if road_forward_continuity < 24.0:
                    continue
                # `left_axis` is positive on the rendered left side for the
                # Blender camera convention used by this pipeline.
                left_axis = (-dy, dx)
                visible_buildings = 0
                building_weight = 0.0
                for polygon, height in buildings:
                    centroid = polygon.centroid
                    rx, ry = centroid.x-xi, centroid.y-yi
                    distance = math.hypot(rx, ry)
                    if not 20 < distance < 320:
                        continue
                    forward = rx*dx+ry*dy
                    side = abs(rx*left_axis[0]+ry*left_axis[1])
                    if forward > 0 and math.atan2(side, forward) < math.radians(38):
                        visible_buildings += 1
                        building_weight += min(height, 30.0)/max(distance, 40.0)

                best_tree = None
                visible_trees = []
                close_obstruction_penalty = 0.0
                for tree_id, tx, ty, tree_height, tree_species in trees:
                    rx, ry = tx-xi, ty-yi
                    forward = rx*dx+ry*dy
                    rendered_side = rx*left_axis[0]+ry*left_axis[1]
                    screen_angle = math.degrees(math.atan2(abs(rendered_side), forward)) if forward > 0 else 180.0
                    signed_angle = math.degrees(math.atan2(rendered_side, forward)) if forward > 0 else 180.0
                    if 2.0 < forward < 100.0 and abs(signed_angle) < 38.0:
                        visible_trees.append((tree_id, tree_species, forward, rendered_side, tree_height))
                    if 0.0 < forward < 6.0 and abs(signed_angle) < 16.0:
                        close_obstruction_penalty += (6.0-forward)*(16.0-abs(signed_angle))*0.08
                    # Negative means right side of the image.
                    if not (
                        4.0 < forward < 24.0 and -14.0 < rendered_side < -1.5
                        and 12.0 < screen_angle < 36.0
                    ):
                        continue
                    tree_score = (
                        8.0-abs(forward-12.0)*0.30-abs(screen_angle-28.0)*0.18
                        + min(tree_height, 16.0)*0.08
                    )
                    if best_tree is None or tree_score > best_tree[0]:
                        best_tree = (tree_score, tree_id, forward, rendered_side, tree_species)
                if best_tree is None:
                    continue
                if visible_buildings < 1 or len(visible_trees) < 2:
                    continue
                tree_weight = sum(min(row[4], 18.0)/max(math.hypot(row[2], row[3]), 18.0) for row in visible_trees)
                score = (
                    road_margin*0.5 + min(camera_building_clearance, 40.0)*0.08
                    + visible_buildings*0.2 + building_weight*3.0 + best_tree[0]
                    + len(visible_trees)*0.55 + tree_weight*1.4 + road_forward_continuity*0.20
                    - close_obstruction_penalty
                )
                candidates.append({
                    "score": round(score, 6),
                    "position_local": [float(xi), float(yi), 1.62],
                    "direction_xy": [round(dx, 9), round(dy, 9)],
                    "target_local": [round(xi+dx*70, 6), round(yi+dy*70, 6), 2.65],
                    "lens_mm": 28.0,
                    "road_margin_m": round(road_margin, 3),
                    "camera_building_clearance_m": round(camera_building_clearance, 3),
                    "road_forward_continuity_m": road_forward_continuity,
                    "visible_buildings": visible_buildings,
                    "visible_source_backed_trees": len(visible_trees),
                    "close_tree_obstruction_penalty": round(close_obstruction_penalty, 6),
                    "framing_tree": {
                        "id": best_tree[1], "forward_m": round(best_tree[2], 3),
                        "rendered_side_m": round(best_tree[3], 3),
                        "species": best_tree[4],
                        "screen_angle_deg": round(math.degrees(math.atan2(abs(best_tree[3]), best_tree[2])), 3),
                    },
                })
    if not candidates:
        raise RuntimeError("No camera candidate passed road/tree/building gates")
    candidates.sort(key=lambda row: (-row["score"], row["position_local"]))
    selected = dict(candidates[0])
    selected["base_street_direction_xy"] = selected["direction_xy"]
    selected["camera_yaw_from_street_deg"] = 0.0
    selected["framing_tree"] = {
        **selected["framing_tree"],
        "screen_angle_after_yaw_deg": -selected["framing_tree"]["screen_angle_deg"],
    }
    result = {
        "schema": "green-atlas.context-camera-selection.v1",
        "status": "deterministic_candidate_alignment_camera",
        "constraints": {
            "camera_inside_authored_road": True,
            "minimum_road_edge_clearance_m": 3.0,
            "minimum_straight_road_continuity_m": 24.0,
            "minimum_building_clearance_m": 10.0,
            "source_backed_tree_on_right_foreground": True,
            "visible_source_backed_tree_minimum": 2,
            "building_cone_half_angle_deg": 38,
            "camera_height_m": 1.62,
        },
        "selected": selected,
        "alternatives": candidates[1:10],
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
