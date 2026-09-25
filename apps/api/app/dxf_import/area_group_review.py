"""Operator-selected authored curves; only AutoCAD may establish their interior."""

from hashlib import sha256

from app.dxf_import.layer_suggestions import suggest_layer_kind
from app.dxf_import.native_area_review import _unconfirmed_building_paths
from app.dxf_import.object_context import (
    JOINABLE_TYPES,
    area_members,
    route_of,
    source_features,
)
from app.dxf_import.object_review_contracts import SourceAreaGroup


def validate_members(project, request):
    if project.import_status.mode != "autocad_live" or not project.source_file:
        raise ValueError("Объединение доступно для захвата AutoCAD")
    if project.import_status.editability == "read_only":
        raise ValueError("Источник доступен только для просмотра")
    if project.source_file.content_sha256 != request.source_sha256:
        raise ValueError("Исходные данные изменились")
    routes = [
        "/".join((*m.source.instance_chain, m.source.handle)) for m in request.members
    ]
    if len(set(routes)) != len(routes):
        raise ValueError("Один объект выбран несколько раз")
    if len({route.rpartition("/")[0] for route in routes}) != 1:
        raise ValueError(
            "Выбраны объекты из разных вставок — такой контур пока не поддерживается"
        )
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
        raise ValueError(
            "Выберите кривые одного слоя — межслойное объединение пока не поддерживается"
        )
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
            raise ValueError("Выбранная кривая не совпадает с текущим захватом")
        if route in covered:
            raise ValueError("Объект уже входит в подтверждённую область")
        if route in interpreted:
            raise ValueError(
                "Сначала отмените решение о линии или обозначении выбранного объекта"
            )
    return routes


def update_groups(project, groups):
    before = {
        "/".join((*m.source.instance_chain, m.source.handle))
        for g in project.source_file.area_groups
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
            raise ValueError("Учёт неполной геометрии изменился")
        if count:
            layer.unsupported_geometry_types[kind] = count
        else:
            layer.unsupported_geometry_types.pop(kind, None)
        layer.geometry_complete = not layer.unsupported_geometry_types
    resolved = after | {
        "/".join((*m.source.instance_chain, m.source.handle))
        for m in project.source_file.object_decisions
    }
    remaining = _unconfirmed_building_paths(
        [
            f
            for f in source_features(project)
            if route_of(f.get("properties", {})) not in resolved
        ]
    )
    warnings = [
        w
        for w in project.source_file.warnings
        if not w.startswith("Линии зданий без подтверждённой площади")
    ]
    if remaining:
        warnings.append(
            f"Линии зданий без подтверждённой площади ({sum(remaining.values())})"
        )
    return project.model_copy(
        update={
            "layers": layers,
            "source_file": project.source_file.model_copy(
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


def accept_group(project, request):
    validate_members(project, request)
    digest = sha256(request.model_dump_json().encode()).hexdigest()
    group = SourceAreaGroup(**request.model_dump(), id=digest[:24])
    return update_groups(project, [*project.source_file.area_groups, group])


def remove_group(project, group_id):
    if project.import_status.editability == "read_only":
        raise ValueError("Источник доступен только для просмотра")
    groups = project.source_file.area_groups if project.source_file else []
    if not any(group.id == group_id for group in groups):
        raise ValueError("Подтверждённая область не найдена")
    return update_groups(project, [group for group in groups if group.id != group_id])
