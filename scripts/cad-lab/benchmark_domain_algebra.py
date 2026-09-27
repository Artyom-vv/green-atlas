"""Read-only cost experiment, NOT a planting verdict or a CAD parser.

Uses the saved AutoCAD-derived MAP projection to estimate the cost of the old
buffer/union/difference approach. This projection is not admitted as a new
calculation snapshot. Results are deliberately not saved to a project and no
claim of complete geometry or safe planting follows from their area.
"""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from time import monotonic

from shapely.geometry import GeometryCollection, box, shape
from shapely.ops import unary_union

from app.native_query.calculation_rules import calculation_rule, effective_category
from app.native_query.derived_faces import LINE_CATEGORIES
from app.projects.contracts import Project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project")
    parser.add_argument("zone")
    parser.add_argument("--kind", choices=("tree", "shrub"), default="shrub")
    parser.add_argument("--radius", type=float, default=0.65)
    args = parser.parse_args()
    started = monotonic()
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        raw = db.execute("SELECT CAST(payload AS BLOB) FROM projects WHERE id=?", (args.project,)).fetchone()[0]
    project = Project.model_validate(json.loads(raw))
    work = shape(next(z.geometry for z in project.planting_zones if z.id == args.zone))
    layers = {layer.source_name: layer for layer in project.layers}
    source = project.geometry
    groups = defaultdict(list)
    site = []
    skipped = Counter()
    observed = Counter()
    prepare = monotonic()
    for feature in source.feature_collection["features"]:
        props = feature.get("properties", {})
        layer = layers.get(props.get("source_layer"))
        if not layer or not props.get("source_native_geometry"):
            continue
        if not layer.mapping_confirmed:
            skipped["unconfirmed_feature"] += 1
            continue
        if layer.mapped_kind in {"ignore", "lawn"} or props.get("source_object_interpretation") == "reference":
            continue
        geometry = shape(feature["geometry"])
        if geometry.is_empty or not geometry.is_valid:
            skipped["invalid_display_feature"] += 1
            continue
        if layer.mapped_kind == "site_border":
            if geometry.geom_type in {"Polygon", "MultiPolygon"}:
                site.append(geometry)
            continue
        rule = calculation_rule(layer, args.kind, args.radius)
        if rule is None:
            skipped["no_rule_feature"] += 1
            continue
        reach = rule.distance_m
        x0, y0, x1, y1 = work.bounds
        if not geometry.intersects(box(x0-reach, y0-reach, x1+reach, y1+reach)):
            continue
        if geometry.geom_type in {"Polygon", "MultiPolygon"} and (
            layer.mapped_kind == "utility" or effective_category(layer) in LINE_CATEGORIES
        ):
            geometry = geometry.boundary
        # The same positive offset applies to each item in this group. Unite
        # before buffering, as in the historical geometry engine.
        groups[reach].append(geometry)
        observed[layer.mapped_kind] += 1
    index_seconds = monotonic() - prepare
    compute = monotonic()
    buffers = [unary_union(parts).buffer(distance) for distance, parts in groups.items()]
    blocked = unary_union(buffers) if buffers else GeometryCollection()
    candidate = work.intersection(unary_union(site)).difference(blocked)
    cold_seconds = monotonic() - compute
    repeated = monotonic()
    again = work.intersection(unary_union(site)).difference(blocked)
    warm_seconds = monotonic() - repeated
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        after = db.execute("SELECT CAST(payload AS BLOB) FROM projects WHERE id=?", (args.project,)).fetchone()[0]
    print(json.dumps({
        "scope": "map_projection_cost_only_not_admitted_calculation", "saved_project": False,
        "payload_unchanged": hashlib.sha256(raw).digest() == hashlib.sha256(after).digest(),
        "state_version": project.state_version, "geometry_version": project.geometry_version,
        "features_used": dict(observed), "display_features_skipped": dict(skipped),
        "index_seconds": index_seconds, "cold_algebra_seconds": cold_seconds,
        "warm_algebra_seconds": warm_seconds, "total_seconds": monotonic()-started,
        "approximate_candidate_area_m2": candidate.area,
        "repeat_area_delta_m2": abs(again.area-candidate.area),
        "regulatory_or_complete_geometry_claim": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
