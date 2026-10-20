"""Просмотр читаемых CAD-объектов, не принятых как расчётные области; исходная геометрия
не исправляется в Python.
"""

from math import dist
from typing import Any

from app.cad_bridge.contracts import SourceIdentity
from app.dxf_import.contracts import ImportEditability
from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import Layer
from app.dxf_import.layers.suggestions import local_layer_name, suggest_layer_kind
from app.dxf_import.review.contracts import (
    OBJECT_LINEAR_ENTITY_TYPES,
    SourceObjectDecision,
    SourceObjectDecisionRequest,
    SourceObjectReviewItem,
    SourceObjectReviewPage,
)
from app.dxf_import.review.native_areas import unconfirmed_building_paths
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


def object_review_eligible(layer: Layer | None, feature: dict[str, Any]) -> bool:
    """Допустить адресное решение по читаемому объекту, сохраняя неоднозначный 0."""
    props = feature.get("properties", {})
    if (
        layer is None
        or props.get("source_context_only")
        or not props.get("source_geometry_content_sha256")
        or not props.get("source_handle")
    ):
        return False
    geometry_type = feature.get("geometry", {}).get("type")
    mixed = (
        layer.category == LayerCategory.MIXED_SOURCE
        or local_layer_name(layer.source_name).strip() == "0"
    )
    if mixed:
        return geometry_type == "LineString" or (
            geometry_type in {"Polygon", "MultiPolygon"}
            and props.get("source_native_geometry") is True
        )
    return bool(
        layer.mapped_kind in {"building", "restricted", "road"}
        and geometry_type == "LineString"
        and props.get("source_closed_path") is False
    )


def _review_path(feature: dict[str, Any]) -> list[tuple[float, float]]:
    """Первый контур нужен для фокуса UI; полная native-геометрия не изменяется."""
    geometry = feature["geometry"]
    points = geometry["coordinates"]
    if geometry["type"] == "Polygon":
        points = points[0]
    elif geometry["type"] == "MultiPolygon":
        points = points[0][0]
    return [tuple(point[:2]) for point in points]


def unresolved_paths(project: Project) -> list[tuple[str, dict[str, Any]]]:
    source = project.source_file
    if source is None or project.import_status.mode != "autocad_live":
        raise DomainFailure(FailureReason.SOURCE_OBJECT_CAPTURE_REQUIRED)
    graph = project.source_geometry or project.geometry
    features = graph.feature_collection.get("features", []) if graph else []
    covered = {
        "/".join((*member.get("instance_chain", []), member.get("handle", "")))
        for snapshot in (project.source_geometry, project.geometry)
        if snapshot is not None
        for f in snapshot.feature_collection.get("features", [])
        if f.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}
        for member in f.get("properties", {}).get("source_derived_from", [])
    }
    covered.update(
        "/".join((*item.source.instance_chain, item.source.handle))
        for item in source.native_area_proposals
        if item.decision == "accepted"
    )
    covered.update(
        "/".join((*m.source.instance_chain, m.source.handle))
        for group in source.area_groups
        for m in group.members
    )
    layers = {layer.source_name: layer for layer in project.layers}
    result = []
    for feature in features:
        props = feature.get("properties", {})
        layer = layers.get(props.get("source_layer"))
        if not object_review_eligible(layer, feature):
            continue
        route = "/".join(
            (*props.get("source_instance_chain", []), props["source_handle"])
        )
        if route not in covered:
            result.append((route, feature))
    return result


def review_objects(
    project: Project, *, layer: str | None = None, offset: int = 0, limit: int = 30
) -> SourceObjectReviewPage:
    paths = unresolved_paths(project)
    source = project.source_file
    assert source is not None  # Допуск захвата уже выполнен unresolved_paths выше.
    if layer is not None:
        paths = [
            (route, feature)
            for route, feature in paths
            if feature["properties"]["source_layer"] == layer
        ]
    decisions = {
        "/".join((*item.source.instance_chain, item.source.handle)): item
        for item in source.object_decisions
    }
    items = []
    for route, feature in paths[offset : offset + limit]:
        props = feature["properties"]
        points = _review_path(feature)
        decision = decisions.get(route)
        items.append(
            SourceObjectReviewItem(
                route=route,
                source=SourceIdentity(
                    handle=props["source_handle"],
                    instance_chain=props.get("source_instance_chain", []),
                ),
                layer=props["source_layer"],
                entity_type=props.get("entity_type", ""),
                geometry_sha256=props["source_geometry_content_sha256"],
                path=points,
                endpoint_distance_m=dist(points[0], points[-1]),
                interpretation=decision.interpretation if decision else "area",
                kind=decision.kind if decision else None,
            )
        )
    return SourceObjectReviewPage.model_validate(
        {
            "source_sha256": source.content_sha256,
            "total": len(paths),
            "offset": offset,
            "items": items,
        }
    )


