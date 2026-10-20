"""Native curb volumes for the fast viewer, using the full render compiler.

Only confirmed display roles and accepted capture properties are admitted.
Source XY is translated to the scene frame; no CAD read, repair, surface
triangulation, network request or calculation geometry is created here.
"""

from collections import defaultdict
from math import hypot
from typing import Any

from shapely import union_all
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, shape

from app.projects.contracts import Project
from app.rendering.curb_geometry import curb_envelopes
from app.rendering.curb_semantics import (
    projected_curb_line,
    suppress_native_curb_graphics,
)
from app.rendering.environment import _curb_aliases
from app.rendering.environment_inputs import environment_inputs
from app.scene.context import _context_base_elevation, _translate_geojson
from app.scene.contracts import SceneContextFeature, SceneSnapshot


def native_scene_curbs(
    project: Project, features: list[dict[str, Any]], origin_xy: tuple[float, float]
) -> dict[str, Any]:
    """Compile source-positioned lines, retaining genuine polygon footprints.

    Dimensions are the same illustrative 0.15/0.18 m used by the full render,
    never a measurement or a planting constraint. Whole native block strokes
    go through the existing graphic-symbol proof before volume construction.
    """
    layers = defaultdict(list)
    for layer in project.layers:
        layers[layer.source_name].append(layer)
    admitted: list[dict[str, Any]] = []
    context: list[SceneContextFeature] = []
    for index, raw in enumerate(features):
        prop = raw.get("properties", {})
        candidates = layers.get(prop.get("source_layer"), [])
        if len(candidates) != 1:
            continue
        layer = candidates[0]
        if (
            layer.mapping_confirmed is not True
            or layer.category not in {"curb", "lawn"}
            or layer.mapped_kind != prop.get("kind")
            or layer.mapped_kind not in {"road", "lawn"}
        ):
            continue
        raw_geometry = raw.get("geometry", {})
        if raw_geometry.get("type") not in {
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        }:
            continue
        identifier = str(
            raw.get("id") or prop.get("source_handle") or f"context-{index}"
        )
        admitted.append(raw)
        context.append(
            SceneContextFeature(
                feature_id=identifier,
                kind=prop["kind"],
                geometry=_translate_geojson(raw_geometry, *origin_xy),
                source_layer=prop.get("source_layer"),
                source_entity_type=prop.get("entity_type"),
                source_handle=prop.get("source_handle"),
                base_elevation_m=_context_base_elevation(prop),
            )
        )
    graph = project.geometry or project.source_geometry
    if not context or graph is None:
        return {
            "meshes": [],
            "curb_polygon_ids": set(),
            "suppressed_source_ids": [],
            "evidence": {},
        }
    # One small metadata projection, not a full SceneSnapshot/mesh of the CAD.
    selected_graph = graph.model_copy(
        update={
            "feature_collection": {"type": "FeatureCollection", "features": admitted}
        }
    )
    selected_project = project.model_copy(
        update={
            "geometry": selected_graph if project.geometry is not None else None,
            "source_geometry": selected_graph if project.geometry is None else None,
        }
    )
    snapshot = SceneSnapshot(
        project_id=project.id,
        geometry_version=project.geometry_version,
        source_sha256=project.source_file.content_sha256
        if project.source_file
        else None,
        plan_version=project.plan.version if project.plan else 1,
        horizon_year=0,
        coordinate_origin=list(origin_xy),
        note="Accepted native display paths",
        context_features=context,
    )
    inputs = environment_inputs(selected_project, snapshot)
    evidence = {row["feature_id"]: row for row in inputs["role_evidence"]}
    lawns_by_base = defaultdict(list)
    for row in context:
        if inputs["roles"].get(row.feature_id) == "lawn":
            lawn = shape(row.geometry)
            if (
                isinstance(lawn, (Polygon, MultiPolygon))
                and lawn.is_valid
                and not lawn.is_empty
            ):
                lawns_by_base[round(row.base_elevation_m or 0, 9)].append(lawn)
    lawn_masks = {base: union_all(parts) for base, parts in lawns_by_base.items()}
    rows: list[dict[str, Any]] = []
    source_lines: dict[str, list[list[list[float]]]] = {}
    native_lines: dict[str, list[list[list[float]]]] = {}
    polygon_ids: set[str] = set()
    projected: list[dict[str, Any]] = []
    keys = set()
    for feature in context:
        if inputs["roles"].get(feature.feature_id) != "curb":
            continue
        detail = evidence[feature.feature_id]
        base = feature.base_elevation_m or 0.0
        native_lines[feature.feature_id] = (
            [detail["source_native_curve_scene_xyz_m"]]
            if "source_native_curve_scene_xyz_m" in detail
            else []
        )
        native_xyz = native_lines[feature.feature_id]
        if native_xyz:
            # Already checked for finite XYZ by environment_inputs. Avoid
            # another GEOS/coordinate roundtrip for every native symbol stroke.
            if len({tuple(point[:2]) for point in native_xyz[0]}) < 2:
                raise ValueError(
                    "Невалидная геометрия просмотра бордюра: " + feature.feature_id
                )
            chains = native_xyz
            geometry = None
        else:
            geometry = shape(feature.geometry)
            if not geometry.is_valid or geometry.is_empty:
                raise ValueError(
                    "Невалидная геометрия просмотра бордюра: " + feature.feature_id
                )
        if isinstance(geometry, (Polygon, MultiPolygon)):
            adjacent_lawn = lawn_masks.get(round(base, 9))
            detail = {
                **detail,
                "lawn_boundary_evidence": bool(
                    adjacent_lawn is not None
                    and geometry.area > 0
                    and geometry.intersection(adjacent_lawn).area >= geometry.area * 0.8
                ),
            }
            if not projected_curb_line(detail, geometry):
                continue
            polygon_ids.add(feature.feature_id)
            polygon_parts = (
                list(geometry.geoms)
                if isinstance(geometry, MultiPolygon)
                else [geometry]
            )
            chains = [
                [[x, y, base] for x, y, *_ in part.exterior.coords]
                for part in polygon_parts
            ]
            projected.append(
                {
                    "source_handle": feature.feature_id,
                    "source_entity_type": detail.get("source_entity_type"),
                    "interpretation": "native_projected_closed_line_perimeter",
                    "width_m": 0.15,
                    "height_m": 0.18,
                }
            )
        elif isinstance(geometry, (LineString, MultiLineString)):
            line_parts = (
                list(geometry.geoms)
                if isinstance(geometry, MultiLineString)
                else [geometry]
            )
            # Native XYZ carries its exact grade. Ordinary display-only paths
            # retain the same explicit CAD base as the full render pipeline.
            chains = [[[x, y, base] for x, y, *_ in part.coords] for part in line_parts]
        elif not native_xyz:
            continue
        source_lines[feature.feature_id] = chains
        for chain in chains:
            for start, end in zip(chain, chain[1:]):
                if hypot(end[0] - start[0], end[1] - start[1]) < 1e-8:
                    continue
                key = tuple(sorted((tuple(start), tuple(end))))
                if key in keys:
                    continue
                keys.add(key)
                rows.append(
                    {
                        "start_scene_xyz_m": start,
                        "end_scene_xyz_m": end,
                        "source_handle": feature.feature_id,
                        "curb_class": "illustrative_CAD_line_curb",
                        "width_m": 0.15,
                        "height_m": 0.18,
                    }
                )
    rows, interpretations, symbol_evidence = suppress_native_curb_graphics(
        rows, native_lines, evidence
    )
    visible_ids = {row["source_handle"] for row in rows}
    rows, aliases = _curb_aliases(
        rows,
        {
            identifier: chains
            for identifier, chains in source_lines.items()
            if identifier in visible_ids
        },
    )
    if (
        len(rows) > 60000
        or sum(
            hypot(
                r["end_scene_xyz_m"][0] - r["start_scene_xyz_m"][0],
                r["end_scene_xyz_m"][1] - r["start_scene_xyz_m"][1],
            )
            for r in rows
        )
        > 100000
    ):
        raise ValueError("Слишком много бордюров для просмотра 3D")
    meshes, _tops, composition = curb_envelopes(rows)
    composition["projected_closed_line_perimeters"] = projected
    composition["native_graphic_interpretation"] = symbol_evidence
    suppressed = {
        identifier for row in interpretations for identifier in row["source_ids"]
    }
    suppressed.update(row["source_handle"] for row in aliases)
    return {
        "meshes": meshes,
        "curb_polygon_ids": polygon_ids,
        "suppressed_source_ids": sorted(suppressed),
        "evidence": {
            "composition": composition,
            "display_aliases": aliases,
            "native_graphic_interpretations": interpretations,
        },
    }
