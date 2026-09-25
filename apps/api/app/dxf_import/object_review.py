"""Review readable paths that have no admitted area; never repair CAD in Python."""

from math import dist

from app.cad_bridge.contracts import SourceIdentity
from app.dxf_import.contracts import ImportEditability
from app.dxf_import.layer_suggestions import suggest_layer_kind
from app.dxf_import.native_area_review import _unconfirmed_building_paths
from app.dxf_import.object_review_contracts import (
    SourceObjectDecision,
    SourceObjectDecisionRequest,
    SourceObjectReviewItem,
    SourceObjectReviewPage,
)


def unresolved_paths(project):
    source = project.source_file
    if source is None or project.import_status.mode != 'autocad_live':
        raise ValueError('Разбор объектов доступен для захвата AutoCAD')
    graph = project.source_geometry or project.geometry
    features = graph.feature_collection.get('features', []) if graph else []
    covered = {
        '/'.join((*member.get('instance_chain', []), member.get('handle', '')))
        for snapshot in (project.source_geometry, project.geometry) if snapshot is not None
        for f in snapshot.feature_collection.get('features', [])
        if f.get('geometry', {}).get('type') in {'Polygon', 'MultiPolygon'}
        for member in f.get('properties', {}).get('source_derived_from', [])
    }
    covered.update('/'.join((*item.source.instance_chain, item.source.handle))
                   for item in source.native_area_proposals if item.decision == 'accepted')
    covered.update('/'.join((*m.source.instance_chain, m.source.handle))
                   for group in source.area_groups for m in group.members)
    layers = {layer.source_name: layer for layer in project.layers}
    result = []
    for feature in features:
        props = feature.get('properties', {})
        layer = layers.get(props.get('source_layer'))
        if (not layer or layer.mapped_kind not in {'building', 'restricted', 'road'}
                or feature.get('geometry', {}).get('type') != 'LineString'
                or props.get('source_closed_path') is not False
                or props.get('source_context_only')
                or not props.get('source_geometry_content_sha256')
                or not props.get('source_handle')):
            continue
        route = '/'.join((*props.get('source_instance_chain', []), props['source_handle']))
        if route not in covered:
            result.append((route, feature))
    return result


def review_objects(project, *, layer=None, offset=0, limit=30):
    paths = unresolved_paths(project)
    if layer is not None:
        paths = [(route, feature) for route, feature in paths
                 if feature['properties']['source_layer'] == layer]
    decisions = {'/'.join((*item.source.instance_chain, item.source.handle)): item
                 for item in project.source_file.object_decisions}
    items = []
    for route, feature in paths[offset:offset + limit]:
        props = feature['properties']
        points = [tuple(point[:2]) for point in feature['geometry']['coordinates']]
        decision = decisions.get(route)
        items.append(SourceObjectReviewItem(
            route=route, source=SourceIdentity(handle=props['source_handle'],
                instance_chain=props.get('source_instance_chain', [])),
            layer=props['source_layer'], entity_type=props.get('entity_type', ''),
            geometry_sha256=props['source_geometry_content_sha256'], path=points,
            endpoint_distance_m=dist(points[0], points[-1]),
            interpretation=decision.interpretation if decision else 'area',
        ))
    return SourceObjectReviewPage(source_sha256=project.source_file.content_sha256,
                                 total=len(paths), offset=offset, items=items)


def decide_object(project, request: SourceObjectDecisionRequest):
    if project.import_status.editability == ImportEditability.READ_ONLY:
        raise ValueError('Этот источник доступен только для просмотра')
    if not project.source_file or project.source_file.content_sha256 != request.source_sha256:
        raise ValueError('Снимок AutoCAD изменился')
    route = '/'.join((*request.source.instance_chain, request.source.handle))
    feature = next((feature for key, feature in unresolved_paths(project) if key == route), None)
    if feature is None or feature['properties']['source_geometry_content_sha256'] != request.geometry_sha256:
        raise ValueError('Открытая линия изменилась или уже имеет подтверждённую область')
    previous = next((item for item in project.source_file.object_decisions if item.source == request.source), None)
    if previous is not None and previous.interpretation == request.interpretation:
        return project
    decisions = [item for item in project.source_file.object_decisions if item.source != request.source]
    if request.interpretation != 'area':
        decisions.append(SourceObjectDecision(**request.model_dump(exclude={'source_sha256'})))
    if decisions == project.source_file.object_decisions:
        return project
    graph = project.source_geometry or project.geometry
    was_resolved = previous is not None
    is_resolved = request.interpretation != 'area'
    props = feature['properties']
    layers = [item.model_copy(deep=True) for item in project.layers]
    # Only building paths were counted as missing interiors by the importer.
    # A readable road edge must not decrement an unrelated unreadable HATCH.
    if was_resolved != is_resolved and suggest_layer_kind(props['source_layer']) == 'building':
        source_layer = next(item for item in layers if item.source_name == props['source_layer'])
        counts = dict(source_layer.unsupported_geometry_types)
        kind = props['entity_type']
        current = counts.get(kind, 0)
        if is_resolved and current < 1:
            raise ValueError('Учёт неполной геометрии слоя изменился')
        counts[kind] = current + (-1 if is_resolved else 1)
        source_layer.unsupported_geometry_types = {key: count for key, count in counts.items() if count}
        source_layer.geometry_complete = not source_layer.unsupported_geometry_types
    resolved = {'/'.join((*item.source.instance_chain, item.source.handle)) for item in decisions}
    resolved.update('/'.join((*m.source.instance_chain, m.source.handle))
                    for group in project.source_file.area_groups for m in group.members)
    remaining = _unconfirmed_building_paths([
        item for item in graph.feature_collection.get('features', [])
        if '/'.join((*item.get('properties', {}).get('source_instance_chain', []),
                     item.get('properties', {}).get('source_handle', ''))) not in resolved
    ])
    warnings = [warning for warning in project.source_file.warnings
                if not warning.startswith('Линии зданий без подтверждённой площади')]
    if remaining:
        warnings.append(f'Линии зданий без подтверждённой площади ({sum(remaining.values())})')
    return project.model_copy(update={
        'layers': layers,
        'source_file': project.source_file.model_copy(update={'object_decisions': decisions, 'warnings': warnings}),
        'source_geometry': graph, 'geometry': None, 'map_ready': False,
        'site_area_m2': None, 'planning_area_m2': None, 'allowed_area_m2': None,
    })
