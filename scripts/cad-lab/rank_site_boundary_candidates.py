"""Rank DXF layers that can form a usable project boundary.

This is a read-only semantic diagnostic. It does not approve a layer mapping
or repair source geometry. The report shows which closed surfaces remain wide
enough for planting after inward offsets, so a thin drafting stroke cannot be
mistaken for the project territory merely because its name contains
"boundary".
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from app.dxf_import.adapters import EzdxfReader
from shapely import union_all
from shapely.errors import GeometryTypeError, GEOSException
from shapely.geometry import GeometryCollection, mapping, shape
from shapely.ops import polygonize, unary_union

BOUNDARY_WORDS = (
    "границ",
    "контур",
    "участ",
    "территор",
    "заказ",
    "улиц",
    "красн",
)


def _polygon_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        return [part for item in geometry.geoms for part in _polygon_parts(item)]
    return []


def _round_bounds(geometry):
    if geometry.is_empty:
        return None
    return [round(value, 3) for value in geometry.bounds]


def run(source: Path, output: Path) -> None:
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    imported = EzdxfReader().read_prepared_file(source)
    layer_by_name = {layer.source_name: layer for layer in imported.layers}
    by_layer = defaultdict(list)
    invalid = defaultdict(int)

    for feature in imported.geometry.feature_collection.get("features", []):
        properties = feature.get("properties", {})
        source_layer = str(properties.get("source_layer", ""))
        if not source_layer:
            continue
        try:
            geometry = shape(feature["geometry"])
        except (GeometryTypeError, KeyError, TypeError, ValueError):
            invalid[source_layer] += 1
            continue
        if geometry.is_empty or not geometry.is_valid:
            invalid[source_layer] += 1
            continue
        by_layer[source_layer].append(geometry)

    candidates = []
    preview_features = []
    for source_layer, geometries in by_layer.items():
        layer = layer_by_name[source_layer]
        name_matches = any(word in source_layer.casefold() for word in BOUNDARY_WORDS)
        if layer.suggested_kind.value != "site_border" and not name_matches:
            continue

        polygons = [part for geometry in geometries for part in _polygon_parts(geometry)]
        linework = [
            geometry
            for geometry in geometries
            if geometry.geom_type in {"LineString", "MultiLineString"}
        ]
        raw_polygonized = list(polygonize(linework)) if linework else []
        polygonized = [
            polygon
            for polygon in raw_polygonized
            if polygon.is_valid and not polygon.is_empty
        ]
        invalid_polygonized_count = len(raw_polygonized) - len(polygonized)
        surfaces = [*polygons, *polygonized]
        union_method = "exact"
        if surfaces:
            try:
                unioned = unary_union(surfaces).buffer(0)
            except GEOSException:
                # A diagnostic snap is allowed only to measure the scale of a
                # candidate. It does not flow back into source geometry or a
                # project mapping, and the report exposes that exact union
                # failed instead of silently treating the snapped result as
                # authoritative CAD geometry.
                unioned = union_all(surfaces, grid_size=1e-9).buffer(0)
                union_method = "diagnostic_grid_1e-9_after_exact_union_failed"
        else:
            unioned = GeometryCollection()
        inset_05 = unioned.buffer(-0.5) if not unioned.is_empty else unioned
        inset_15 = unioned.buffer(-1.5) if not unioned.is_empty else unioned
        inset_30 = unioned.buffer(-3.0) if not unioned.is_empty else unioned
        area = unioned.area
        hull_area = unioned.convex_hull.area if not unioned.is_empty else 0.0
        candidate = {
            "source_layer": source_layer,
            "suggested_kind": layer.suggested_kind.value,
            "source_object_count": layer.object_count,
            "valid_geometry_count": len(geometries),
            "invalid_geometry_count": invalid[source_layer],
            "union_method": union_method,
            "source_polygon_count": len(polygons),
            "polygonized_ring_count": len(polygonized),
            "invalid_polygonized_ring_count": invalid_polygonized_count,
            "surface_component_count": len(_polygon_parts(unioned)),
            "area_m2": round(area, 3),
            "inset_0_5m_area_m2": round(inset_05.area, 3),
            "inset_1_5m_area_m2": round(inset_15.area, 3),
            "inset_3_0m_area_m2": round(inset_30.area, 3),
            "inset_1_5m_ratio": round(inset_15.area / area, 6) if area else 0.0,
            "convex_fill_ratio": round(area / hull_area, 6) if hull_area else 0.0,
            "bounds": _round_bounds(unioned),
        }
        candidates.append(candidate)
        if not unioned.is_empty:
            preview_features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "source_layer": source_layer,
                        "suggested_kind": layer.suggested_kind.value,
                        "area_m2": candidate["area_m2"],
                        "inset_1_5m_area_m2": candidate["inset_1_5m_area_m2"],
                    },
                    "geometry": mapping(unioned),
                }
            )

    candidates.sort(
        key=lambda item: (
            item["inset_1_5m_area_m2"],
            item["area_m2"],
            -item["surface_component_count"],
        ),
        reverse=True,
    )
    report = {
        "scope": "read-only boundary candidate diagnostic; no mapping approved",
        "source": str(source),
        "source_sha256": source_sha,
        "source_unchanged": source_sha == hashlib.sha256(source.read_bytes()).hexdigest(),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    output.with_suffix(".geojson").write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": preview_features},
            ensure_ascii=False,
        )
    )
    print(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "top": candidates[:10],
                "source_unchanged": report["source_unchanged"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    run(arguments.source, arguments.output)
