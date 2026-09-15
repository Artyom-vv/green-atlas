"""Create a labelled beta example through HTTPS; retain it for user inspection."""
import argparse
import concurrent.futures
import datetime
import hashlib
import io
import json
from pathlib import Path
import re
import time
import zipfile

import httpx

parser = argparse.ArgumentParser()
parser.add_argument('--credentials', type=Path, required=True)
parser.add_argument('--fixture', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
credentials = json.loads(args.credentials.read_text())
base = credentials['url']
client = httpx.Client(base_url=base, auth=(credentials['username'], credentials['password']), trust_env=False, timeout=120)
report = {'url': base, 'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'checks': []}


def request(method, path, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != expected:
        raise RuntimeError(f'{method} {path}: {response.status_code} {response.text[:400]}')
    return response


for path in ['/', '/api/health', '/api/projects']:
    response = httpx.get(base + path, trust_env=False, timeout=30)
    assert response.status_code == 401
report['checks'].append('unauthenticated website and API blocked')
html = request('GET', '/').text
for asset in re.findall(r'(?:src|href)="(/assets/[^"\s]+)"', html):
    request('GET', asset)
request('GET', '/api/health')
assert request('GET', '/api/planning-assistant/status').json()['available'] is False
report['checks'].append('HTML, entry assets, health, intentionally disabled agent')
project = request('POST', '/api/projects', expected=201, json={'name': 'Демо — проверка сервера 11 сентября'}).json()
pid = project['id']
report['project_id'] = pid
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
prefix = '/api/projects/' + pid
source = args.fixture.read_bytes()
project = request('POST', prefix + '/source-dxf', files={'file': ('site.dxf', source, 'application/dxf')}).json()
mappings = [{'layer_id': layer['id'], 'kind': layer['suggested_kind'], 'visible': True} for layer in project['layers']]
request('PUT', prefix + '/layer-mappings', json={'mappings': mappings})
operation = request('POST', prefix + '/operations/geometry', expected=202).json()
for attempt in range(60):
    operation = request('GET', prefix + '/operations/' + operation['id']).json()
    if operation['status'] == 'completed':
        break
    if operation['status'] in {'failed', 'cancelled'}:
        raise RuntimeError(str(operation))
    time.sleep(1)
assert operation['status'] == 'completed'
coordinates = [[12, 12], [60, 12], [60, 35], [12, 35], [12, 12]]
request('PUT', prefix + '/planting-zones', json={'zones': [{'id': 'demo-area', 'label': 'Демонстрационный участок', 'geometry': {'type': 'Polygon', 'coordinates': [coordinates]}}]})
request('POST', prefix + '/plan/manual')
request('POST', prefix + '/plan/objects', json={'kind': 'tree', 'x': 20, 'y': 20, 'species_revision_id': 'tilia-cordata@2026-08-28.1', 'size_class': 'standard', 'spacing_policy': 'canopy'})
release = request('POST', prefix + '/releases', json={'mode': 'draft', 'scene_horizon': 0}).json()
bundle = next(item for item in release['artifacts'] if item['kind'] == 'bundle')
content = request('GET', bundle['download_url']).content
with zipfile.ZipFile(io.BytesIO(content)) as archive:
    assert archive.testzip() is None
    report['bundle_members'] = archive.namelist()
report.update(release_id=release['id'], bundle_bytes=len(content), bundle_sha256=hashlib.sha256(content).hexdigest())
report['checks'].append('HTTPS create, DXF upload, mappings, geometry, zone, planting, draft ZIP with valid CRC')
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
    statuses = list(pool.map(lambda _: request('GET', prefix).status_code, range(5)))
assert statuses == [200] * 5
report['checks'].append('five concurrent project reads (not heavy-import load certification)')
project = request('GET', prefix + '?include_geometry=true').json()
report['project_sha256'] = hashlib.sha256(json.dumps(project, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
report['state_version'] = project['state_version']
report['geometry_version'] = project['geometry_version']
report['plan_version'] = project['plan']['version']
report['status'] = 'passed'
args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
