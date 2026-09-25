import json
from copy import deepcopy
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient
from test_cad_bridge_compiler import (
    near_closed_building_proposal_probe,
    probe_with_xref,
    valid_probe,
)

from app.cad_bridge.compiler import compile_live_document, compile_region_probe
from app.cad_bridge.provider import verify_cad_snapshot_integrity
from app.main import app


def live_bytes(probe=None):
    probe = probe or valid_probe()
    probe['capture_mode'] = 'live_document'
    probe['source'].update(database_modified_flags=32, live_database_matches_disk=False)
    return json.dumps(probe).encode()


def compile_live(content):
    return compile_live_document(content, autocad_version='2027.0.1', target='macos-arm64')


def live_area_probe():
    probe = near_closed_building_proposal_probe()
    probe['coverage'].append({
        'handle': 'C0', 'instance_chain': [], 'entity_type': 'AcDbRegion',
        'source_layer': 'Граница участка', 'layer': 'Граница участка',
        'status': 'native', 'method': 'AcBr loop traversal', 'reason': None,
    })
    probe['regions'].append({
        'handle': 'C0', 'instance_chain': [],
        'source_layer': 'Граница участка', 'layer': 'Граница участка',
        'status': 'native', 'error_status': None,
        'native_area_units2': 1500, 'native_perimeter_units': 160,
        'loops': [{
            'role': 'outer', 'sampled_max_deviation_units': 0,
            'coordinates': [[-10, -10, 0], [40, -10, 0], [40, 20, 0],
                            [-10, 20, 0], [-10, -10, 0]],
        }],
    })
    probe['summary']['regions'] += 1
    probe['summary']['resolved'] += 1
    probe['summary']['source_instances'] += 1
    probe['summary']['native'] += 1
    return probe


def test_native_area_requires_explicit_decision_and_reaches_calculation(monkeypatch):
    monkeypatch.setattr(
        'app.dxf_import.adapters.EzdxfReader.read',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('live area review must not use portable DXF')
        ),
    )
    client = TestClient(app)
    pid = client.post('/api/projects', json={'name': 'Native area review'}).json()['id']
    url = f'/api/projects/{pid}'
    captured = live_bytes(live_area_probe())
    response = client.post(url + '/source-autocad-live',
        data={'autocad_version': '2027.0.1', 'target': 'macos-arm64'},
        files={'file': ('document.json', captured, 'application/json')})
    assert response.status_code == 200, response.text
    opened = response.json()
    proposal = opened['source_file']['native_area_proposals'][0]
    assert proposal['decision'] == 'pending'
    assert proposal['area_m2'] == 100
    assert proposal['area_gap_entity_type'] == 'LWPOLYLINE'
    preview = client.get(url + '/source-native-area/preview', params={'proposal_id': proposal['id']})
    assert preview.status_code == 200, preview.text
    assert preview.json()['source_sha256'] == opened['source_file']['content_sha256']
    assert preview.json()['proposal_sha256'] == proposal['proposal_sha256']
    assert len(preview.json()['source_path']) >= 4
    assert preview.json()['proposed_rings']
    before = client.get(url, params={'include_geometry': 'true'}).json()
    assert not any(
        feature['properties'].get('source_area_proposal_id')
        for feature in before['geometry']['feature_collection']['features']
    )
    request = {
        'source_sha256': opened['source_file']['content_sha256'],
        'proposal_id': proposal['id'],
        'proposal_sha256': proposal['proposal_sha256'],
        'decision': 'accepted',
    }
    stale = client.post(url + '/source-native-area/decision',
        json={**request, 'proposal_sha256': '0' * 64})
    assert stale.status_code == 400
    assert client.get(url).json()['state_version'] == before['state_version']
    accepted = client.post(url + '/source-native-area/decision',
        headers={'If-Match': str(before['state_version'])}, json=request)
    assert accepted.status_code == 200, accepted.text
    after = client.get(url, params={'include_geometry': 'true'}).json()
    assert after['source_file']['native_area_proposals'][0]['decision'] == 'accepted'
    source_features = after['geometry']['feature_collection']['features']
    assert any(feature['properties'].get('source_area_proposal_id') == proposal['id']
               and feature['geometry']['type'] == 'Polygon'
               for feature in source_features)
    assert any(feature['properties'].get('source_handle') == 'BEEF'
               and feature['geometry']['type'] == 'LineString'
               for feature in source_features)
    assert next(layer for layer in after['layers'] if layer['source_name'] == 'Здания')['geometry_complete']
    mappings = [
        {'layer_id': layer['id'],
         'kind': 'site_border' if layer['source_name'] == 'Граница участка' else 'building',
         'confirmed': True}
        for layer in after['layers']
    ]
    mapped = client.put(url + '/layer-mappings', json={'mappings': mappings})
    assert mapped.status_code == 200, mapped.text
    started = client.post(url + '/operations/geometry')
    assert started.status_code == 202, started.text
    operation = client.get(url + '/operations/' + started.json()['id']).json()
    assert operation['status'] == 'completed', operation
    calculated = client.get(url, params={'include_geometry': 'true'}).json()
    assert calculated['allowed_area_m2'] is not None
    assert any(
        feature['properties'].get('kind') == 'forbidden'
        and 'building' in feature['properties'].get('rule_id', '')
        for feature in calculated['geometry']['feature_collection']['features']
    )
    rejected = client.post(url + '/source-native-area/decision',
        headers={'If-Match': str(calculated['state_version'])},
        json={**request, 'decision': 'rejected'})
    assert rejected.status_code == 200, rejected.text
    reverted = client.get(url, params={'include_geometry': 'true'}).json()
    assert reverted['source_file']['native_area_proposals'][0]['decision'] == 'rejected'
    assert not next(layer for layer in reverted['layers']
                    if layer['source_name'] == 'Здания')['geometry_complete']
    assert reverted['allowed_area_m2'] is None
    assert not any(feature['properties'].get('source_area_proposal_id')
                   for feature in reverted['geometry']['feature_collection']['features'])
    assert client.post(url + '/source-native-area/decision',
        headers={'If-Match': str(calculated['state_version'])}, json=request
    ).status_code in (409, 412)


