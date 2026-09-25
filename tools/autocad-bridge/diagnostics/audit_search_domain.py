"""Read-only native domain replay with area-weighted, overlapping causes.

No layer decisions, plan mutations, CAD repairs, or sampling fallback. Cell
geometry belongs to the service; obstacle measurements come from AutoCAD.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from app.composition import create_runtime
from app.native_query.domain_cells import certify_cell
from app.native_query.search_domain import (
    MIN_CELL_M,
    DomainProgress,
    build_domain,
    cell_polygon,
)
from shapely.geometry import shape


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--zone", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--refine-known-edges", action="store_true")
    parser.add_argument("--canopy", type=float, default=0)
    parser.add_argument("--roots", type=float, default=0)
    args = parser.parse_args()
    runtime = create_runtime(args.database)
    try:
        project = runtime.application.get(args.project)
        before = project.model_dump(mode="json")
        engine = runtime.application.geometry.native
        zone = next(z for z in project.planting_zones if z.id == args.zone)
        work = shape(zone.geometry)
        leaves = []

        def observed(
            cell, rows, layers, linear, factor, kind, radius, canopy, roots, reach
        ):
            verdict = certify_cell(
                cell,
                rows,
                layers,
                linear,
                factor,
                kind,
                radius,
                canopy,
                roots,
                reach,
                refine_known_edges=args.refine_known_edges,
            )
            if verdict.state in {"available", "excluded"} or (
                (verdict.state == "boundary" or verdict.refine)
                and cell.size > MIN_CELL_M
            ):
                return verdict
            sites = tuple(
                row
                for row in rows
                if layers.get(row.item.layer)
                and layers[row.item.layer].mapped_kind == "site_border"
                and layers[row.item.layer].mapping_confirmed
            )
            causes = []
            for row in rows:
                if row in sites:
                    continue
                detail = certify_cell(
                    cell,
                    (row, *sites),
                    layers,
                    linear,
                    factor,
                    kind,
                    radius,
                    canopy,
                    roots,
                    reach,
                )
                if detail.state not in {"unknown", "boundary"}:
                    continue
                # Site uncertainty is not evidence about this obstacle.
                if detail.reason == "site_membership":
                    continue
                layer = layers.get(row.item.layer)
                causes.append(
                    {
                        "reason": detail.reason,
                        "state": detail.state,
                        "layer": row.item.layer,
                        "routes": row.item.routes,
                        "role": layer.mapped_kind if layer else None,
                        "confirmed": layer.mapping_confirmed if layer else False,
                        "inventory_error": row.item.error,
                        "preparation_error": row.measurement.preparation_error
                        if row.measurement
                        else None,
                        "interior_known": row.measurement.interior_known
                        if row.measurement
                        else None,
                        "answer": row.answer.model_dump() if row.answer else None,
                    }
                )
            leaves.append(
                {
                    "cell": [cell.x, cell.y, cell.size],
                    "area_m2": cell_polygon(cell).intersection(work).area,
                    "reason": verdict.reason,
                    "causes": causes,
                }
            )
            return verdict

        progress = DomainProgress()
        with patch("app.native_query.search_domain.certify_cell", observed):
            for attempt in range(10):
                result = build_domain(
                    engine,
                    project,
                    zone.geometry,
                    0.65,
                    "shrub",
                    args.canopy,
                    args.roots,
                    progress=progress,
                )
                domain = result["ga_search_domain"]
                print(
                    json.dumps(
                        {
                            "pass": attempt + 1,
                            **{k: v for k, v in domain.items() if "geometry" not in k},
                        }
                    ),
                    flush=True,
                )
                if not progress.pending:
                    break
        totals = defaultdict(float)
        layers = defaultdict(float)
        objects = defaultdict(float)
        examples = {}
        for leaf in leaves:
            area = leaf["area_m2"]
            totals[leaf["reason"]] += area
            for key in {(c["reason"], c["layer"]) for c in leaf["causes"]}:
                layers[key] += area
            for key in {
                (c["reason"], c["layer"], tuple(c["routes"])) for c in leaf["causes"]
            }:
                objects[key] += area
            for cause in leaf["causes"]:
                key = (cause["reason"], cause["layer"], tuple(cause["routes"]))
                examples.setdefault(key, {"cell": leaf["cell"], **cause})
        after = runtime.application.get(args.project).model_dump(mode="json")
        assert before == after, "Project changed during read-only audit"
        report = {
            "project": args.project,
            "zone": args.zone,
            "state_version": project.state_version,
            "geometry_version": project.geometry_version,
            "source_sha256": engine.session.source_sha256,
            "domain": domain,
            "project_unchanged": True,
            "experimental_refine_known_edges": args.refine_known_edges,
            "plant_parameters": {
                "kind": "shrub",
                "radius_m": 0.65,
                "canopy_radius_m": args.canopy,
                "root_radius_m": args.roots,
            },
            "primary_reason_area_m2": dict(totals),
            "overlapping_layer_areas": [
                {"reason": k[0], "layer": k[1], "area_m2": area}
                for k, area in sorted(layers.items(), key=lambda x: -x[1])
            ],
            "overlapping_object_areas": [
                {
                    "reason": k[0],
                    "layer": k[1],
                    "routes": k[2],
                    "area_m2": area,
                    "example": examples[k],
                }
                for k, area in sorted(objects.items(), key=lambda x: -x[1])
            ],
            "unresolved_cells": [
                {k: v for k, v in leaf.items() if k != "causes"} for leaf in leaves
            ],
        }
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {
                    "primary": dict(totals),
                    "overlapping_layers": report["overlapping_layer_areas"][:15],
                    "overlapping_objects": report["overlapping_object_areas"][:10],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
