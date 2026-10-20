"""Render-only roles and whole CAD tree markers from one accepted project.

No CAD reader or layer assignment runs here. Unknown marker dimensions and
species remain unknown; optional Botaniq-sized volumes are explicit proxies.
"""

from collections import Counter, defaultdict
from math import isfinite, pi
from typing import Any

from shapely import STRtree, union_all
from shapely.geometry import MultiPolygon, Point, Polygon, shape

from app.dxf_import.layers.contracts import Layer
from app.projects.contracts import Project
from app.scene.contracts import SceneSnapshot

ROLES = {
    "carriageway": "road",
    "footway": "sidewalk",
    "hard_surface": "sidewalk",
    "lawn": "lawn",
    "curb": "curb",
    "equipment": "equipment",
    "road_marking": "marking",
}
MAX_ADDRESSES = 64


def _tree_layer(layer: Layer) -> bool:
    """Use explicit existing-tree semantics, never general green linework."""
    leaf = layer.source_name.rsplit("|", 1)[-1].casefold()
    excluded = (
        "демонтаж",
        "снос",
        "удален",
        "удалён",
        "выруб",
        "посадк",
        "проект",
        "planned",
        "planting",
        "removal",
        "demolition",
    )
    return (
        layer.mapping_confirmed is True
        and layer.mapped_kind == "existing_green"
        and not any(term in leaf for term in excluded)
        and (layer.category == "tree" or "отдельно стоящее дерево" in leaf)
    )


