"""Validate provenance and geometric invariants of the map-first world packet."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from shapely.geometry import box, shape


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD = ROOT / ".runtime/map-first-world-20260920/world.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    args = parser.parse_args()
    packet = json.loads(args.world.read_text())
    checks = []

    def check(name: str, passed: bool, evidence: object) -> None:
        checks.append({"id": name, "passed": bool(passed), "evidence": evidence})

    check("schema", packet.get("schema") == "green-atlas.map-first-world.v1", packet.get("schema"))
    input_paths = [row["path"] for row in packet["inputs"].values()]
    check("no_dxf_input", not any(path.lower().endswith((".dxf", ".dwg")) for path in input_paths), input_paths)
    hash_results = {
        name: sha256(Path(row["path"])) == row["sha256"]
        for name, row in packet["inputs"].items()
    }
    check("source_hashes", all(hash_results.values()), hash_results)

    all_features = packet["buildings"] + packet["roads"] + packet["green_areas"] + packet["infrastructure"]
    ids = [row["id"] for row in all_features]
    check("unique_feature_ids", len(ids) == len(set(ids)), {"ids": len(ids), "unique": len(set(ids))})
    scope = box(*packet["scope"]["local_bounds_m"]).buffer(1e-4)
    outside = []
    for row in packet["buildings"] + packet["green_areas"] + packet["infrastructure"]:
        if not scope.covers(shape(row["geometry_local"])):
            outside.append(row["id"])
    for row in packet["roads"]:
        if not scope.covers(shape(row["centerline_geometry_local"])) or not scope.covers(shape(row["surface_geometry_local"])):
            outside.append(row["id"])
    check("geometry_clipped_to_scope", not outside, outside)

    bad_widths = [
        row["id"] for row in packet["roads"]
        if float(row["properties"]["width_m"]) <= 0
        or row["properties"]["width_status"] not in {
            "cartographic_explicit", "cartographic_explicit_partial_segment",
            "render_estimate_from_lane_count", "render_estimate_from_class",
            "independent_satellite_width_corroborated",
        }
    ]
    check("road_width_provenance", not bad_widths, bad_widths)
    corroborated = [
        row for row in packet["roads"]
        if row["properties"]["width_status"] == "independent_satellite_width_corroborated"
    ]
    bad_corroborated = [
        row["id"] for row in corroborated
        if not row["properties"].get("width_evidence")
        or not row["properties"]["width_evidence"].get("evidence_ids")
        or not row["properties"]["width_evidence"].get("review")
    ]
    check("corroborated_width_evidence", not bad_corroborated, {
        "count": len(corroborated), "invalid": bad_corroborated,
    })
    area_errors = {
        row["id"]: abs(float(row["source_area_m2"]) - float(row["triangulated_area_m2"]))
        for row in packet["render_layers"]
    }
    check("draped_surface_area", all(error <= 0.001 for error in area_errors.values()), area_errors)

    unknown_with_height = [
        row["id"] for row in packet["buildings"]
        if row["properties"]["height_status"] == "unknown" and row["properties"]["height_m"] is not None
    ]
    check("unknown_buildings_not_extrudable", not unknown_with_height, unknown_with_height)
    terrain = packet["terrain"]
    vertex_count = len(terrain["vertices"])
    bad_triangles = [triangle for triangle in terrain["triangles"] if len(triangle) != 3 or min(triangle) < 0 or max(triangle) >= vertex_count]
    expected = (int(terrain["rows"]) - 1) * (int(terrain["columns"]) - 1) * 2
    check("continuous_terrain_grid", not bad_triangles and len(terrain["triangles"]) == expected, {
        "vertices": vertex_count, "triangles": len(terrain["triangles"]), "expected_triangles": expected,
    })
    street_lengths = [
        float(row["length_in_project_m"]) for row in packet["named_reference_axes"]
        if row["name"] == "Кустанайская улица"
    ]
    check("kustanayskaya_axis_present", bool(street_lengths) and max(street_lengths) > 100.0, street_lengths)

    result = {
        "schema": "green-atlas.map-first-world-validation.v1",
        "world": {"path": str(args.world.resolve()), "sha256": sha256(args.world)},
        "checks": checks,
        "passed": all(row["passed"] for row in checks),
    }
    output = args.world.parent / "validation.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
