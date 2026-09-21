"""Build a source-only surface contract for the Kustanayskaya render pilot.

The output is deliberately not a render.  It proves which pixels may later be
assigned to roads, pavements, and lawns without tracing or generative filling.
Every polygon keeps its DXF file hash, entity handle, and source layer.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

import ezdxf
from ezdxf.path import make_path
from shapely.geometry import Polygon, box, mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.dxf_import.adapters import _hatch_polygon_geometry  # noqa: E402
from app.dxf_import.polygons import hatch_geometry  # noqa: E402


DXF_ROOT = ROOT / ".runtime/kustanayskaya-mac-ready-20260917/dxf"
OUT = ROOT / ".runtime/deterministic-render-audit-20260919"
ROI = (15840.0, -4980.0, 15893.0, -4937.0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_source(name: str) -> Path:
    matches = list(DXF_ROOT.rglob(name))
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {name}, found {len(matches)}")
    return matches[0]


def surface_class(layer: str) -> str:
    """Classify only the authored primary surface named before `за ...`."""
    primary = layer.split(" за ", 1)[0]
    if "Тип0_Газон" in primary:
        return "lawn"
    if "Тип2_ПЧ" in primary or "Тип4_ПЧ" in primary:
        return "road"
    if any(token in primary for token in ("Тип5_Тротуар", "Тип6_Тротуар", "Тип7_Тротуар")):
        return "sidewalk"
    if "Спец. Покрытие" in primary:
        return "special_surface"
    if "Лестница" in primary:
        return "stairs"
    return "unclassified"


def has_geodata(document) -> bool:
    return any(entity.dxftype() == "GEODATA" for entity in document.objects)


def work_boundary(document) -> Polygon:
    candidates = []
    for entity in document.modelspace().query("LWPOLYLINE"):
        if entity.dxf.layer != "ДВ_ГП_П_Граница работ" or not entity.closed:
            continue
        points = [(point.x, point.y) for point in make_path(entity).flattening(0.02)]
        if points and points[0] != points[-1]:
            points.append(points[0])
        polygon = Polygon(points)
        if polygon.is_valid and polygon.area > 0:
            candidates.append((polygon.area, entity.dxf.handle, polygon))
    if not candidates:
        raise RuntimeError("No valid closed work boundary found")
    return max(candidates)[2]


def render_preview(unions, roi, output: Path):
    width, height, margin = 1500, 1120, 80
    x0, y0, x1, y1 = roi.bounds
    scale = min((width - 2 * margin) / (x1 - x0), (height - 2 * margin) / (y1 - y0))

    def project(x, y):
        return (round(margin + (x - x0) * scale), round(height - margin - (y - y0) * scale))

    colors = {
        "road": "#74787b",
        "sidewalk": "#c9c4b8",
        "lawn": "#8eb881",
        "special_surface": "#c7a977",
        "stairs": "#aaa59a",
        "unclassified": "#d75d4f",
    }
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g font-family="Arial, sans-serif">',
        '<text x="80" y="30" font-size="22" font-weight="bold" fill="#172421">Кустанайская · точные проектные HATCH в контрольном окне 53 × 43 м</text>',
        '<text x="80" y="56" font-size="16" fill="#485651">Белое = неизвестно. Никакого фонового заполнения и нейрогенерации.</text>',
    ]

    def path_data(ring):
        points = [project(*point) for point in ring.coords]
        return "M" + " L".join(f"{x},{y}" for x, y in points) + " Z"

    # Draw authored classes only. White remains explicitly unknown. Even/odd
    # filling preserves any holes in source HATCH boundaries.
    for kind in ("road", "sidewalk", "special_surface", "stairs", "lawn", "unclassified"):
        geometry = unions.get(kind)
        if geometry is not None and not geometry.is_empty:
            polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
            for polygon in polygons:
                data = path_data(polygon.exterior) + " " + " ".join(path_data(ring) for ring in polygon.interiors)
                svg.append(f'<path d="{data}" fill="{colors[kind]}" fill-rule="evenodd" stroke="#35413e" stroke-width="2"/>')
    left, top = project(x0, y1)
    right, bottom = project(x1, y0)
    svg.append(f'<rect x="{left}" y="{top}" width="{right-left}" height="{bottom-top}" fill="none" stroke="#172421" stroke-width="3"/>')
    legend_x = 85
    for kind in ("road", "sidewalk", "lawn", "special_surface", "stairs", "unclassified"):
        svg.append(f'<rect x="{legend_x}" y="{height-46}" width="24" height="24" fill="{colors[kind]}" stroke="#35413e"/>')
        svg.append(f'<text x="{legend_x+31}" y="{height-27}" font-size="14" fill="#172421">{kind}</text>')
        legend_x += 205
    svg.extend(["</g>", "</svg>"])
    output.write_text("\n".join(svg))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    project_path = find_source("03_10004141_Проектные решения.dxf")
    topo_path = find_source("00.1_10004141_Топография.dxf")
    boundary_path = find_source("01_10004141_Границы работ.dxf")
    project = ezdxf.readfile(project_path)
    topo = ezdxf.readfile(topo_path)
    boundary_doc = ezdxf.readfile(boundary_path)
    main_boundary = work_boundary(boundary_doc)
    roi = box(*ROI)

    features = []
    rejected = []
    by_kind = defaultdict(list)
    layer_counts = Counter()
    layer_areas = defaultdict(float)
    for entity in project.modelspace().query("HATCH"):
        geometry = hatch_geometry(entity, 1.0, _hatch_polygon_geometry)
        if geometry is None:
            rejected.append({"handle": entity.dxf.handle, "layer": entity.dxf.layer})
            continue
        authored = shape(geometry)
        clipped = authored.intersection(roi)
        if clipped.is_empty:
            continue
        kind = surface_class(entity.dxf.layer)
        by_kind[kind].append(clipped)
        layer_counts[entity.dxf.layer] += 1
        layer_areas[entity.dxf.layer] += clipped.area
        features.append({
            "type": "Feature",
            "geometry": mapping(clipped),
            "properties": {
                "class": kind,
                "source_file": str(project_path),
                "source_sha256": sha256(project_path),
                "source_handle": entity.dxf.handle,
                "source_layer": entity.dxf.layer,
                "status": "authored_project_hatch",
            },
        })

    unions = {kind: unary_union(parts) for kind, parts in by_kind.items()}
    overlaps = []
    for left, right in combinations(sorted(unions), 2):
        area = unions[left].intersection(unions[right]).area
        if area > 1e-8:
            overlaps.append({"classes": [left, right], "area_m2": area})
    covered = unary_union(list(unions.values())) if unions else Polygon()
    unknown = roi.difference(covered)

    geojson = {
        "type": "FeatureCollection",
        "bbox": list(ROI),
        "features": features,
    }
    (OUT / "authored-surfaces.geojson").write_text(json.dumps(geojson, ensure_ascii=False, indent=2))
    contract = {
        "schema": "green-atlas.render-source-contract.v1",
        "scope": {"name": "kustanayskaya-control-53x43m", "bbox_local_m": list(ROI), "area_m2": roi.area},
        "source_files": [
            {"role": "topography", "path": str(topo_path), "sha256": sha256(topo_path), "insunits": topo.header.get("$INSUNITS"), "has_geodata": has_geodata(topo)},
            {"role": "project_surfaces", "path": str(project_path), "sha256": sha256(project_path), "insunits": project.header.get("$INSUNITS"), "has_geodata": has_geodata(project)},
            {"role": "work_boundary", "path": str(boundary_path), "sha256": sha256(boundary_path), "insunits": boundary_doc.header.get("$INSUNITS"), "has_geodata": has_geodata(boundary_doc)},
        ],
        "coordinate_contract": {
            "horizontal_units": "metres_from_DXF_INSUNITS_6",
            "declared_in_project_documents": "Moscow coordinate system",
            "machine_readable_crs": None,
            "external_map_alignment_status": "blocked_until_control_point_transform_is_verified",
            "render_local_origin_required": True,
        },
        "work_boundary": {"area_m2": main_boundary.area, "bbox_local_m": list(main_boundary.bounds)},
        "surface_counts": dict(Counter(feature["properties"]["class"] for feature in features)),
        "surface_union_area_m2": {kind: geometry.area for kind, geometry in sorted(unions.items())},
        "surface_layers": [
            {"layer": layer, "features": layer_counts[layer], "area_in_scope_m2": layer_areas[layer]}
            for layer in sorted(layer_counts)
        ],
        "covered_area_m2": covered.area,
        "covered_ratio": covered.area / roi.area,
        "unknown_area_m2": unknown.area,
        "unknown_policy": "preserve_as_unknown; do not assign a material or generate objects",
        "cross_class_overlaps": overlaps,
        "rejected_project_hatches_total": len(rejected),
        "rejected_project_hatches": rejected,
        "terrain_status": {
            "source_spot_labels": 3520,
            "source_piket_symbols": 2436,
            "machine_readable_ground_z": False,
            "vertical_reference_from_project_documents": "Moscow height system",
            "policy": "build reviewed constrained TIN from classified spot elevations and breaklines; external 30 m DEM is context only",
        },
        "render_gate": {
            "geometry_may_enter_scene_only_if": [
                "source feature and transform are recorded",
                "semantic class is explicit",
                "elevation is confirmed or visibly marked estimated",
            ],
            "generative_additions_allowed": False,
        },
    }
    (OUT / "source-contract.json").write_text(json.dumps(contract, ensure_ascii=False, indent=2))
    render_preview(unions, roi, OUT / "authored-surfaces.svg")
    print(json.dumps({
        "features": len(features),
        "classes": contract["surface_counts"],
        "covered_ratio": contract["covered_ratio"],
        "unknown_area_m2": contract["unknown_area_m2"],
        "overlaps": overlaps,
        "output": str(OUT),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
