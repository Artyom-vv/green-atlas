"""Validate the evening Kustanayskaya GIS + inventory prototype."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--render-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    world = json.loads(args.world.read_text())
    report = json.loads(args.report.read_text())
    render = json.loads(args.render_receipt.read_text())
    rows = world["inventory_vegetation"]
    ids = {int(row["inventory_id"]) for row in rows}
    min_x, min_y, max_x, max_y = (float(value) for value in world["scope"]["local_bounds_m"])
    terrain_model = world["terrain"]["source_model"]
    terrain_audit = terrain_model["render_grid_audit"]

    checks = {
        "world_schema": world["schema"] == "green-atlas.kustanayskaya-working-prototype.v1",
        "record_count_1278": len(rows) == 1278,
        "unique_inventory_ids": len(ids) == len(rows),
        "source_numbering_gaps_preserved": sorted(set(range(1, 1283)) - ids) == [300, 566, 675, 951],
        "tree_quantity_882": sum(int(row["tree_count"]) for row in rows) == 882,
        "shrub_quantity_1490": sum(int(row["shrub_count"]) for row in rows) == 1490,
        "all_rows_positioned": report["inventory"]["unresolved_rows"] == 0,
        "all_positions_inside_scope": all(
            min_x <= float(row["position_local_xyz_m"][0]) <= max_x
            and min_y <= float(row["position_local_xyz_m"][1]) <= max_y
            for row in rows
        ),
        "source_topography_controls_connected": (
            world["terrain"]["status"] == "source_topography_robust_quadratic_surface"
            and int(terrain_model["source_control_count"]) == 2267
            and int(terrain_model["deduplicated_bare_earth_supports"]) >= 1700
        ),
        "glo30_not_used_for_active_ground_z": all(
            "copernicus" not in str(row["z_status"]).casefold() for row in rows
        ),
        "terrain_height_delta_bounded": float(terrain_audit["height_delta_m"]) <= 15.0,
        "terrain_neighbor_slope_bounded": float(terrain_audit["maximum_neighbor_slope_ratio"]) <= 0.06,
        "dataset_pdf_and_photo_evidence_recorded": (
            len(world["dataset_evidence"]["source_pdfs"]) == 4
            and int(world["dataset_evidence"]["existing_condition_photos"]["image_count"]) == 77
        ),
        "gis_context_nonempty": len(world["buildings"]) >= 100 and len(world["roads"]) >= 100,
        "candidate_rmse_recorded": 0 < float(report["georeference"]["rmse_inliers_m"]) < 4,
        "render_input_hash": render["input"]["sha256"] == sha256(args.world),
        "render_tracks_full_inventory": render["inventory"]["source_records"] == 1278,
        "render_window_has_real_assets": render["inventory"]["tree_records_rendered"] >= 100,
        "render_not_mislabeled_beauty": render["status"] == "3d_data_diagnostic_not_beauty",
        "render_outputs_exist": all(
            Path(render["output"][key]).is_file()
            for key in ("aerial_image", "street_image", "blend")
        ),
    }
    result = {
        "schema": "green-atlas.kustanayskaya-prototype-validation.v1",
        "passed": all(checks.values()),
        "checks": checks,
        "inputs": {
            "world": {"path": str(args.world.resolve()), "sha256": sha256(args.world)},
            "report": {"path": str(args.report.resolve()), "sha256": sha256(args.report)},
            "render_receipt": {"path": str(args.render_receipt.resolve()), "sha256": sha256(args.render_receipt)},
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