def test_live_capture_hashes_geometry_payload_not_stale_disk_file():
    content = live_bytes()
    snapshot = compile_live(content)
    assert snapshot.source.sha256 == sha256(content).hexdigest()
    assert snapshot.source.live_capture.original_disk_sha256 == 'a' * 64
    assert snapshot.source.live_capture.database_modified_flags == 32
    assert len(snapshot.geometry) == 1
    verify_cad_snapshot_integrity(snapshot)
    with pytest.raises(ValueError, match='side-database'):
        compile_region_probe(json.loads(content), autocad_version='2027.0.1', target='macos-arm64')


def test_live_route_does_not_admit_disk_probe_as_live():
    with pytest.raises(ValueError, match='live_document'):
        compile_live(json.dumps(valid_probe()).encode())


def test_live_capture_cannot_masquerade_as_source_dxf():
    client = TestClient(app)
    pid = client.post('/api/projects', json={'name': 'Not a DXF'}).json()['id']
    content = live_bytes()
    response = client.post(f'/api/projects/{pid}/source-dxf', files={
        'file': ('source.dxf', content, 'application/dxf'),
        'cad_snapshot': ('snapshot.json', compile_live(content).model_dump_json(), 'application/json'),
    })
    assert response.status_code == 400
    assert client.get(f'/api/projects/{pid}').json()['source_file'] is None


