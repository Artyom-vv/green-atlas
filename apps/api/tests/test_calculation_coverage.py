"""One geometry ledger for search masks and the source passport."""

import pytest
from test_hybrid_search import feature, populate
from test_native_live_provider import setup as setup

from app.data_passport import build_data_passport
from app.dxf_import.layer_contracts import LayerKind
from app.native_query.live_inventory import InventoryObject


def test_passport_uses_current_areas_instead_of_old_import_flags(setup):
    engine, client, project = setup
    populate(project)
    project.layers[1].geometry_complete = False
    project.layers[1].unsupported_geometry_types = {'LWPOLYLINE': 1}
    passport = build_data_passport(project, geometry=engine)
    building = next(e for e in passport.entries if e.kind == 'building')
    assert building.status == 'verified'
    assert building.geometry_coverage.area_count == 1
    assert 'Здания' not in passport.incomplete_layers
    assert not client.calls  # No point grid or position query to render a passport
    assert engine._hybrid.projection.get(engine._objects[1]).geometries


def test_readable_lines_are_not_lost_objects_and_missing_projection_is_addressed(setup):
    engine, _, project = setup
    populate(project)
    engine.linear_layers = frozenset({'Дорога'})
    project.geometry.feature_collection['features'][2] = feature(
        'CC', 'Дорога', {'type': 'LineString', 'coordinates': [[20, 0], [25, 0]]},
    )
    project.layers[2].geometry_complete = False
    coverage = engine.source_coverage(project)
    assert coverage.layers['Дорога'].linear_count == 1
    assert not coverage.layers['Дорога'].unresolved
    project.geometry.feature_collection['features'].pop(1)
    project.geometry_version += 1
    coverage = engine.source_coverage(project)
    assert coverage.layers['Здания'].unresolved[0].routes == ['BB']
    assert coverage.layers['Здания'].unresolved[0].reason == 'projection_missing'
    passport = build_data_passport(project, geometry=engine)
    assert passport.incomplete_layers == ['Здания']


def test_confirmed_exclusion_is_not_an_unresolved_class_or_geometry_gap(setup):
    engine, _, project = setup
    populate(project)
    project.layers[1].suggested_kind = LayerKind.UTILITY
    project.layers[1].mapped_kind = LayerKind.UTILITY
    ignored = project.layers[2]
    ignored.suggested_kind = LayerKind.UTILITY
    ignored.mapped_kind = LayerKind.IGNORE
    ignored.mapping_confirmed = True
    ignored.geometry_complete = False
    passport = build_data_passport(project, geometry=engine)
    utility = next(e for e in passport.entries if e.kind == 'utility')
    assert utility.status == 'verified'
    assert ignored.source_name in passport.excluded_layers  # Decision is still auditable
    assert ignored.source_name not in passport.incomplete_layers
    assert not any('сети покрыт' in gap or 'Исключённые физические' in gap for gap in passport.gaps)
    ignored.mapping_confirmed = False
    assert next(e for e in build_data_passport(project, geometry=engine).entries
                if e.kind == 'utility').status == 'partial'


def test_reference_failure_is_not_hidden_by_readable_objects_in_the_same_layer(setup):
    engine, _, project = setup
    populate(project)
    engine.inventory = engine.inventory.model_copy(update={'objects': (*engine.inventory.objects,
        InventoryObject(route='DD', layer='Здания', entity_type='AcDbBlockReference',
                        context=False, bounds=None, curve=False, error='external reference unavailable'))})
    building = engine.source_coverage(project).layers['Здания']
    assert building.area_count == 1
    assert building.unresolved[0].routes == ['DD']
    assert next(e for e in build_data_passport(project, geometry=engine).entries
                if e.kind == 'building').status == 'partial'


def test_context_only_blocks_do_not_need_a_fake_area(setup):
    engine, _, project = setup
    populate(project)
    road = engine.inventory.objects[2].model_copy(update={'context': True, 'curve': False})
    engine.inventory = engine.inventory.model_copy(update={'objects': (*engine.inventory.objects[:2], road)})
    project.geometry.feature_collection['features'].pop()
    entry = next(e for e in build_data_passport(project, geometry=engine).entries if e.kind == 'road')
    assert entry.geometry_coverage.context_count == 1
    assert not entry.geometry_coverage.unresolved
    assert not entry.used_in_calculation


def test_stale_capture_invalidates_cached_coverage(setup):
    import pytest
    engine, client, project = setup
    populate(project)
    engine.source_coverage(project)
    client.stale = True
    with pytest.raises(ValueError, match='изменён'):
        engine.source_coverage(project)
    assert engine._coverage is None


@pytest.mark.parametrize('category', ['geodetic_marker', 'pole'])
def test_symbol_endpoint_cycles_keep_source_curves_and_existing_areas(setup, category):
    from app.dxf_import.layer_categories import LayerCategory
    from app.native_query.live_inventory import InventoryGroup

    engine, client, project = setup
    populate(project)
    layer = project.layers[1]
    layer.category = LayerCategory(category)
    layer.mapped_kind = LayerKind.RESTRICTED
    # Two coincident arcs are a cycle in an endpoint graph, not an area.
    duplicate = engine.inventory.objects[1].model_copy(update={'route': 'DD'})
    polygon = engine.inventory.objects[1].model_copy(update={'route': 'EE'})
    engine.inventory = engine.inventory.model_copy(update={
        'objects': (*engine.inventory.objects, duplicate, polygon),
        'groups': (InventoryGroup(routes=('BB', 'DD'), error=''),),
    })
    line = {'type': 'LineString', 'coordinates': [[0, 0], [1, 1], [2, 0]]}
    features = project.geometry.feature_collection['features']
    existing_area = features[1]['geometry']
    features[1] = feature('BB', layer.source_name, line)
    features.extend([feature('DD', layer.source_name, line),
                     feature('EE', layer.source_name, existing_area)])
    coverage = engine.source_coverage(project).layers[layer.source_name]
    assert not coverage.unresolved
    assert coverage.linear_count == 2
    assert coverage.area_count == 1  # Do not downgrade an existing source polygon.
    assert {item.routes for item in engine._objects if item.layer == layer.source_name} == {
        ('BB',), ('DD',), ('EE',),
    }
    assert not client.calls
