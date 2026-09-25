"""Object-aware display context from the admitted snapshot, not a CAD parser."""

from app.cad_bridge.contracts import SourceIdentity
from app.dxf_import.object_review_contracts import (
    SourceContextObject,
    SourceObjectContext,
)

CONTEXT_MARGIN_M = 8.0
MAX_CONTEXT_OBJECTS = 1800
MAX_CONTEXT_VERTICES = 100_000
JOINABLE_TYPES = {"LINE", "ARC", "LWPOLYLINE", "CIRCLE", "ELLIPSE", "SPLINE"}


def route_of(props):
    return "/".join(
        (*props.get("source_instance_chain", []), props.get("source_handle", ""))
    )


def source_features(project):
    graph = project.source_geometry or project.geometry
    if not graph or not project.source_file:
        raise ValueError("Геометрия исходного чертежа недоступна")
    return graph.feature_collection.get("features", [])


def area_members(project):
    covered = {
        route_of(
            {
                "source_handle": member.get("handle"),
                "source_instance_chain": member.get("instance_chain", []),
            }
        )
        for graph in (project.source_geometry, project.geometry) if graph is not None
        for feature in graph.feature_collection.get("features", [])
        if feature.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
        for member in feature.get("properties", {}).get("source_derived_from", [])
    }
    covered.update(
        "/".join((*member.source.instance_chain, member.source.handle))
        for group in project.source_file.area_groups
        for member in group.members
    )
    return covered


def paths_of(geometry):
    coordinates = geometry.get("coordinates", [])
    kind = geometry.get("type")
    if kind == "LineString":
        return [coordinates]
    if kind in {"Polygon", "MultiLineString"}:
        return coordinates
    if kind == "MultiPolygon":
        return [ring for polygon in coordinates for ring in polygon]
    return []


def object_context(project, route, *, scale=1.0):
    features = source_features(project)
    focus = next(
        (f for f in features if route_of(f.get("properties", {})) == route), None
    )
    if focus is None:
        raise ValueError("Объект не найден в текущем захвате")
    points = [p for path in paths_of(focus.get("geometry", {})) for p in path]
    if not points:
        raise ValueError("У объекта нет доступного контура для просмотра")
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    margin = (
        max(CONTEXT_MARGIN_M, max(max(xs) - min(xs), max(ys) - min(ys)) * 0.2) * scale
    )
    extent = min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin
    covered = area_members(project)
    layers = {item.source_name: item for item in project.layers}
    by_route = {}
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
        can_join = (
            geometry.get("type") == "LineString"
            and key not in covered
            and props.get("entity_type") in JOINABLE_TYPES
            and bool(props.get("source_geometry_content_sha256"))
            and not props.get("source_context_only")
        )
        item = SourceContextObject(
            route=key,
            source=SourceIdentity(
                handle=props["source_handle"],
                instance_chain=props.get("source_instance_chain", []),
            ),
            geometry_sha256=props.get("source_geometry_content_sha256"),
            layer=props.get("source_layer", ""),
            kind=str(layer.mapped_kind or "ignore") if layer else "ignore",
            entity_type=props.get("entity_type", ""),
            paths=[[tuple(p[:2]) for p in path] for path in paths],
            native_area=geometry.get("type") in {"Polygon", "MultiPolygon"},
            can_join=can_join,
            reviewable=can_join
            and props.get("source_closed_path") is False
            and bool(layer)
            and layer.mapped_kind in {"building", "road", "restricted"},
        )
        distance = (
            max(x0 - xs[0], 0, xs[0] - x1) ** 2 + max(y0 - ys[0], 0, ys[0] - y1) ** 2
        )
        # An accepted native area may share the source handle with its display
        # curve. Prefer the area, never offer the same identity twice.
        previous = by_route.get(key)
        if previous is None or item.native_area:
            by_route[key] = (key != route, distance, len(points), item)
    candidates = list(by_route.values())
    candidates.sort(key=lambda row: row[:2])
    items, vertices = [], 0
    for _, _, count, item in candidates:
        if items and (
            len(items) >= MAX_CONTEXT_OBJECTS or vertices + count > MAX_CONTEXT_VERTICES
        ):
            continue
        items.append(item)
        vertices += count
    return SourceObjectContext(
        source_sha256=project.source_file.content_sha256,
        focus_route=route,
        extent=extent,
        objects=items,
        total=len(candidates),
        limited=len(items) < len(candidates),
    )