def test_partial_live_source_can_be_reviewed_then_calculated_without_dxf(monkeypatch):
    def no_dxf(*args, **kwargs):
        raise AssertionError('live calculation must use captured native geometry')
    monkeypatch.setattr('app.dxf_import.adapters.EzdxfReader.read', no_dxf)
    client = TestClient(app)
    pid = client.post('/api/projects', json={'name': 'Partial live'}).json()['id']
    url = f'/api/projects/{pid}'
    opened = client.post(url + '/source-autocad-live',
        data={'autocad_version': '2027.0.1', 'target': 'macos-arm64'}, files={
        'file': ('document.json', live_bytes(), 'application/json'),
    }).json()
    source_hash = opened['source_file']['content_sha256']
    assert opened['source_file']['prepared_provenance'] is None
    assert not opened['source_file']['accept_partial_geometry']
    assert any(not layer['geometry_complete'] for layer in opened['layers'])
    before = client.get(url, params={'include_geometry': 'true'}).json()
    stale = client.post(url + '/source-partial-geometry/accept', json={'source_sha256': 'b' * 64})
    assert stale.status_code == 400
    assert client.get(url).json()['state_version'] == before['state_version']
    response = client.post(url + '/source-partial-geometry/accept',
        headers={'If-Match': str(before['state_version'])}, json={'source_sha256': source_hash})
    assert response.status_code == 200, response.text
    accepted = client.get(url, params={'include_geometry': 'true'}).json()
    assert accepted['source_file']['accept_partial_geometry']
    assert accepted['geometry'] == before['geometry']
    assert accepted['layers'] == before['layers']
    assert accepted['source_review'] == before['source_review']
    assert accepted['source_file']['prepared_provenance'] is None
    # The decision is idempotent; a stale tab still cannot change the source.
    assert client.post(url + '/source-partial-geometry/accept',
        json={'source_sha256': source_hash}).json()['state_version'] == accepted['state_version']
    mapping = client.put(url + '/layer-mappings', json={'mappings': [
        {'layer_id': layer['id'], 'kind': 'site_border', 'confirmed': True}
        for layer in accepted['layers']
    ]})
    assert mapping.status_code == 200, mapping.text
    started = client.post(url + '/operations/geometry')
    assert started.status_code == 202, started.text
    operation = client.get(url + '/operations/' + started.json()['id']).json()
    assert operation['status'] == 'completed', operation
    calculated = client.get(url, params={'include_geometry': 'true'}).json()
    assert calculated['source_review'] is None
    assert calculated['geometry']['calculation_scope'] == 'available_data'
    assert calculated['site_area_m2'] > 0
    assert not calculated['layers'][0]['geometry_complete']


def test_partial_geometry_review_respects_project_version():
    client = TestClient(app)
    pid = client.post('/api/projects', json={'name': 'Concurrent review'}).json()['id']
    url = f'/api/projects/{pid}'
    before = client.get(url).json()
    opened = client.post(url + '/source-autocad-live',
        data={'autocad_version': '2027.0.1', 'target': 'macos-arm64'}, files={
        'file': ('document.json', live_bytes(), 'application/json'),
    }).json()
    response = client.post(url + '/source-partial-geometry/accept',
        headers={'If-Match': str(before['state_version'])},
        json={'source_sha256': opened['source_file']['content_sha256']})
    assert response.status_code in (409, 412), response.text
    assert not client.get(url).json()['source_file']['accept_partial_geometry']


def test_loaded_xref_is_not_reopened_or_claimed_to_be_archived(tmp_path, monkeypatch):
    probe = probe_with_xref(tmp_path)
    probe['xref_dependencies'][0]['resolved_path'] = '/missing/no-longer-on-disk.dxf'
    def no_file_read(*args, **kwargs):
        raise AssertionError('live capture must not reread XREF files')
    monkeypatch.setattr('app.cad_bridge.compiler._file_sha256', no_file_read)
    snapshot = compile_live(live_bytes(probe))
    assert snapshot.dependencies is None
    assert snapshot.live_references[0].id == 'xref/AA'
    assert snapshot.coverage[-1].dependency_ids == ['xref/AA']
    verify_cad_snapshot_integrity(snapshot)


