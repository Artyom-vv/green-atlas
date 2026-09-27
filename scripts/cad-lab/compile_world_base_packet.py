"""Compile a continuous geospatial base without turning estimates into truth.

The packet keeps the external world in one shared local metric frame, clips
external map geometry only where exact DXF surfaces actually exist, and stores
the DEM in its native vertical datum.  It is intentionally a diagnostic world
packet until horizontal and vertical control gates are admitted.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable

import numpy as np
from shapely.affinity import translate
from shapely.geometry import GeometryCollection, box, mapping, shape
from shapely.ops import unary_union, transform as transform_geometry


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / ".runtime/deterministic-render-audit-20260920-expanded"
LEGACY_AUDIT = ROOT / ".runtime/deterministic-render-audit-20260919"
SCENE = ROOT / ".runtime/deterministic-render-pipeline-20260920-expanded/scene-packet.json"
OVERTURE = ROOT / ".runtime/overture-kustanayskaya-20260920"
DEM = OVERTURE / "Copernicus_DSM_COG_10_N55_00_E037_00_DEM.tif"
ROAD_TERRAIN = ROOT / ".runtime/terrain-control-20260918/road-trial/terrain.json"
ANNOTATION_TERRAIN = ROOT / ".runtime/annotation-constrained-terrain-20260920/terrain.json"
SOURCE_CURB_MESH = ROOT / ".runtime/source-curb-mesh-20260920/curb-mesh.json"
OUTPUT = ROOT / ".runtime/world-base-packet-20260920"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_ref(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)}


class Frame:
    def __init__(self, alignment: dict[str, Any], local_origin: list[float]):
        contract = alignment["similarity_transform_row_vector"]
        self.scale = float(contract["scale"])
        self.rotation = np.asarray(contract["rotation"], dtype=float)
        self.translation = np.asarray(contract["translation"], dtype=float)
        self.lon0, self.lat0 = alignment["projection_before_fit"]["reference_lon_lat"]
        self.earth_radius = float(alignment["projection_before_fit"]["earth_radius_m"])
        self.origin = np.asarray(local_origin[:2], dtype=float)

    def wgs84_to_local(self, lon: Any, lat: Any) -> tuple[Any, Any]:
        longitude = np.asarray(lon, dtype=float)
        latitude = np.asarray(lat, dtype=float)
        tangent_x = self.earth_radius * np.radians(longitude - self.lon0) * math.cos(math.radians(self.lat0))
        tangent_y = self.earth_radius * np.radians(latitude - self.lat0)
        tangent = np.column_stack((np.atleast_1d(tangent_x), np.atleast_1d(tangent_y)))
        local = self.scale * tangent @ self.rotation + self.translation - self.origin
        if np.ndim(longitude) == 0:
            return float(local[0, 0]), float(local[0, 1])
        return local[:, 0], local[:, 1]

    def source_to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        tangent = ((np.asarray([x, y]) - self.translation) / self.scale) @ self.rotation.T
        lon = self.lon0 + math.degrees(tangent[0] / (
            self.earth_radius * math.cos(math.radians(self.lat0))
        ))
        lat = self.lat0 + math.degrees(tangent[1] / self.earth_radius)
        return float(lon), float(lat)

    def geometry_to_local(self, geometry: Any) -> Any:
        return transform_geometry(self.wgs84_to_local, geometry)


def rounded(value: Any, digits: int = 6) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [rounded(item, digits) for item in value]
    if isinstance(value, tuple):
        return [rounded(item, digits) for item in value]
    if isinstance(value, dict):
        return {key: rounded(item, digits) for key, item in value.items()}
    return value


def feature_geometry(feature: dict[str, Any]) -> Any:
    geometry = feature.get("geometry")
    return shape(geometry) if geometry else GeometryCollection()


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


def vertical_tie_audit(dem_path: Path, road_terrain_path: Path, frame: Frame) -> dict[str, Any]:
    rasterio = _load_rasterio()
    road = load(road_terrain_path)
    rows = []
    coordinates = []
    for vertex in road["vertices"]:
        lon, lat = frame.source_to_wgs84(vertex["xyz"][0], vertex["xyz"][1])
        coordinates.append((lon, lat))
        rows.append({
            "picket": vertex["picket"],
            "wgs84": [lon, lat],
            "project_height_m": float(vertex["xyz"][2]),
        })
    with rasterio.open(dem_path) as dataset:
        samples = [float(value[0]) for value in dataset.sample(coordinates)]
    offsets = []
    for row, sample in zip(rows, samples):
        row["dem_height_egm2008_m"] = sample
        row["project_minus_dem_m"] = row["project_height_m"] - sample
        offsets.append(row["project_minus_dem_m"])
    median = float(np.median(offsets))
    residuals = np.asarray(offsets) - median
    return {
        "status": "candidate_rejected_for_local_truth",
        "method": "median project road-control height minus nearest Copernicus DSM sample",
        "candidate_vertical_offset_m": round(median, 6),
        "residual_stddev_m": round(float(np.std(residuals)), 6),
        "max_abs_residual_m": round(float(np.max(np.abs(residuals))), 6),
        "samples": rounded(rows),
        "reason": "30 m DSM cells include roofs/vegetation and cannot establish curb or tree ground Z",
    }


def normalize_project_surfaces(
    path: Path, local_origin: list[float]
) -> tuple[list[dict[str, Any]], Any]:
    output = []
    geometries = []
    for feature in load(path).get("features", []):
        geometry = translate(
            feature_geometry(feature), xoff=-local_origin[0], yoff=-local_origin[1]
        )
        if geometry.is_empty:
            continue
        properties = feature.get("properties", {})
        output.append({
            "type": "Feature",
            "geometry": rounded(mapping(geometry)),
            "properties": {
                "id": f"dxf:hatch:{properties.get('source_handle')}",
                "kind": "project_surface_override",
                "semantic_class": properties.get("class"),
                "layer": properties.get("source_layer"),
                "horizontal_status": "authored_DXF_inside_explicit_feature_boundary",
                "vertical_status": "unknown_in_base_packet",
                "authority_rank": 20,
                "render_eligible": False,
                "render_gate": "blocked_missing_admitted_surface_z",
            },
        })
        geometries.append(geometry)
    return output, unary_union(geometries)


def normalize_annotation_terrain(
    path: Path, local_origin: list[float]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    terrain = load(path)
    vertices = terrain["vertices"]
    features = []
    for triangle_index, (face, semantic_class) in enumerate(zip(
        terrain["triangles"], terrain["triangle_classes"]
    )):
        coordinates = []
        support_ids = set()
        for vertex_index in face:
            vertex = vertices[vertex_index]
            x, y, z = vertex["xyz"]
            coordinates.append([
                round(float(x)-local_origin[0], 8),
                round(float(y)-local_origin[1], 8),
                round(float(z), 6),
            ])
            support_ids.update(vertex.get("support_ids", []))
        coordinates.append(coordinates[0])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [coordinates]},
            "properties": {
                "id": f"annotation-terrain:triangle:{triangle_index:06d}",
                "kind": "terrain_triangle",
                "semantic_class": semantic_class,
                "height_status": "source_annotation_class_constrained",
                "vertical_reference": "Moscow height system declared in project documents",
                "support_ids": sorted(support_ids),
                "authority_rank": 25,
                "render_eligible": False,
                "render_gate": "blocked_annotation_role_review_and_incomplete_coverage",
            },
        })
    return features, {
        "status": terrain["status"],
        "triangles": len(features),
        "vertices": len(vertices),
        "coverage": terrain["coverage"],
        "height_range_m": terrain["height_range_m"],
        "z_storage": "absolute source height; renderer must record any vertical render origin",
    }


def normalize_source_curb_mesh(
    path: Path, local_origin: list[float]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mesh = load(path)
    vertices = mesh["vertices"]
    features = []
    for face_index, face in enumerate(mesh["faces"]):
        rows = []
        chain_ids = set()
        for vertex_index in face:
            vertex = vertices[vertex_index]
            x, y, z = vertex["xyz"]
            rows.append([
                round(float(x)-local_origin[0], 8),
                round(float(y)-local_origin[1], 8),
                round(float(z), 6),
            ])
            chain_ids.add(vertex["chain_id"])
        features.append({
            "type": "Feature",
            # GeoJSON polygons are evaluated in XY and cannot represent a
            # vertical quad.  The lower 3D breakline stays valid GeoJSON while
            # the paired upper endpoints reconstruct the exact render face.
            "geometry": {"type": "LineString", "coordinates": [rows[0], rows[1]]},
            "properties": {
                "id": f"source-curb:riser-segment:{face_index:06d}",
                "kind": "curb_riser_segment",
                "semantic_class": "curb",
                "chain_ids": sorted(chain_ids),
                "upper_endpoints_local_xyz": [rows[3], rows[2]],
                "mesh_reconstruction": "quad=[lower_start,lower_end,upper_end,upper_start]",
                "height_status": "interpolated_between_source_annotation_pairs",
                "vertical_reference": "Moscow height system declared in project documents",
                "authority_rank": 24,
                "render_eligible": False,
                "render_gate": "blocked_curb_pair_role_review",
            },
        })
    return features, {
        "status": mesh["status"],
        "faces": len(features),
        "vertices": len(vertices),
        "covered_curb_length_m": mesh["quality"]["covered_curb_length_m"],
        "accepted_pair_controls": mesh["quality"]["accepted_pair_controls"],
        "road_width_inferred": False,
        "curb_cap_width_inferred": False,
    }


def building_height(properties: dict[str, Any]) -> tuple[float | None, str]:
    if properties.get("height") is not None:
        return float(properties["height"]), "explicit_height"
    if properties.get("num_floors") is not None:
        return float(properties["num_floors"]) * 3.0, "num_floors_times_3m_render_estimate"
    return None, "unknown"


def normalize_overture(
    directory: Path, frame: Frame, authority: Any, context_bbox_local: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    features = []
    building_path = directory / "building.geojson"
    buildings = load(building_path).get("features", [])
    buildings_with_vertical = 0
    for feature in buildings:
        geometry = frame.geometry_to_local(feature_geometry(feature)).intersection(context_bbox_local)
        if geometry.is_empty:
            continue
        height, height_status = building_height(feature.get("properties", {}))
        buildings_with_vertical += int(height is not None)
        features.append({
            "type": "Feature",
            "geometry": rounded(mapping(geometry)),
            "properties": {
                "id": f"overture:building:{feature.get('id')}",
                "kind": "building",
                "height_m": height,
                "height_status": height_status,
                "horizontal_status": "cartographic_candidate_shared_transform",
                "authority_rank": 40,
                "render_eligible": False,
                "render_gate": "blocked_shared_horizontal_transform" if height is not None else "blocked_height_and_transform",
                "sources": feature.get("properties", {}).get("sources"),
            },
        })

    segment_path = directory / "segment.geojson"
    segments = load(segment_path).get("features", [])
    original_length = kept_length = suppressed_length = 0.0
    kept_segments = explicit_width_segments = residual_conflicts = 0
    for feature in segments:
        properties = feature.get("properties", {})
        geometry = frame.geometry_to_local(feature_geometry(feature)).intersection(context_bbox_local)
        if geometry.is_empty:
            continue
        original_length += geometry.length
        # The project is authoritative only where an actual authored surface
        # exists.  A rectangular DXF crop never erases the outside world.
        clipped = geometry.difference(authority.buffer(0.02))
        suppressed_length += geometry.length - clipped.length
        if clipped.is_empty:
            continue
        kept_length += clipped.length
        kept_segments += 1
        width_rules = properties.get("width_rules") or []
        explicit_width_segments += int(bool(width_rules))
        residual_conflicts += int(clipped.intersection(authority).length > 0.001)
        features.append({
            "type": "Feature",
            "geometry": rounded(mapping(clipped)),
            "properties": {
                "id": f"overture:segment:{feature.get('id')}",
                "kind": "transportation_centerline",
                "subtype": properties.get("subtype"),
                "class": properties.get("class"),
                "names": properties.get("names"),
                "road_surface": properties.get("road_surface"),
                "width_rules": width_rules,
                "width_status": "explicit_rule" if width_rules else "unknown_no_surface_mesh",
                "horizontal_status": "cartographic_candidate_shared_transform",
                "authority_rank": 40,
                "render_eligible": False,
                "render_gate": "centerline_only_not_physical_surface",
                "sources": properties.get("sources"),
            },
        })
    return features, {
        "buildings": len(buildings),
        "buildings_with_vertical": buildings_with_vertical,
        "segments": len(segments),
        "kept_segment_parts": kept_segments,
        "segments_with_explicit_width": explicit_width_segments,
        "original_centerline_length_m": round(original_length, 3),
        "kept_centerline_length_m": round(kept_length, 3),
        "suppressed_inside_DXF_authority_m": round(suppressed_length, 3),
        "residual_authority_intersections": residual_conflicts,
    }


def make_svg(features: Iterable[dict[str, Any]], output: Path) -> None:
    rows = list(features)
    raw_geometries = [(feature, feature_geometry(feature)) for feature in rows]
    project_geometry = unary_union([
        geometry for feature, geometry in raw_geometries
        if feature["properties"].get("kind") == "project_surface_override"
    ])
    focus = project_geometry.envelope.buffer(180.0)
    geometries = [
        (feature, geometry.intersection(focus))
        for feature, geometry in raw_geometries
        if not geometry.is_empty and geometry.intersects(focus)
    ]
    bounds = focus.bounds
    min_x, min_y, max_x, max_y = bounds
    width, height, margin = 1400, 1100, 45
    scale = min((width - 2 * margin) / (max_x - min_x), (height - 2 * margin) / (max_y - min_y))

    def project(x: float, y: float) -> tuple[float, float]:
        return margin + (x - min_x) * scale, height - margin - (y - min_y) * scale

    def line_path(coordinates: Any, close: bool = False) -> str:
        points = [project(float(x), float(y)) for x, y, *_ in coordinates]
        result = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
        return result + (" Z" if close else "")

    def paths(geometry: Any) -> list[str]:
        if geometry.geom_type == "LineString":
            return [line_path(geometry.coords)]
        if geometry.geom_type == "MultiLineString":
            return [line_path(part.coords) for part in geometry.geoms]
        if geometry.geom_type == "Polygon":
            return [line_path(geometry.exterior.coords, True)]
        if geometry.geom_type == "MultiPolygon":
            return [line_path(part.exterior.coords, True) for part in geometry.geoms]
        return []

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#eef1f2"/>',
        '<g stroke-linejoin="round" stroke-linecap="round">',
    ]
    # Draw external context first, exact project overrides last.
    for feature, geometry in geometries:
        properties = feature["properties"]
        kind = properties.get("kind")
        if kind == "building":
            for path in paths(geometry):
                svg.append(f'<path d="{path}" fill="#d9dddc" stroke="#7a8587" stroke-width="0.9"/>')
        elif kind == "transportation_centerline":
            color = "#3e6570" if properties.get("subtype") == "road" else "#9b704b"
            dash = "" if properties.get("width_status") == "explicit_rule" else ' stroke-dasharray="6 4"'
            for path in paths(geometry):
                svg.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="3.5"{dash}/>')
    colors = {"road": "#555b60", "sidewalk": "#c4bbb0", "lawn": "#91ad70", "special_surface": "#b99373"}
    for feature, geometry in geometries:
        properties = feature["properties"]
        if properties.get("kind") != "project_surface_override":
            continue
        color = colors.get(properties.get("semantic_class"), "#b9b1a2")
        for path in paths(geometry):
            svg.append(f'<path d="{path}" fill="{color}" stroke="#333c3d" stroke-width="0.45"/>')
    svg.extend([
        '</g>',
        '<g font-family="Arial, sans-serif">',
        '<rect x="28" y="25" width="780" height="92" rx="8" fill="#ffffff" opacity="0.94"/>',
        '<text x="48" y="57" font-size="22" font-weight="700" fill="#172426">Непрерывный WGS84-каркас + точные DXF overrides</text>',
        '<text x="48" y="84" font-size="15" fill="#4c5b5d">Пунктир — Overture без физической ширины. Цветные полигоны — только авторские HATCH.</text>',
        '<text x="48" y="105" font-size="14" fill="#9a3c31">Diagnostic world base: horizontal/vertical survey gates ещё не пройдены.</text>',
        '</g></svg>',
    ])
    output.write_text("\n".join(svg))


def build_packet(
    manifest_path: Path,
    alignment_path: Path,
    scene_path: Path,
    surfaces_path: Path,
    overture_dir: Path,
    dem_path: Path,
    road_terrain_path: Path,
    annotation_terrain_path: Path,
    curb_mesh_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = load(manifest_path)
    alignment = load(alignment_path)
    scene = load(scene_path)
    origin = scene["coordinates"]["local_origin_xyz"]
    frame = Frame(alignment, origin)
    project_features, authority = normalize_project_surfaces(surfaces_path, origin)
    external_bbox_wgs84 = manifest["site"]["external_context_bbox_wgs84"]
    corners = [
        frame.wgs84_to_local(external_bbox_wgs84[0], external_bbox_wgs84[1]),
        frame.wgs84_to_local(external_bbox_wgs84[2], external_bbox_wgs84[3]),
    ]
    context_bbox_local = box(
        min(corners[0][0], corners[1][0]), min(corners[0][1], corners[1][1]),
        max(corners[0][0], corners[1][0]), max(corners[0][1], corners[1][1]),
    )
    overture_features, fusion = normalize_overture(
        overture_dir, frame, authority, context_bbox_local
    )
    terrain_features, terrain_candidate = normalize_annotation_terrain(
        annotation_terrain_path, origin
    )
    curb_features, curb_candidate = normalize_source_curb_mesh(curb_mesh_path, origin)
    features = overture_features + project_features + terrain_features + curb_features
    vertical_tie = vertical_tie_audit(dem_path, road_terrain_path, frame)
    remaining_blockers = list(manifest["render_admission"]["blocking_gates"])
    if fusion["residual_authority_intersections"] == 0:
        remaining_blockers = [
            blocker for blocker in remaining_blockers
            if blocker != "source_priority_conflicts_resolved"
        ]
    packet = {
        "schema": "green-atlas.world-base-packet.v1",
        "status": "diagnostic_world_base_not_beauty",
        "sources": {
            "world_manifest": source_ref(manifest_path),
            "alignment": source_ref(alignment_path),
            "project_scene": source_ref(scene_path),
            "authored_surfaces": source_ref(surfaces_path),
            "overture_buildings": source_ref(overture_dir / "building.geojson"),
            "overture_segments": source_ref(overture_dir / "segment.geojson"),
            "copernicus_dem": source_ref(dem_path),
            "road_height_controls": source_ref(road_terrain_path),
            "annotation_constrained_terrain": source_ref(annotation_terrain_path),
            "source_curb_mesh": source_ref(curb_mesh_path),
        },
        "coordinates": {
            "feature_frame": "local metres derived from candidate WGS84-to-DXF shared similarity",
            "local_origin_dxf_xyz": origin,
            "horizontal_status": alignment["status"],
            "vertical_frames": {
                "project": "Moscow height system declared; machine transform unresolved",
                "external_dem": "EGM2008",
                "annotation_terrain": "Moscow height system declared; source annotations need role review",
            },
        },
        "authority_fusion": {
            "rule": "external geometry is clipped only by the union of actual authored DXF surfaces",
            "rectangular_DXF_scope_is_not_an_authority_mask": True,
            "project_surface_features": len(project_features),
            "project_authority_area_m2": round(authority.area, 6),
            **fusion,
        },
        "vertical_tie_audit": vertical_tie,
        "terrain_candidate": terrain_candidate,
        "curb_candidate": curb_candidate,
        "feature_counts": dict(sorted(Counter(
            feature["properties"]["kind"] for feature in features
        ).items())),
        "render_policy": {
            "admitted": False,
            "remaining_blocking_gates": remaining_blockers,
            "gates_resolved_by_compilation": [
                "source_priority_conflicts_resolved"
            ] if fusion["residual_authority_intersections"] == 0 else [],
            "allowed_outputs": ["coverage map", "conflict map", "depth/semantic diagnostic"],
            "forbidden_outputs": ["beauty render", "neural finish"],
        },
    }
    return packet, features


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / ".runtime/world-source-manifest-20260920/world-source-manifest.json")
    parser.add_argument("--alignment", type=Path, default=LEGACY_AUDIT / "candidate-osm-alignment.json")
    parser.add_argument("--scene", type=Path, default=SCENE)
    parser.add_argument("--surfaces", type=Path, default=AUDIT / "authored-surfaces.geojson")
    parser.add_argument("--overture-dir", type=Path, default=OVERTURE)
    parser.add_argument("--dem", type=Path, default=DEM)
    parser.add_argument("--road-terrain", type=Path, default=ROAD_TERRAIN)
    parser.add_argument("--annotation-terrain", type=Path, default=ANNOTATION_TERRAIN)
    parser.add_argument("--curb-mesh", type=Path, default=SOURCE_CURB_MESH)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    packet, features = build_packet(
        args.manifest, args.alignment, args.scene, args.surfaces,
        args.overture_dir, args.dem, args.road_terrain, args.annotation_terrain,
        args.curb_mesh,
    )
    packet_path = args.output / "world-base-packet.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    geojson_path = args.output / "world-base-local.geojson"
    geojson_path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2
    ) + "\n")
    svg_path = args.output / "world-base-overview.svg"
    make_svg(features, svg_path)
    png_path = args.output / "world-base-overview.png"
    converter = shutil.which("rsvg-convert")
    if converter:
        subprocess.run([converter, "-o", str(png_path), str(svg_path)], check=True)
    receipt = {
        "schema": "green-atlas.world-base-packet-receipt.v1",
        "status": packet["status"],
        "packet": source_ref(packet_path),
        "geojson": source_ref(geojson_path),
        "overview": source_ref(svg_path),
        "overview_png": source_ref(png_path) if png_path.exists() else None,
        "authority_fusion": packet["authority_fusion"],
        "vertical_tie": {
            key: packet["vertical_tie_audit"][key]
            for key in ("status", "candidate_vertical_offset_m", "residual_stddev_m", "max_abs_residual_m")
        },
    }
    (args.output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
