"""Read-only trace of saved plants, source obstacles, and calculated overlays.

No CAD parsing, repair, or project mutation. Straight closure of an open path
is used ONLY as a labelled diagnostic hypothesis, never as accepted geometry.
Run with PYTHONPATH=apps/api and the API virtual environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from app.geometry.domain import PositionChecker
from app.projects.contracts import Project
from shapely import STRtree
from shapely.geometry import LineString, Point, Polygon, shape


def audit(database: Path, project_id: str) -> dict:
    with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT payload, state_version FROM projects WHERE id=?", (project_id,)
        ).fetchone()
    if row is None:
        raise ValueError("Project not found")
    project = Project.model_validate_json(row[0])
    if project.geometry is None or project.plan is None:
        raise ValueError("Project needs saved geometry and plan")
    features = project.geometry.feature_collection["features"]
    geometries = [shape(feature["geometry"]) for feature in features]
    index = STRtree(geometries)
    checker = PositionChecker(project)
    obstacle_indices = {
        kind: [i for i, f in enumerate(features) if f.get("properties", {}).get("kind") == kind]
        for kind in ("building", "road")
    }
    obstacle_trees = {
        kind: STRtree([geometries[i] for i in indices])
        for kind, indices in obstacle_indices.items()
    }
    hypotheses = []
    for i, (feature, geometry) in enumerate(zip(features, geometries, strict=True)):
        props = feature.get("properties", {})
        if props.get("kind") not in {"building", "road"}:
            continue
        if not isinstance(geometry, LineString) or len(geometry.coords) < 3:
            continue
        closed = Polygon(geometry.coords)
        if closed.is_valid and closed.area > 0:
            hypotheses.append((i, closed, Point(geometry.coords[0]).distance(Point(geometry.coords[-1]))))

    def identity(i: int) -> dict:
        props = features[i].get("properties", {})
        return {
            "feature_id": features[i].get("id"),
            "kind": props.get("kind"),
            "layer": props.get("source_layer"),
            "handle": props.get("source_handle"),
            "instance_chain": props.get("source_instance_chain"),
            "entity_type": props.get("entity_type"),
            "source_closed_path": props.get("source_closed_path"),
            "geometry_type": geometries[i].geom_type,
            "area_m2": geometries[i].area,
            "bounds": geometries[i].bounds,
        }

    plants = []
    for number, plant in enumerate(project.plan.objects, 1):
        point = Point(plant.x, plant.y)
        hits = [int(i) for i in index.query(point, predicate="intersects")]
        violation = checker.check(plant.x, plant.y, plant.radius, plant.kind)
        nearest = {}
        for kind, tree in obstacle_trees.items():
            found = tree.nearest(point)
            if found is not None:
                i = obstacle_indices[kind][int(found)]
                nearest[kind] = {**identity(i), "distance_m": geometries[i].distance(point)}
        plants.append({
            "number": number, "id": plant.id, "kind": plant.kind,
            "xy": [plant.x, plant.y], "status": plant.status,
            "checker_violation": asdict(violation) if violation else None,
            "nearest_saved_obstacles": nearest,
            "covering_features": [identity(i) for i in hits],
            "open_path_closure_hypotheses": [
                {**identity(i), "hypothetical_area_m2": polygon.area,
                 "endpoint_gap_m": gap, "distance_to_saved_path_m": geometries[i].distance(point)}
                for i, polygon, gap in hypotheses if polygon.covers(point)
            ],
        })
    aggregate = Counter()
    for plant in plants:
        for item in plant["open_path_closure_hypotheses"]:
            aggregate[(item["kind"], item["layer"], item["handle"], tuple(item["instance_chain"] or []))] += 1
    provenance = project.source_file.cad_snapshot_provenance if project.source_file else None
    return {
        "scope": "Read-only saved project; NOT an independent native CAD validation",
        "hypothesis_warning": "Straight closures are diagnostic only, not evidence of authored areas or approved repairs",
        "database": str(database.resolve()), "project_id": project_id,
        "state_version": row[1], "plan_version": project.plan.version,
        "payload_sha256": hashlib.sha256(row[0].encode()).hexdigest(),
        "plugin_version": provenance.plugin_version if provenance else None,
        "source_role_geometry_counts": {
            kind: dict(Counter(geometries[i].geom_type for i in indices))
            for kind, indices in obstacle_indices.items()
        },
        "plants": plants,
        "summary": {
            "plant_count": len(plants),
            "checker_blocked_count": sum(p["checker_violation"] is not None for p in plants),
            "saved_building_or_road_surface_hits": sum(any(f["kind"] in {"building", "road"} for f in p["covering_features"]) for p in plants),
            "plants_inside_open_path_closure_hypotheses": sum(bool(p["open_path_closure_hypotheses"]) for p in plants),
            "hypothesis_membership_groups": [
                {"kind": k, "layer": l, "handle": h, "instance_chain": c, "plants": n}
                for (k, l, h, c), n in aggregate.most_common()
            ],
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.database, args.project_id)
    with args.output.open("x") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
