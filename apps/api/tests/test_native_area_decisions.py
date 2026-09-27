
from fastapi.testclient import TestClient
from test_autocad_live_import import live_area_probe, live_bytes

from app.composition import get_application
from app.main import app
from app.native_query.live_inventory import LiveInventory, query_objects
from app.planning.contracts import Plan, PlanObject
from app.planting_zones.contracts import PlantingZoneAssignment


def test_post_plan_decisions_preserve_work_and_count_only_real_transitions():
    client = TestClient(app)
    pid = client.post('/api/projects', json={'name': 'Review with saved plants'}).json()['id']
    url = f'/api/projects/{pid}'
    response = client.post(url + '/source-autocad-live',
        data={'autocad_version': '2027.0.1', 'target': 'macos-arm64'},
        files={'file': ('capture.json', live_bytes(live_area_probe()), 'application/json')})
    assert response.status_code == 200, response.text
    application = get_application()
    project = application.repository.get(pid)
    project.plan = Plan(objects=[PlanObject(kind='shrub', x=20, y=0, radius=0.5)])
    project.planting_zones = [PlantingZoneAssignment(id='zone', label='Work', geometry={
        'type': 'Polygon', 'coordinates': [[[15, -5], [25, -5], [25, 5], [15, 5], [15, -5]]],
    })]
    project = application.repository.save(project)
    plan = project.plan.model_dump()
    zones = [zone.model_dump() for zone in project.planting_zones]
    record = project.source_file.native_area_proposals[0]
    payload = {'source_sha256': project.source_file.content_sha256,
               'proposal_id': record.id, 'proposal_sha256': record.proposal_sha256}
    before = next(layer for layer in project.layers if layer.source_name == record.layer)
    assert before.unsupported_geometry_types['LWPOLYLINE'] == 1
    for decision, expected in [('rejected', 1), ('accepted', 0), ('rejected', 1), ('rejected', 1), ('accepted', 0)]:
        result = client.post(url + '/source-native-area/decision', json={**payload, 'decision': decision})
        assert result.status_code == 200, result.text
        changed = result.json()
        assert changed['plan'] == plan
        assert changed['planting_zones'] == zones
        layer = next(item for item in changed['layers'] if item['source_name'] == record.layer)
        assert layer['unsupported_geometry_types'].get('LWPOLYLINE', 0) == expected
        assert changed['source_review'] is not None
        assert changed['geometry_version'] > project.geometry_version

    recognition = client.get(url + '/source-layer-review')
    assert recognition.status_code == 200, recognition.text
    assert len(recognition.json()['categories']) == 46
    mappings = [{'layer_id': item['id'], 'kind': item['mapped_kind'], 'confirmed': True,
                 **({'category': 'pavilion'} if item['source_name'] == record.layer else {})}
                for item in changed['layers']]
    result = client.put(url + '/layer-mappings', json={'mappings': mappings})
    assert result.status_code == 200, result.text
    assert result.json()['plan'] == plan
    assert result.json()['planting_zones'] == zones
    assert result.json()['source_review'] is not None
    assert result.json()['allowed_area_m2'] is None
    assert next(item for item in result.json()['layers'] if item['source_name'] == record.layer)['category'] == 'pavilion'

    for item in mappings:
        if item.get('category') == 'pavilion':
            item.update(kind='ignore', category='interior_detail', confirmed=False)
    result = client.put(url + '/layer-mappings', json={'mappings': mappings})
    assert result.status_code == 200, result.text
    reviewed = result.json()
    assert reviewed['plan'] == plan
    assert reviewed['planting_zones'] == zones
    layer = next(item for item in reviewed['layers'] if item['source_name'] == record.layer)
    assert layer['category'] == 'interior_detail'
    assert layer['mapping_confirmed'] is False
    assert reviewed['source_file']['native_area_proposals'][0]['decision'] == 'accepted'


def test_reviewed_closure_does_not_drop_remaining_group_interior():
    inventory = LiveInventory.model_validate({'schema': 'green-atlas.live-inventory/1',
        'objects': [dict(route=route, layer='Здания', entity_type='AcDbPolyline',
                         context=False, bounds=[0, 0, 10, 10], curve=True, error='')
                    for route in ['AA', 'BB', 'CC']],
        'groups': [{'routes': ['AA', 'BB', 'CC'], 'error': 'open native endpoint chain'}]})
    items = query_objects(inventory, {'Здания'}, frozenset({'AA'}))
    closed = next(item for item in items if item.routes == ('AA',))
    assert closed.target(area=True).capability == 'closed_area'
    assert closed.target(area=False).capability == 'curve'
    assert closed.bounds is None
    remainder = next(item for item in items if item.routes == ('BB', 'CC'))
    assert remainder.error == 'open native endpoint chain'
    assert remainder.bounds == (0, 0, 10, 10)
    assert remainder.curve_routes == ('BB', 'CC')
