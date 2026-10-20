"""Группировка выбранных оператором CAD-кривых для просмотра; внутренние области
восстанавливает только AutoCAD.
"""

from hashlib import sha256

from app.dxf_import.layers.suggestions import suggest_layer_kind
from app.dxf_import.review.context import (
    JOINABLE_TYPES,
    area_members,
    route_of,
    source_features,
)
from app.dxf_import.review.contracts import SourceAreaGroup, SourceAreaGroupRequest
from app.dxf_import.review.native_areas import unconfirmed_building_paths
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


def validate_members(project: Project, request: SourceAreaGroupRequest) -> list[str]:
    if project.import_status.mode != "autocad_live" or not project.source_file:
        raise DomainFailure(FailureReason.GROUP_CAPTURE_REQUIRED)
    if project.import_status.editability == "read_only":
        raise DomainFailure(FailureReason.GROUP_SOURCE_READ_ONLY)
    if project.source_file.content_sha256 != request.source_sha256:
        raise DomainFailure(FailureReason.GROUP_SOURCE_CHANGED)
    routes = [
        "/".join((*m.source.instance_chain, m.source.handle)) for m in request.members
    ]
    if len(set(routes)) != len(routes):
        raise DomainFailure(FailureReason.GROUP_DUPLICATE_OBJECT)
    if len({route.rpartition("/")[0] for route in routes}) != 1:
        raise DomainFailure(FailureReason.GROUP_INSTANCE_MISMATCH)
    covered = area_members(project)
    interpreted = {
        "/".join((*m.source.instance_chain, m.source.handle))
        for m in project.source_file.object_decisions
    }
    index = {route_of(f.get("properties", {})): f for f in source_features(project)}
    selected_layers = {
        index[r].get("properties", {}).get("source_layer") for r in routes if r in index
    }
    if len(selected_layers) > 1:
        raise DomainFailure(FailureReason.GROUP_LAYER_MISMATCH)
    for route, member in zip(routes, request.members, strict=True):
        feature = index.get(route)
        props = feature.get("properties", {}) if feature else {}
        if (
            not feature
            or feature.get("geometry", {}).get("type") != "LineString"
            or props.get("entity_type") not in JOINABLE_TYPES
            or props.get("source_context_only")
            or props.get("source_geometry_content_sha256") != member.geometry_sha256
        ):
            raise DomainFailure(FailureReason.GROUP_CURVE_CHANGED)
        if route in covered:
            raise DomainFailure(FailureReason.GROUP_OBJECT_ALREADY_COVERED)
        if route in interpreted:
            raise DomainFailure(FailureReason.GROUP_OBJECT_INTERPRETATION_REQUIRED)
    return routes


def update_groups(project: Project, groups: list[SourceAreaGroup]) -> Project:
    source = project.source_file
    assert (
        source is not None
    )  # Принятие и удаление выполняют native-допуск до изменения состояния.
    before = {
        "/".join((*m.source.instance_chain, m.source.handle))
        for g in source.area_groups
        for m in g.members
    }
    after = {
        "/".join((*m.source.instance_chain, m.source.handle))
        for g in groups
        for m in g.members
    }
    layers = [layer.model_copy(deep=True) for layer in project.layers]
    layer_index = {layer.source_name: layer for layer in layers}
    for feature in source_features(project):
        props = feature.get("properties", {})
        route = route_of(props)
        if (
            route not in before ^ after
            or feature.get("geometry", {}).get("type") != "LineString"
        ):
            continue
        if (
            "source_closed_path" not in props
            or suggest_layer_kind(props.get("source_layer", "")) != "building"
        ):
            continue
        layer = layer_index[props["source_layer"]]
        kind = props["entity_type"]
        count = layer.unsupported_geometry_types.get(kind, 0) + (
            -1 if route in after else 1
        )
        if count < 0:
            raise DomainFailure(FailureReason.GROUP_COVERAGE_CHANGED)
        if count:
            layer.unsupported_geometry_types[kind] = count
        else:
            layer.unsupported_geometry_types.pop(kind, None)
        layer.geometry_complete = not layer.unsupported_geometry_types
    resolved = after | {
        "/".join((*m.source.instance_chain, m.source.handle))
        for m in source.object_decisions
    }
    remaining = unconfirmed_building_paths(
        [
            f
            for f in source_features(project)
            if route_of(f.get("properties", {})) not in resolved
        ]
    )
    warnings = [
        w
        for w in source.warnings
        if not w.startswith("Линии зданий без подтверждённой площади")
    ]
    if remaining:
        warnings.append(
            f"Линии зданий без подтверждённой площади ({sum(remaining.values())})"
        )
    return project.model_copy(
        update={
            "layers": layers,
            "source_file": source.model_copy(
                update={"area_groups": groups, "warnings": warnings}
            ),
            "source_geometry": project.source_geometry or project.geometry,
            "geometry": None,
            "map_ready": False,
            "site_area_m2": None,
            "planning_area_m2": None,
            "allowed_area_m2": None,
        }
    )


def accept_group(project: Project, request: SourceAreaGroupRequest) -> Project:
    validate_members(project, request)
    source = project.source_file
    assert source is not None  # Допуск выполнен validate_members выше.
    digest = sha256(request.model_dump_json().encode()).hexdigest()
    group = SourceAreaGroup(**request.model_dump(), id=digest[:24])
    return update_groups(project, [*source.area_groups, group])


def remove_group(project: Project, group_id: str) -> Project:
    if project.import_status.editability == "read_only":
        raise DomainFailure(FailureReason.GROUP_SOURCE_READ_ONLY)
    groups = project.source_file.area_groups if project.source_file else []
    if not any(group.id == group_id for group in groups):
        raise DomainFailure(FailureReason.GROUP_MISSING)
    return update_groups(project, [group for group in groups if group.id != group_id])
