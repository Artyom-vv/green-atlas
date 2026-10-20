"""Контекст отображения объектов из сохранённого снимка; CAD-парсер для просмотра не
запускается.
"""

from typing import Any

from app.cad_bridge.contracts import SourceIdentity
from app.dxf_import.review.contracts import (
    SourceContextObject,
    SourceObjectContext,
)
from app.dxf_import.review.objects import object_review_eligible
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason

CONTEXT_MARGIN_M = 8.0
MAX_CONTEXT_OBJECTS = 1800
MAX_CONTEXT_VERTICES = 100_000
JOINABLE_TYPES = {"LINE", "ARC", "LWPOLYLINE", "CIRCLE", "ELLIPSE", "SPLINE"}


def route_of(props: dict[str, Any]) -> str:
    return "/".join(
        (*props.get("source_instance_chain", []), props.get("source_handle", ""))
    )


def source_features(project: Project) -> list[dict[str, Any]]:
    graph = project.source_geometry or project.geometry
    if not graph or not project.source_file:
        raise DomainFailure(FailureReason.SOURCE_CONTEXT_GEOMETRY_MISSING)
    return graph.feature_collection.get("features", [])


def area_members(project: Project) -> set[str]:
    source = project.source_file
    assert (
        source is not None
    )  # Владелец уже допущен source_features и validate_members.
    covered = {
        route_of(
            {
                "source_handle": member.get("handle"),
                "source_instance_chain": member.get("instance_chain", []),
            }
        )
        for graph in (project.source_geometry, project.geometry)
        if graph is not None
        for feature in graph.feature_collection.get("features", [])
        if feature.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
        for member in feature.get("properties", {}).get("source_derived_from", [])
    }
    covered.update(
        "/".join((*member.source.instance_chain, member.source.handle))
        for group in source.area_groups
        for member in group.members
    )
    return covered


def paths_of(geometry: dict[str, Any]) -> list[list[list[float]]]:
    coordinates = geometry.get("coordinates", [])
    kind = geometry.get("type")
    if kind == "LineString":
        return [coordinates]
    if kind in {"Polygon", "MultiLineString"}:
        return coordinates
    if kind == "MultiPolygon":
        return [ring for polygon in coordinates for ring in polygon]
    return []


def object_context(
    project: Project, route: str, *, scale: float = 1.0
) -> SourceObjectContext:
    features = source_features(project)
    source = project.source_file
    assert source is not None  # Допуск выполнен source_features выше.
    focus = next(
        (f for f in features if route_of(f.get("properties", {})) == route), None
    )
    if focus is None:
        raise DomainFailure(FailureReason.SOURCE_CONTEXT_OBJECT_MISSING)
    points = [p for path in paths_of(focus.get("geometry", {})) for p in path]
    if not points:
        raise DomainFailure(FailureReason.SOURCE_CONTEXT_CONTOUR_MISSING)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    margin = (
        max(CONTEXT_MARGIN_M, max(max(xs) - min(xs), max(ys) - min(ys)) * 0.2) * scale
    )
    extent = min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin
    covered = area_members(project)
    layers = {item.source_name: item for item in project.layers}
    decisions = {
        "/".join((*item.source.instance_chain, item.source.handle)): item
        for item in source.object_decisions
    }
    by_route: dict[str, tuple[bool, float, int, SourceContextObject]] = {}
    for feature in features:
        props, geometry = feature.get("properties", {}), feature.get("geometry", {})
        if not props.get("source_handle"):
            continue
        paths = paths_of(geometry)
        points = [point for path in paths for point in path]
        if not points:
            continue
        x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
        x1, y1 = max(p[0] for p in points), max(p[1] for p in points)
        if x1 < extent[0] or y1 < extent[1] or x0 > extent[2] or y0 > extent[3]:
            continue
        key = route_of(props)
        layer = layers.get(props.get("source_layer"))
        decision = decisions.get(key)
        effective_kind = (
            None
            if decision is not None and decision.interpretation == "reference"
            else decision.kind
            if decision is not None and decision.kind is not None
            else layer.mapped_kind
            if layer
            else None
        )
        can_join = (
            geometry.get("type") == "LineString"
            and key not in covered
            and props.get("entity_type") in JOINABLE_TYPES
            and bool(props.get("source_geometry_content_sha256"))
            and not props.get("source_context_only")
        )
        item = SourceContextObject.model_validate(
            {
                "route": key,
                "source": SourceIdentity(
                    handle=props["source_handle"],
                    instance_chain=props.get("source_instance_chain", []),
                ),
                "geometry_sha256": props.get("source_geometry_content_sha256"),
                "layer": props.get("source_layer", ""),
                "kind": effective_kind,
                "entity_type": props.get("entity_type", ""),
                "paths": [[tuple(p[:2]) for p in path] for path in paths],
                "native_area": geometry.get("type") in {"Polygon", "MultiPolygon"},
                "can_join": can_join,
                "reviewable": key not in covered
                and object_review_eligible(layer, feature),
            }
        )
        distance = (
            max(x0 - xs[0], 0, xs[0] - x1) ** 2 + max(y0 - ys[0], 0, ys[0] - y1) ** 2
        )
        # Принятая native-область может иметь тот же дескриптор, что отображаемая
        # кривая. Выбираем область и не предлагаем одну идентичность дважды.
        previous = by_route.get(key)
        if previous is None or item.native_area:
            by_route[key] = (key != route, distance, len(points), item)
    candidates = list(by_route.values())
    candidates.sort(key=lambda row: row[:2])
    items: list[SourceContextObject] = []
    vertices = 0
    for _, _, count, item in candidates:
        if items and (
            len(items) >= MAX_CONTEXT_OBJECTS or vertices + count > MAX_CONTEXT_VERTICES
        ):
            continue
        items.append(item)
        vertices += count
    return SourceObjectContext.model_validate(
        {
            "source_sha256": source.content_sha256,
            "focus_route": route,
            "extent": extent,
            "objects": items,
            "total": len(candidates),
            "limited": len(items) < len(candidates),
        }
    )
