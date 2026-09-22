"""Build a render admission manifest for a continuous, source-backed world.

This tool does not invent missing streets, terrain or object dimensions.  It
combines the existing DXF audit, the candidate map alignment, OSM/Overture
inventories and a broad DEM audit into one machine-readable decision.  A beauty
render is admitted only when the required geometry is continuous and every
truth-critical transform/dimension has an explicit evidence status.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
from shapely.affinity import translate
from shapely.geometry import shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT = ROOT / ".runtime/deterministic-render-audit-20260920-expanded"
DEFAULT_LEGACY_AUDIT = ROOT / ".runtime/deterministic-render-audit-20260919"
DEFAULT_SCENE = ROOT / ".runtime/deterministic-render-pipeline-20260920-expanded/scene-packet.json"
DEFAULT_CONTEXT = ROOT / ".runtime/geospatial-context-packet-20260920-expanded/context-packet.json"
DEFAULT_OVERTURE = ROOT / ".runtime/overture-kustanayskaya-20260920"
DEFAULT_DEM = DEFAULT_OVERTURE / "Copernicus_DSM_COG_10_N55_00_E037_00_DEM.tif"
DEFAULT_OUTPUT = ROOT / ".runtime/world-source-manifest-20260920"
DEFAULT_CAD_TERRAIN_EVIDENCE = DEFAULT_OUTPUT / "cad-terrain-evidence.json"
DEFAULT_ANNOTATION_TERRAIN = ROOT / ".runtime/annotation-constrained-terrain-20260920/terrain.json"
DEFAULT_ANNOTATION_TERRAIN_VALIDATION = ROOT / ".runtime/annotation-constrained-terrain-20260920/validation.json"
DEFAULT_CURB_MESH = ROOT / ".runtime/source-curb-mesh-20260920/curb-mesh.json"
DEFAULT_CURB_MESH_VALIDATION = ROOT / ".runtime/source-curb-mesh-20260920/validation.json"
OVERTURE_RELEASE = "2026-08-19.0"
OVERTURE_TYPES = (
    "building", "building_part", "segment", "connector", "infrastructure",
    "land", "land_cover", "land_use", "water",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_ref(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def dxf_bbox_to_wgs84(bbox: list[float], alignment: dict[str, Any]) -> list[float]:
    contract = alignment["similarity_transform_row_vector"]
    scale = float(contract["scale"])
    rotation = np.asarray(contract["rotation"], dtype=float)
    translation = np.asarray(contract["translation"], dtype=float)
    lon0, lat0 = alignment["projection_before_fit"]["reference_lon_lat"]
    earth_radius = float(alignment["projection_before_fit"]["earth_radius_m"])
    corners = []
    for x in (bbox[0], bbox[2]):
        for y in (bbox[1], bbox[3]):
            tangent = ((np.asarray([x, y]) - translation) / scale) @ rotation.T
            lon = lon0 + math.degrees(tangent[0] / (earth_radius * math.cos(math.radians(lat0))))
            lat = lat0 + math.degrees(tangent[1] / earth_radius)
            corners.append((float(lon), float(lat)))
    return [
        min(point[0] for point in corners), min(point[1] for point in corners),
        max(point[0] for point in corners), max(point[1] for point in corners),
    ]


def audit_overture(directory: Path) -> dict[str, Any]:
    inventories: dict[str, Any] = {}
    raw: dict[str, list[dict[str, Any]]] = {}
    for feature_type in OVERTURE_TYPES:
        path = directory / f"{feature_type}.geojson"
        if not path.exists():
            inventories[feature_type] = {"status": "missing", "features": 0}
            raw[feature_type] = []
            continue
        features = load(path).get("features", [])
        raw[feature_type] = features
        inventories[feature_type] = {
            "status": "captured",
            "features": len(features),
            "geometry_types": dict(sorted(Counter(
                feature.get("geometry", {}).get("type", "missing") for feature in features
            ).items())),
            "source": source_ref(path),
        }

    buildings = [feature.get("properties", {}) for feature in raw["building"]]
    building_sources = Counter()
    for properties in buildings:
        for source in properties.get("sources") or []:
            building_sources[source.get("dataset") or "unknown"] += 1
    height_count = sum(properties.get("height") is not None for properties in buildings)
    floor_count = sum(properties.get("num_floors") is not None for properties in buildings)
    vertical_count = sum(
        properties.get("height") is not None or properties.get("num_floors") is not None
        for properties in buildings
    )

    segments = [feature.get("properties", {}) for feature in raw["segment"]]
    road_segments = [properties for properties in segments if properties.get("subtype") == "road"]
    infrastructure = [feature.get("properties", {}) for feature in raw["infrastructure"]]
    return {
        "release": OVERTURE_RELEASE,
        "license": "ODbL-compatible mixed sources; retain feature-level sources and attribution",
        "inventories": inventories,
        "building_quality": {
            "features": len(buildings),
            "explicit_height": height_count,
            "num_floors": floor_count,
            "height_or_floors": vertical_count,
            "vertical_coverage_ratio": round(vertical_count / len(buildings), 9) if buildings else 0.0,
            "source_datasets": dict(sorted(building_sources.items())),
            "building_parts": len(raw["building_part"]),
        },
        "transportation_quality": {
            "segments": len(segments),
            "road_segments": len(road_segments),
            "segments_with_width_rules": sum(bool(properties.get("width_rules")) for properties in road_segments),
            "segments_with_surface": sum(bool(properties.get("road_surface")) for properties in road_segments),
            "geometry_model": "centerline_network_not_surface_polygons",
        },
        "infrastructure_quality": {
            "features": len(infrastructure),
            "classes": dict(sorted(Counter(
                properties.get("class") or "unknown" for properties in infrastructure
            ).items())),
            "placement_status": "cartographic_candidate_until_shared_transform_is_admitted",
        },
    }


def _load_rasterio() -> Any:
    try:
        import rasterio  # type: ignore
        return rasterio
    except ImportError:
        vendor = ROOT / ".runtime/vendor/rasterio"
        if vendor.exists():
            sys.path.insert(0, str(vendor))
            import rasterio  # type: ignore
            return rasterio
        raise


def audit_dem(path: Path, bbox_wgs84: list[float]) -> dict[str, Any]:
    if not path.exists():
        return {"status": "missing", "source": str(path.resolve())}
    try:
        rasterio = _load_rasterio()
        from rasterio.windows import from_bounds  # type: ignore
    except ImportError:
        return {
            "status": "captured_not_inspected_missing_rasterio",
            "source": source_ref(path),
        }
    with rasterio.open(path) as dataset:
        window = from_bounds(*bbox_wgs84, dataset.transform).round_offsets().round_lengths()
        values = dataset.read(1, window=window, masked=True)
        latitude = sum((bbox_wgs84[1], bbox_wgs84[3])) / 2
        x_resolution_m = abs(dataset.transform.a) * 111_320.0 * math.cos(math.radians(latitude))
        y_resolution_m = abs(dataset.transform.e) * 111_320.0
        return {
            "status": "captured_context_only",
            "source": source_ref(path),
            "dataset": "Copernicus DEM GLO-30 Public / 2021 AWS COG",
            "source_url": (
                "https://copernicus-dem-30m.s3.amazonaws.com/"
                "Copernicus_DSM_COG_10_N55_00_E037_00_DEM/"
                "Copernicus_DSM_COG_10_N55_00_E037_00_DEM.tif"
            ),
            "attribution": (
                "Copernicus DEM accessed from the Registry of Open Data on AWS; "
                "retain the applicable Copernicus/ESA/DLR/Airbus notice"
            ),
            "model": "digital_surface_model_including_buildings_infrastructure_and_vegetation",
            "crs": str(dataset.crs),
            "vertical_reference": "EGM2008 (dataset specification); not converted to Moscow height system",
            "pixel_size_approx_m": [round(x_resolution_m, 3), round(y_resolution_m, 3)],
            "scope_window": {
                "bbox_wgs84": [round(value, 10) for value in bbox_wgs84],
                "width": int(values.shape[1]),
                "height": int(values.shape[0]),
                "samples": int(values.count()),
                "min_m": round(float(values.min()), 6),
                "max_m": round(float(values.max()), 6),
                "mean_m": round(float(values.mean()), 6),
                "stddev_m": round(float(values.std()), 6),
            },
            "fitness": "broad_context_grade_only_not_curbs_roads_or_tree_ground_z",
        }


def detect_transport_conflicts(
    authored_surfaces: Path, context: dict[str, Any], local_origin: list[float]
) -> list[dict[str, Any]]:
    surfaces = load(authored_surfaces).get("features", [])
    lawn = unary_union([
        translate(shape(feature["geometry"]), xoff=-local_origin[0], yoff=-local_origin[1])
        for feature in surfaces if feature.get("properties", {}).get("class") == "lawn"
    ])
    road = unary_union([
        translate(shape(feature["geometry"]), xoff=-local_origin[0], yoff=-local_origin[1])
        for feature in surfaces if feature.get("properties", {}).get("class") == "road"
    ])
    conflicts = []
    for feature in context.get("features", []):
        properties = feature.get("properties", {})
        if properties.get("kind") != "transportation":
            continue
        if properties.get("highway") in {"footway", "path", "steps"}:
            continue
        geometry = shape(feature["geometry"])
        lawn_length = geometry.intersection(lawn).length
        if lawn_length <= 0.25:
            continue
        conflicts.append({
            "external_feature_id": properties.get("id"),
            "external_class": properties.get("highway"),
            "external_name": properties.get("name"),
            "centerline_inside_exact_lawn_m": round(lawn_length, 3),
            "centerline_inside_exact_road_m": round(geometry.intersection(road).length, 3),
            "resolution": "suppress_external_geometry_inside_DXF_authority_mask_and_review_seam",
        })
    return sorted(conflicts, key=lambda row: (-row["centerline_inside_exact_lawn_m"], row["external_feature_id"]))


def gate(gate_id: str, passed: bool, actual: Any, required: Any, evidence: str) -> dict[str, Any]:
    return {
        "id": gate_id,
        "status": "passed" if passed else "blocked",
        "actual": actual,
        "required": required,
        "evidence": evidence,
    }


def build_manifest(
    source_contract_path: Path,
    scene_path: Path,
    context_path: Path,
    alignment_path: Path,
    osm_receipt_path: Path,
    overture_dir: Path,
    dem_path: Path,
    cad_terrain_evidence_path: Path | None = None,
    annotation_terrain_path: Path | None = None,
    annotation_terrain_validation_path: Path | None = None,
    curb_mesh_path: Path | None = None,
    curb_mesh_validation_path: Path | None = None,
) -> dict[str, Any]:
    source_contract = load(source_contract_path)
    scene = load(scene_path)
    context = load(context_path)
    alignment = load(alignment_path)
    osm_receipt = load(osm_receipt_path)
    scope_bbox_source = source_contract["scope"]["bbox_local_m"]
    scope_bbox_wgs84 = dxf_bbox_to_wgs84(scope_bbox_source, alignment)
    overture = audit_overture(overture_dir)
    overture["retrieval"] = {
        "client": "overturemaps 1.0.2",
        "release": OVERTURE_RELEASE,
        "bbox_wgs84": osm_receipt["bbox_wgs84"],
        "feature_types": list(OVERTURE_TYPES),
        "documentation": "https://docs.overturemaps.org/getting-data/",
    }
    dem = audit_dem(dem_path, scope_bbox_wgs84)
    cad_terrain_evidence = (
        load(cad_terrain_evidence_path)
        if cad_terrain_evidence_path is not None and cad_terrain_evidence_path.exists()
        else None
    )
    annotation_terrain = (
        load(annotation_terrain_path)
        if annotation_terrain_path is not None and annotation_terrain_path.exists()
        else None
    )
    annotation_validation = (
        load(annotation_terrain_validation_path)
        if annotation_terrain_validation_path is not None
        and annotation_terrain_validation_path.exists()
        else None
    )
    curb_mesh = (
        load(curb_mesh_path) if curb_mesh_path is not None and curb_mesh_path.exists() else None
    )
    curb_validation = (
        load(curb_mesh_validation_path)
        if curb_mesh_validation_path is not None and curb_mesh_validation_path.exists()
        else None
    )
    local_origin = scene["coordinates"]["local_origin_xyz"]
    conflicts = detect_transport_conflicts(
        source_contract_path.parent / "authored-surfaces.geojson", context, local_origin
    )

    quality = scene["quality"]
    scope_area = float(quality["scope_area_m2"])
    xy_ratio = float(quality["authored_xy_area_m2"]) / scope_area
    z_ratio = float(quality["renderable_estimated_z_area_m2"]) / scope_area
    alignment_checkpoints = int(context["coordinate_contract"]["independent_survey_checkpoints"])
    osm_buildings = int(context["quality"]["building_count"])
    osm_height_ratio = float(context["quality"]["building_height_coverage"])
    overture_roads = int(overture["transportation_quality"]["road_segments"])
    overture_widths = int(overture["transportation_quality"]["segments_with_width_rules"])

    gates = [
        gate(
            "shared_horizontal_transform",
            alignment_checkpoints >= 3 and alignment["status"] == "survey_control_validated",
            {
                "status": alignment["status"],
                "independent_survey_checkpoints": alignment_checkpoints,
                "fit_rmse_m": alignment["ransac"]["rmse_inliers_m"],
                "max_inlier_residual_m": alignment["ransac"]["max_inlier_residual_m"],
            },
            "verified CRS or >=3 independent surveyed checkpoints",
            "candidate-osm-alignment.json + context coordinate contract",
        ),
        gate(
            "continuous_terrain",
            annotation_terrain is not None
            and annotation_terrain["status"] == "admitted_engineering_tin"
            and annotation_terrain["coverage"]["terrain_ratio_of_scope"] >= 0.98
            and annotation_validation is not None
            and annotation_validation["status"] == "passed",
            {
                "previous_experimental_z_coverage_ratio": round(z_ratio, 9),
                "annotation_derived_z_coverage_ratio": (
                    annotation_terrain["coverage"]["terrain_ratio_of_scope"]
                    if annotation_terrain else 0.0
                ),
                "annotation_terrain_status": (
                    annotation_terrain["status"] if annotation_terrain else "missing"
                ),
                "annotation_terrain_validation": (
                    annotation_validation["status"] if annotation_validation else "missing"
                ),
                "vertical_reference": scene["coordinates"]["vertical_reference"],
                "external_dem_status": dem["status"],
                "cad_input_audit": cad_terrain_evidence["summary"] if cad_terrain_evidence else "not_run",
            },
            "continuous DTM/TIN with verified vertical datum; DEM may fill context only",
            "scene quality + Copernicus audit",
        ),
        gate(
            "continuous_surface_semantics",
            xy_ratio >= 0.98,
            {
                "project_scope_xy_coverage_ratio": round(xy_ratio, 9),
                "unknown_area_m2": quality["unknown_xy_area_m2"],
            },
            ">=0.98 coverage after external base plus explicit DXF override masks",
            "DXF source contract and scene quality",
        ),
        gate(
            "physical_road_dimensions",
            overture_roads > 0 and overture_widths / overture_roads >= 0.95,
            {
                "road_segments": overture_roads,
                "segments_with_width_rules": overture_widths,
                "coverage_ratio": round(overture_widths / overture_roads, 9) if overture_roads else 0.0,
                "source_curb_riser_length_m": (
                    curb_mesh["quality"]["covered_curb_length_m"] if curb_mesh else 0.0
                ),
                "source_curb_mesh_status": curb_mesh["status"] if curb_mesh else "missing",
                "source_curb_mesh_validation": (
                    curb_validation["status"] if curb_validation else "missing"
                ),
            },
            ">=0.95 explicit or independently measured widths; centerline class defaults do not count",
            "Overture segment inventory",
        ),
        gate(
            "source_priority_conflicts_resolved",
            len(conflicts) == 0,
            {"unresolved_conflicts": len(conflicts), "conflicts": conflicts},
            "zero external road centerlines crossing exact DXF lawn after authority masking",
            "aligned context intersected with authored HATCH",
        ),
        gate(
            "building_vertical_data",
            osm_height_ratio >= 0.9,
            {
                "osm_buildings": osm_buildings,
                "osm_height_coverage_ratio": osm_height_ratio,
                "overture_vertical_coverage_ratio": overture["building_quality"]["vertical_coverage_ratio"],
            },
            ">=0.90 height/levels coverage for buildings eligible to enter the render volume",
            "OSM/Overture inventories",
        ),
        gate(
            "vegetation_ground_contact",
            quality["tree_candidates"] > 0
            and quality["tree_candidates_with_estimated_z"] == quality["tree_candidates"],
            {
                "inventory_trees": quality["tree_candidates"],
                "trees_with_ground_z": quality["tree_candidates_with_estimated_z"],
            },
            "every rendered inventory tree has admitted terrain Z and an asset contact plane",
            "scene vegetation candidates",
        ),
    ]
    blockers = [row["id"] for row in gates if row["status"] != "passed"]

    return {
        "schema": "green-atlas.world-source-manifest.v1",
        "site": {
            "name": "Кустанайская улица, Москва",
            "scope_bbox_dxf_m": scope_bbox_source,
            "scope_bbox_wgs84_candidate": [round(value, 10) for value in scope_bbox_wgs84],
            "external_context_bbox_wgs84": osm_receipt["bbox_wgs84"],
        },
        "status": "beauty_render_admitted" if not blockers else "blocked_before_beauty_render",
        "truth_model": {
            "canonical_world": "WGS84 source records + local metric render frame",
            "local_metric_transform": "single shared transform; per-layer nudging is forbidden",
            "unknown_policy": "unknown remains explicit and cannot be filled by geometry generation",
            "authority_order": [
                "reviewed engineering survey / official terrain model",
                "reviewed topographic and project DXF inside explicit validity masks",
                "authoritative municipal/open GIS",
                "Overture/OSM cartographic context",
                "imagery-derived measurements after review",
            ],
        },
        "sources": {
            "dxf_source_contract": {**source_ref(source_contract_path), "quality": {
                "xy_coverage_ratio": round(xy_ratio, 9),
                "z_coverage_ratio": round(z_ratio, 9),
                "source_spot_labels": source_contract["terrain_status"]["source_spot_labels"],
                "source_piket_symbols": source_contract["terrain_status"]["source_piket_symbols"],
                "machine_readable_ground_z": source_contract["terrain_status"]["machine_readable_ground_z"],
            }},
            "project_scene": source_ref(scene_path),
            "candidate_alignment": {**source_ref(alignment_path), "quality": alignment["ransac"]},
            "osm_snapshot": {**source_ref(Path(osm_receipt["source"])), "quality": osm_receipt["counts"]},
            "overture": overture,
            "copernicus_dem": dem,
            "cad_terrain_evidence": (
                {**source_ref(cad_terrain_evidence_path), "summary": cad_terrain_evidence["summary"]}
                if cad_terrain_evidence is not None and cad_terrain_evidence_path is not None
                else {"status": "not_run"}
            ),
            "annotation_constrained_terrain": (
                {
                    **source_ref(annotation_terrain_path),
                    "status": annotation_terrain["status"],
                    "coverage": annotation_terrain["coverage"],
                    "validation": (
                        source_ref(annotation_terrain_validation_path)
                        if annotation_terrain_validation_path is not None
                        and annotation_terrain_validation_path.exists()
                        else {"status": "missing"}
                    ),
                }
                if annotation_terrain is not None and annotation_terrain_path is not None
                else {"status": "missing"}
            ),
            "source_curb_mesh": (
                {
                    **source_ref(curb_mesh_path),
                    "status": curb_mesh["status"],
                    "quality": curb_mesh["quality"],
                    "validation": (
                        source_ref(curb_mesh_validation_path)
                        if curb_mesh_validation_path is not None
                        and curb_mesh_validation_path.exists()
                        else {"status": "missing"}
                    ),
                }
                if curb_mesh is not None and curb_mesh_path is not None
                else {"status": "missing"}
            ),
            "mapillary": {
                "status": "not_captured",
                "allowed_role": "appearance and object-existence review; never silent geometry authority",
                "credential": "API access token required for automated capture",
            },
            "moscow_official_terrain": {
                "status": (
                    "not_present_in_input_bundle_confirmed_by_cad_audit"
                    if cad_terrain_evidence is not None
                    and cad_terrain_evidence["summary"]["terrain_gate_passed_files"] == 0
                    else "not_present_in_input_bundle"
                ),
                "preferred_formats": ["LandXML", "DWG/DGN", "CSV", "XYZ point cloud when supplied"],
                "role": "preferred continuous local DTM, structural lines and verified vertical datum",
            },
        },
        "source_conflicts": conflicts,
        "render_admission": {
            "status": "passed" if not blockers else "blocked",
            "blocking_gates": blockers,
            "gates": gates,
            "rule": "diagnostic passes may run while blocked; beauty and neural finish may not",
        },
        "deterministic_render_contract": {
            "required_passes": [
                "beauty_linear", "depth_metric", "normal_world", "world_position",
                "semantic_id", "instance_id", "source_confidence", "unknown_mask",
                "protected_geometry_edges",
            ],
            "scene_lock": [
                "source hashes", "coordinate transform hash", "object ids", "XYZ and dimensions",
                "asset hashes and contact planes", "camera matrix", "lighting preset", "color management",
            ],
        },
        "neural_finish_contract": {
            "status": "disabled_until_render_admission_passes",
            "allowed": ["albedo micro-detail", "roughness micro-detail", "bounded illumination refinement"],
            "forbidden": [
                "new objects", "object deletion", "silhouette changes", "road/curb/path displacement",
                "building opening relocation", "vegetation count or crown displacement", "depth changes",
            ],
            "reproducibility": [
                "model id and immutable revision", "seed", "sampler/scheduler", "step count",
                "CFG/control weights", "prompt and negative-prompt hashes", "all conditioning-pass hashes",
            ],
            "validation": "reject candidate when protected pixels, semantic edges, instance ids or depth ordering change",
        },
    }


def report_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# World-source gate: Кустанайская",
        "",
        f"**Статус:** `{manifest['status']}`",
        "",
        "## Проверяемые gates",
        "",
        "| Gate | Статус | Фактическое состояние |",
        "|---|---|---|",
    ]
    for row in manifest["render_admission"]["gates"]:
        actual = json.dumps(row["actual"], ensure_ascii=False, sort_keys=True)
        lines.append(f"| `{row['id']}` | **{row['status']}** | `{actual}` |")
    lines.extend([
        "",
        "## Решение",
        "",
        "Пока хотя бы один gate заблокирован, разрешены только карты покрытия, конфликты и технические passes. ",
        "Beauty-render и нейродорисовка не запускаются: иначе пробелы данных снова станут визуальными артефактами.",
        "",
        "DXF применяется как локальный override внутри собственной подтверждённой маски. Он не обрезает внешний мир. ",
        "Непрерывный внешний каркас приходит из WGS84-источников; точный рельеф и борта — из проверенной инженерной модели.",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-contract", type=Path, default=DEFAULT_AUDIT / "source-contract.json")
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--context", type=Path, default=DEFAULT_CONTEXT)
    parser.add_argument("--alignment", type=Path, default=DEFAULT_LEGACY_AUDIT / "candidate-osm-alignment.json")
    parser.add_argument("--osm-receipt", type=Path, default=DEFAULT_LEGACY_AUDIT / "osm-context-receipt.json")
    parser.add_argument("--overture-dir", type=Path, default=DEFAULT_OVERTURE)
    parser.add_argument("--dem", type=Path, default=DEFAULT_DEM)
    parser.add_argument("--cad-terrain-evidence", type=Path, default=DEFAULT_CAD_TERRAIN_EVIDENCE)
    parser.add_argument("--annotation-terrain", type=Path, default=DEFAULT_ANNOTATION_TERRAIN)
    parser.add_argument(
        "--annotation-terrain-validation", type=Path,
        default=DEFAULT_ANNOTATION_TERRAIN_VALIDATION,
    )
    parser.add_argument("--curb-mesh", type=Path, default=DEFAULT_CURB_MESH)
    parser.add_argument(
        "--curb-mesh-validation", type=Path, default=DEFAULT_CURB_MESH_VALIDATION
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(
        args.source_contract, args.scene, args.context, args.alignment,
        args.osm_receipt, args.overture_dir, args.dem, args.cad_terrain_evidence,
        args.annotation_terrain, args.annotation_terrain_validation,
        args.curb_mesh, args.curb_mesh_validation,
    )
    manifest_path = args.output / "world-source-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    (args.output / "world-source-gate.md").write_text(report_markdown(manifest))
    receipt = {
        "schema": "green-atlas.world-source-manifest-receipt.v1",
        "manifest": source_ref(manifest_path),
        "status": manifest["status"],
        "blocking_gates": manifest["render_admission"]["blocking_gates"],
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