def test_two_live_underlays_keep_their_geometry_layers_and_instance_identity(monkeypatch):
    """Loaded topography/utilities must survive the complete import-to-map path."""
    probe = valid_probe()
    probe['plugin_version'] = '0.1.30'
    probe['xref_dependencies'] = []
    for parent, dependency, name, offset in (
        ('20', 'AA', 'Survey|Здания', 100),
        ('30', 'BB', 'Networks|Водопровод', 200),
    ):
        probe['xref_dependencies'].append({
            'record_handle': dependency, 'block_name': name.split('|')[0],
            'stored_path': name.split('|')[0] + '.dwg', 'status': 'resolved',
        })
        probe['coverage'].append({
            'handle': parent, 'instance_chain': [], 'entity_type': 'AcDbBlockReference',
            'layer': '0', 'status': 'context', 'method': 'traverse-xref-reference',
            'reason': 'loaded reference traversed in memory',
            'xref_dependency_id': 'xref/' + dependency,
        })
        geometry = deepcopy(probe['regions'][0])
        # The same local entity handle in two XREFs is not the same instance.
        geometry['instance_chain'] = [parent]
        for loop in geometry['loops']:
            for point in loop['coordinates']:
                point[0] += offset
        probe['regions'].append(geometry)
        probe['coverage'].append({
            'handle': geometry['handle'], 'instance_chain': [parent],
            'entity_type': 'AcDbRegion', 'layer': name,
            'status': 'native', 'method': 'AcBr loop traversal', 'reason': None,
        })
    probe['summary'].update(regions=3, resolved=3, paths=0, points=0, source_instances=7,
                            native=3, context=3, xref_block_references=2,
                            xref_dependency_records=2)
    def no_dxf(*args, **kwargs):
        raise AssertionError('loaded underlays must not require another DXF parse')
    monkeypatch.setattr('app.dxf_import.adapters.EzdxfReader.read', no_dxf)
    client = TestClient(app)
    pid = client.post('/api/projects', json={'name': 'Live underlays'}).json()['id']
    url = f'/api/projects/{pid}'
    response = client.post(url + '/source-autocad-live',
        data={'autocad_version': '2027.0.1', 'target': 'macos-arm64'},
        files={'file': ('underlays.autocad.json', live_bytes(probe), 'application/json')})
    assert response.status_code == 200, response.text
    project = client.get(url, params={'include_geometry': 'true'}).json()
    features = project['geometry']['feature_collection']['features']
    sources = {f['properties']['source_layer']: f for f in features}
    assert {'SITE', 'Survey|Здания', 'Networks|Водопровод'} <= sources.keys()
    survey, networks = sources['Survey|Здания'], sources['Networks|Водопровод']
    assert survey['id'] != networks['id']
    assert survey['properties']['source_instance_chain'] == ['20']
    assert networks['properties']['source_instance_chain'] == ['30']
    assert survey['geometry']['coordinates'][0][0][0] == 100
    assert networks['geometry']['coordinates'][0][0][0] == 200
    evidence = project['source_file']['cad_snapshot_provenance']
    assert len(evidence['live_references']) == 2
    assert evidence['dependencies'] == []
    assert project['source_review']['status'] == 'pending'


@pytest.mark.parametrize('fault', ['coverage', 'units', 'geometry'])
def test_live_input_still_validates_geometry(fault):
    probe = valid_probe()
    if fault == 'coverage':
        probe['coverage'].pop()
    elif fault == 'units':
        probe['source']['metres_per_unit'] = 0
    else:
        probe['regions'][0]['loops'][0]['coordinates'][1][0] = float('nan')
    with pytest.raises(ValueError):
        compile_live(live_bytes(probe))


def test_direct_project_opens_and_edits_without_any_dxf_read(monkeypatch):
    def no_dxf(*args, **kwargs):
        raise AssertionError('direct native import must not parse DXF')
    monkeypatch.setattr('app.dxf_import.adapters.EzdxfReader.read', no_dxf)
    client = TestClient(app)
    project_id = client.post('/api/projects', json={'name': 'Live document'}).json()['id']
    url = f'/api/projects/{project_id}'
    content = live_bytes()
    response = client.post(url + '/source-autocad-live',
        data={'autocad_version': '2027.0.1', 'target': 'macos-arm64'},
        files={'file': ('document.autocad.json', content, 'application/json')})
    assert response.status_code == 200, response.text
    project = response.json()
    assert project['map_ready']
    assert project['import_status']['mode'] == 'autocad_live'
    assert project['import_status']['editability'] == 'editable'
    assert project['source_review']['status'] == 'pending'
    assert project['source_file']['content_sha256'] == sha256(content).hexdigest()
    assert project['source_file']['cad_snapshot_provenance']['live_capture']['database_modified_flags'] == 32
    assert client.get(url + '/source-dxf/asset').status_code != 200
    assert client.get(url + '/source-dxf/download').status_code != 200
    exported = client.post(url + '/exports')
    assert exported.status_code == 400
    assert 'живого снимка' in exported.text
    zone = {'id': 'work', 'label': 'Участок', 'geometry': {
        'type': 'Polygon', 'coordinates': [[[1, 1], [9, 1], [9, 9], [1, 9], [1, 1]]]}}
    saved = client.put(url + '/planting-zones', json={'zones': [zone]})
    assert saved.status_code == 200, saved.text
    plan = client.post(url + '/plan/manual')
    assert plan.status_code == 200, plan.text
    added = client.post(url + '/plan/objects', json={'kind': 'tree', 'x': 5, 'y': 4})
    assert added.status_code == 200, added.text
    # Reimport cannot discard the source, working zone, or user's manual plan.
    repeated = client.post(url + '/source-autocad-live',
        data={'autocad_version': '2027.0.1', 'target': 'macos-arm64'},
        files={'file': ('document.autocad.json', content, 'application/json')})
    assert repeated.status_code == 400
    assert client.get(url).json()['plan']['objects']
