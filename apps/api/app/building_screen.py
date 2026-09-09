"""Building context and a validated proposal, independent of language models."""
from shapely.geometry import Point, shape, mapping
from shapely.ops import unary_union

from app.contracts import BuildingScreenRequest, BuildingScreenTargets, EvidenceAssessment, PlacementMaskRequest, PlanObject, RecommendationPreview
from app.planning.domain import required_spacing
from app.species.catalog import get_species, growth_forecasts


def screen_features(project, zone_ids):
    selected = set(zone_ids)
    zones = [z for z in project.planting_zones if z.id in selected]
    if not selected or selected - {z.id for z in zones}:
        raise ValueError('Выберите существующие рабочие участки.')
    scope = unary_union([shape(z.geometry) for z in zones]).buffer(40)
    result = []
    for f in project.geometry.feature_collection.get('features', []) if project.geometry else []:
        if f.get('properties', {}).get('kind') not in {'building', 'road'} or not f.get('geometry'):
            continue
        geometry = shape(f['geometry'])
        if not geometry.is_empty and geometry.intersects(scope):
            if f['properties']['kind'] == 'building' and geometry.geom_type not in {'Polygon', 'MultiPolygon'}:
                continue
            result.append(f)
    return result


def targets(project, zone_ids):
    features = screen_features(project, zone_ids)
    buildings = [shape(f['geometry']) for f in features if f['properties']['kind'] == 'building']
    return BuildingScreenTargets(geometry=mapping(unary_union(buildings)) if buildings else None,
                                 has_roads=any(f['properties']['kind'] == 'road' for f in features))


def generation_features(project, request):
    features = screen_features(project, request.zone_ids)
    if not project.plan or not project.plan.objects:
        return features
    radius = request.layout_radius_m or 1.6
    prototype = PlanObject(kind=request.plant_kind, x=0, y=0, radius=radius,
                          size_class=request.size_class, species_revision_id=request.species_revision_id)
    if request.species_revision_id:
        prototype.canopy_forecast, prototype.root_forecast = growth_forecasts(get_species(request.species_revision_id), request.size_class)
    # Existing planned trees constrain the search itself, not only its output.
    # The final validator remains authoritative; the extra millimetre avoids
    # placing rounded coordinates just inside a polygonal buffer boundary.
    occupied = unary_union([Point(p.x, p.y).buffer(required_spacing(prototype, p) + 0.01, quad_segs=32)
                            for p in project.plan.objects])
    features.append({'type': 'Feature', 'properties': {'kind': 'planned_plant_clearance'}, 'geometry': mapping(occupied)})
    return features


def preview(application, project_id: str, request: BuildingScreenRequest):
    project = application.get(project_id)
    context = targets(project, request.zone_ids)
    if not context.geometry:
        raise ValueError('Рядом с выбранными участками не найдены контуры зданий. Выберите другие участки.')
    result = application.preview_pattern(project_id, PlacementMaskRequest(
        mask_id='building_contour' if request.arrangement == 'contour' else 'building_screen', screen_side=request.screen_side,
        base_plan_version=request.base_plan_version, zone_ids=request.zone_ids,
        placement_mode='count' if request.max_sites else 'spacing', target_count=request.max_sites or 500,
        species_revision_id=request.species_revision_id, size_class=request.size_class,
        spacing_m=request.spacing_m, building_offset_m=request.building_offset_m,
        spacing_policy=request.spacing_policy,
        cluster_size=3, cluster_gap_m=24, edge_offset_m=0,
    ))
    return RecommendationPreview(arrangement='building_screen', target_geometry=context.geometry, profile='shade',
        change_set=result.change_set, skipped=result.skipped, data_gaps=result.unverified_data,
        evidence=EvidenceAssessment(spatial_constraints='partial', species_catalog='verified',
            note=('Посадки следуют контурам зданий.' if request.arrangement == 'contour' else 'Группы расположены рядом со зданиями.') + ' Проверены ограничения чертежа и рост посадок. Видимость фасадов из конкретных точек не рассчитывалась.'))