def _circle(feature: dict[str, Any]) -> tuple[float, float, float] | None:
    properties = feature.get("properties", {})
    if (
        properties.get("entity_type") not in {"ARC", "CIRCLE"}
        or properties.get("source_closed_path") is not True
    ):
        return None
    try:
        geometry = shape(feature["geometry"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not isinstance(geometry, Polygon)
        or geometry.is_empty
        or not geometry.is_valid
        or geometry.interiors
    ):
        return None
    x0, y0, x1, y1 = geometry.bounds
    width, depth = x1 - x0, y1 - y0
    if (
        not all(isfinite(value) for value in (x0, y0, x1, y1))
        or min(width, depth) < 0.1
        or max(width, depth) > 2
        or min(width, depth) / max(width, depth) < 0.9
        or geometry.length <= 0
        or 4 * pi * geometry.area / geometry.length**2 < 0.9
    ):
        return None
    center = geometry.centroid
    return center.x, center.y, max(width, depth)


def environment_inputs(project: Project, snapshot: SceneSnapshot) -> dict[str, Any]:
    """Extract only accepted scene IDs, without editing geometry or mappings."""
    if (
        project.id != snapshot.project_id
        or project.geometry_version != snapshot.geometry_version
    ):
        raise ValueError("Environment project does not match the accepted scene")
    source_sha = project.source_file.content_sha256 if project.source_file else None
    if source_sha != snapshot.source_sha256:
        raise ValueError("Environment source does not match the accepted scene")

    counts: Counter[str] = Counter()
    addresses: dict[str, list[str]] = defaultdict(list)
    seen_omissions: set[tuple[str, str]] = set()

    def omit(reason: str, feature_id: str) -> None:
        key = reason, feature_id
        if key in seen_omissions:
            return
        seen_omissions.add(key)
        counts[reason] += 1
        if len(addresses[reason]) < MAX_ADDRESSES:
            addresses[reason].append(feature_id)

    layers_by_name: dict[str, list[Layer]] = defaultdict(list)
    for layer in project.layers:
        layers_by_name[layer.source_name].append(layer)
    selected = {row.feature_id: row for row in snapshot.context_features}
    roles: dict[str, str] = {}
    role_evidence = []
    unknown_layers: dict[str, Counter[str]] = defaultdict(Counter)
    unsupported_layers: dict[str, Counter[str]] = defaultdict(Counter)
    marker_layers: dict[str, Layer] = {}
    lawns: list[Polygon | MultiPolygon] = []
    lawn_bases: list[float | None] = []
    lawn_ids: list[str] = []
    for feature in snapshot.context_features:
        name = feature.source_layer or ""
        candidates = layers_by_name.get(name, [])
        if len(candidates) != 1:
            omit(
                "ambiguous_layer" if candidates else "missing_layer", feature.feature_id
            )
            continue
        layer = candidates[0]
        if layer.mapping_confirmed is not True or layer.mapped_kind in {None, "ignore"}:
            omit("unconfirmed_or_ignored_layer", feature.feature_id)
            unknown_layers[name]["unconfirmed_or_ignored_layer"] += 1
            continue
        if str(layer.mapped_kind) != feature.kind:
            omit("layer_kind_mismatch", feature.feature_id)
            continue
        is_tree_layer = _tree_layer(layer)
        if is_tree_layer:
            marker_layers[name] = layer
        role = ROLES.get(str(layer.category))
        if role is None:
            if not is_tree_layer and feature.kind in {
                "road",
                "lawn",
                "existing_green",
                "restricted",
            }:
                category_reason = (
                    "unknown_environment_category"
                    if layer.category is None
                    else "unsupported_environment_category"
                )
                omit(category_reason, feature.feature_id)
                (unknown_layers if layer.category is None else unsupported_layers)[
                    name
                ][category_reason] += 1
            continue
        roles[feature.feature_id] = role
        role_evidence.append(
            {
                "feature_id": feature.feature_id,
                "role": role,
                "layer_id": layer.id,
                "source_layer": name,
                "category": str(layer.category),
                "mapping_confirmed": True,
                "geometry_type": feature.geometry.get("type"),
                "base_elevation_m": feature.base_elevation_m,
                "source_entity_type": feature.source_entity_type,
                "source_handle": feature.source_handle,
            }
        )
        if role == "equipment":
            omit("equipment_type_unresolved", feature.feature_id)
        if role == "lawn":
            try:
                geometry = shape(feature.geometry)
            except (TypeError, ValueError):
                omit("invalid_lawn_geometry", feature.feature_id)
                continue
            if (
                isinstance(geometry, (Polygon, MultiPolygon))
                and geometry.is_valid
                and not geometry.is_empty
            ):
                lawns.append(geometry)
                lawn_bases.append(feature.base_elevation_m)
                lawn_ids.append(feature.feature_id)
            else:
                omit("invalid_lawn_geometry", feature.feature_id)

    lawn = union_all(lawns) if lawns else None
    lawn_index = STRtree(lawns) if lawns else None
    if lawn is not None and not lawn.is_valid:
        raise ValueError("Invalid accepted lawn union for environment markers")
    graph = project.geometry or project.source_geometry
    role_details = {row["feature_id"]: row for row in role_evidence}
    groups: dict[tuple[str, ...], dict[str, Any]] = {}
    raw_selected_count = 0
    raw_seen: set[str] = set()
    if graph is not None:
        for raw in graph.feature_collection.get("features", []):
            identifier = str(raw.get("id") or "")
            if identifier not in selected:
                continue
            raw_selected_count += 1
            if identifier in raw_seen:
                continue
            raw_seen.add(identifier)
            feature = selected[identifier]
            name = feature.source_layer or ""
            properties = raw.get("properties", {})
            if roles.get(identifier) == "curb":
                # Native curve semantics belong to the accepted capture. A
                # display Polygon made from a closed CAD line is not proof
                # that its whole interior is a concrete curb footprint.
                detail = role_details[identifier]
                if (
                    properties.get("source_layer") != name
                    or properties.get("kind") != feature.kind
                    or (
                        feature.source_entity_type is not None
                        and properties.get("entity_type") != feature.source_entity_type
                    )
                    or (
                        feature.source_handle is not None
                        and properties.get("source_handle") != feature.source_handle
                    )
                ):
                    omit("curb_source_evidence_mismatch", identifier)
                else:
                    detail["source_entity_type"] = properties.get("entity_type")
                    for flag in ("source_closed_path", "source_polygon_projection"):
                        value = properties.get(flag)
                        detail[flag] = value if isinstance(value, bool) else None
                    native_area = properties.get("source_native_area")
                    if native_area is None:
                        native_area = properties.get("source_native_area_units2")
                    if native_area is None or (
                        isinstance(native_area, (int, float))
                        and not isinstance(native_area, bool)
                        and isfinite(native_area)
                        and native_area >= 0
                    ):
                        detail["source_native_area"] = native_area
                    else:
                        omit("invalid_curb_native_area", identifier)
                        detail["source_polygon_projection"] = None
                    for purpose in ("source_geometry_purpose", "representation"):
                        value = properties.get(purpose)
                        if isinstance(value, str):
                            detail[purpose] = value
                    chain = properties.get("source_instance_chain")
                    if isinstance(chain, list) and all(
                        isinstance(member, str) and member for member in chain
                    ):
                        detail["source_instance_chain"] = list(chain)
                    # Retain native-capture provenance already accepted into
                    # this graph. Rendering does not reopen CAD or its archive.
                    if properties.get("source_native_geometry") is True:
                        detail["source_native_geometry"] = True
                    provider = properties.get("source_geometry_provider")
                    if isinstance(provider, str):
                        detail["source_geometry_provider"] = provider
                    geometry = raw.get("geometry", {})
                    coordinates = geometry.get("coordinates")
                    if (
                        detail.get("source_native_geometry") is True
                        and provider == "autocad_snapshot_v1"
                        and geometry.get("type") == "LineString"
                        and isinstance(coordinates, (list, tuple))
                        and len(coordinates) >= 2
                    ):
                        # The display mesh may simplify an arc to a chord.
                        # Whole-symbol evidence uses the accepted native path,
                        # converted only to the same local coordinate frame.
                        if all(
                            isinstance(point, (list, tuple))
                            and len(point) in {2, 3}
                            and all(
                                isinstance(value, (int, float))
                                and not isinstance(value, bool)
                                and isfinite(value)
                                for value in point
                            )
                            for point in coordinates
                        ):
                            ox, oy = snapshot.coordinate_origin
                            detail["source_native_curve_scene_xyz_m"] = [
                                [
                                    point[0] - ox,
                                    point[1] - oy,
                                    point[2]
                                    if len(point) == 3
                                    else feature.base_elevation_m or 0.0,
                                ]
                                for point in coordinates
                            ]
                        else:
                            omit("invalid_curb_native_curve", identifier)
            if name not in marker_layers:
                continue
            if (
                properties.get("source_layer") != name
                or properties.get("kind") != "existing_green"
            ):
                omit("marker_source_mismatch", identifier)
                continue
            chain = properties.get("source_instance_chain")
            if (
                not isinstance(chain, list)
                or not chain
                or not all(isinstance(item, str) and item for item in chain)
            ):
                omit("marker_instance_missing", identifier)
                continue
            key = tuple(chain)
            group = groups.setdefault(
                key, {"layers": set(), "source_ids": [], "anchors": {}}
            )
            group["layers"].add(name)
            group["source_ids"].append(identifier)
            anchor = _circle(raw)
            if anchor is not None:
                # Coincident duplicate source rows do not make a second anchor.
                anchor_key = tuple(round(value, 6) for value in anchor)
                group["anchors"].setdefault(anchor_key, []).append(identifier)
    for feature in snapshot.context_features:
        if (
            feature.source_layer or ""
        ) in marker_layers and feature.feature_id not in raw_seen:
            omit("marker_source_missing", feature.feature_id)

    markers = []
    for chain, group in sorted(groups.items()):
        anchors = group["anchors"]
        reason = (
            "marker_layer_ambiguous"
            if len(group["layers"]) != 1
            else "marker_anchor_ambiguous"
            if len(anchors) > 1
            else "marker_anchor_missing"
            if not anchors
            else "marker_lawn_unavailable"
            if lawn is None
            else None
        )
        if reason:
            for identifier in group["source_ids"]:
                omit(reason, identifier)
            continue
        x, y, _ = next(iter(anchors))
        local_x = x - snapshot.coordinate_origin[0]
        local_y = y - snapshot.coordinate_origin[1]
        assert lawn is not None
        point = Point(local_x, local_y)
        if not lawn.covers(point):
            for identifier in group["source_ids"]:
                omit("marker_outside_lawn", identifier)
            continue
        assert lawn_index is not None
        covering = [
            int(index)
            for index in lawn_index.query(point)
            if lawns[int(index)].covers(point)
        ]
        bases = {lawn_bases[index] for index in covering}
        if len(bases) != 1:
            for identifier in group["source_ids"]:
                omit("marker_base_ambiguous", identifier)
            continue
        base = next(iter(bases))
        markers.append(
            {
                "object_id": "existing-tree/" + "/".join(chain),
                "local_x": round(local_x, 6),
                "local_y": round(local_y, 6),
                "height_m": 12.0,
                "radius_m": 3.5,
                "base_elevation_m": base,
                "source_feature_ids": sorted(group["source_ids"]),
                "anchor_feature_ids": sorted(next(iter(anchors.values()))),
                "source_layer": next(iter(group["layers"])),
                "source_instance_chain": list(chain),
                "evidence": {
                    "origin": "cad_tree_marker",
                    "position_status": "circular_marker_anchor",
                    "height_status": "estimated",
                    "size_source": "illustrative_render_proxy",
                    "species_status": "unknown",
                    "lawn_status": "confirmed_surface",
                    "base_elevation_source": (
                        "accepted_lawn_base"
                        if base is not None
                        else "confirmed_terrain_or_unmeasured_datum"
                    ),
                    "base_elevation_feature_ids": sorted(
                        lawn_ids[index] for index in covering
                    ),
                },
            }
        )
    return {
        "schema": "green-atlas.render-environment-inputs/1",
        "project_id": project.id,
        "scene_sha256": snapshot.content_sha256,
        "roles": roles,
        "role_evidence": role_evidence,
        "existing_tree_markers": markers,
        "omissions": {
            "counts": dict(sorted(counts.items())),
            "feature_ids": dict(sorted(addresses.items())),
            "truncated_reasons": sorted(
                reason for reason, count in counts.items() if count > MAX_ADDRESSES
            ),
        },
        "metadata": {
            "snapshot_feature_count": len(selected),
            "raw_selected_feature_count": raw_selected_count,
            "role_counts": dict(sorted(Counter(roles.values()).items())),
            "marker_group_count": len(groups),
            "marker_count": len(markers),
            "unknown_category_layer_count": len(unknown_layers),
            "unknown_category_layers": [
                {"source_layer": name, "counts": dict(reasons)}
                for name, reasons in sorted(unknown_layers.items())[:MAX_ADDRESSES]
            ],
            "unsupported_category_layer_count": len(unsupported_layers),
            "unsupported_category_layers": [
                {"source_layer": name, "counts": dict(reasons)}
                for name, reasons in sorted(unsupported_layers.items())[:MAX_ADDRESSES]
            ],
            "proxy_rule": "Existing tree size 12 m / radius 3.5 m is an illustrative appearance proxy, without species or inventory measurement",
        },
    }