def decide_object(project: Project, request: SourceObjectDecisionRequest) -> Project:
    if project.import_status.editability == ImportEditability.READ_ONLY:
        raise DomainFailure(FailureReason.SOURCE_READ_ONLY)
    if (
        not project.source_file
        or project.source_file.content_sha256 != request.source_sha256
    ):
        raise DomainFailure(FailureReason.NATIVE_AREA_SNAPSHOT_CHANGED)
    route = "/".join((*request.source.instance_chain, request.source.handle))
    feature = next(
        (feature for key, feature in unresolved_paths(project) if key == route), None
    )
    if (
        feature is None
        or feature["properties"]["source_geometry_content_sha256"]
        != request.geometry_sha256
    ):
        raise DomainFailure(FailureReason.SOURCE_OBJECT_LINE_CHANGED)
    if (
        request.interpretation == "linear"
        and feature["properties"].get("entity_type") not in OBJECT_LINEAR_ENTITY_TYPES
    ):
        raise DomainFailure(FailureReason.SOURCE_OBJECT_LINEAR_UNAVAILABLE)
    previous = next(
        (
            item
            for item in project.source_file.object_decisions
            if item.source == request.source
        ),
        None,
    )
    if (
        previous is not None
        and previous.interpretation == request.interpretation
        and previous.kind == request.kind
    ):
        return project
    decisions = [
        item
        for item in project.source_file.object_decisions
        if item.source != request.source
    ]
    if request.interpretation != "area" or request.kind is not None:
        decisions.append(
            SourceObjectDecision(**request.model_dump(exclude={"source_sha256"}))
        )
    if decisions == project.source_file.object_decisions:
        return project
    graph = project.source_geometry or project.geometry
    assert graph is not None  # Совпавший нерешённый объект получен из этого графа.
    was_resolved = previous is not None and previous.interpretation != "area"
    is_resolved = request.interpretation != "area"
    props = feature["properties"]
    layers = [item.model_copy(deep=True) for item in project.layers]
    # Импорт считает отсутствующее внутреннее пространство только у зданий.
    # Читаемая граница дороги не уменьшает счётчик непрочитанных HATCH.
    if (
        was_resolved != is_resolved
        and suggest_layer_kind(props["source_layer"]) == "building"
    ):
        source_layer = next(
            item for item in layers if item.source_name == props["source_layer"]
        )
        counts = dict(source_layer.unsupported_geometry_types)
        kind = props["entity_type"]
        current = counts.get(kind, 0)
        if is_resolved and current < 1:
            raise DomainFailure(FailureReason.NATIVE_AREA_COVERAGE_CHANGED)
        counts[kind] = current + (-1 if is_resolved else 1)
        source_layer.unsupported_geometry_types = {
            key: count for key, count in counts.items() if count
        }
        source_layer.geometry_complete = not source_layer.unsupported_geometry_types
    resolved = {
        "/".join((*item.source.instance_chain, item.source.handle))
        for item in decisions
        if item.interpretation != "area"
    }
    resolved.update(
        "/".join((*m.source.instance_chain, m.source.handle))
        for group in project.source_file.area_groups
        for m in group.members
    )
    remaining = unconfirmed_building_paths(
        [
            item
            for item in graph.feature_collection.get("features", [])
            if "/".join(
                (
                    *item.get("properties", {}).get("source_instance_chain", []),
                    item.get("properties", {}).get("source_handle", ""),
                )
            )
            not in resolved
        ]
    )
    warnings = list(project.source_file.warnings)
    if was_resolved != is_resolved:
        warnings = [
            warning
            for warning in warnings
            if not warning.startswith("Линии зданий без подтверждённой площади")
        ]
        if remaining:
            warnings.append(
                f"Линии зданий без подтверждённой площади ({sum(remaining.values())})"
            )
    return project.model_copy(
        update={
            "layers": layers,
            "source_file": project.source_file.model_copy(
                update={"object_decisions": decisions, "warnings": warnings}
            ),
            "source_geometry": graph,
            "geometry": None,
            "map_ready": False,
            "site_area_m2": None,
            "planning_area_m2": None,
            "allowed_area_m2": None,
        }
    )
