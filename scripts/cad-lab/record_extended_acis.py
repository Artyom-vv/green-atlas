"""Archive measured lab evidence, not runtime/vendor modifications."""
import hashlib
import json
import math
from pathlib import Path
import shutil

from ezdxf.acis import sab

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.runtime/cad-extended-20260916'
OUT = ROOT / 'docs/implementation/2026-09-16-extended-cad-gate'
OUT.mkdir(exist_ok=True)


def read(name):
    return json.loads((LAB / name).read_text())


corpus = read('corpus-results.json')
upgraded = read('oda-cases-results.json')
torus = next(row['result'] for row in upgraded if row['name'] == 'oda-torus_r2010-9C')
raw = sab.parse_sab((LAB / 'oda-inputs/torus_r2010-9C.sab').read_bytes())
transform = next(e for e in raw.entities if e.name == 'transform')
transform_tokens = [str(t.value) for t in transform.data]
assert any('128 135 0' in value for value in transform_tokens)
actual_center = [(torus['bounds'][i] + torus['bounds'][i+3])/2 for i in range(3)]
report = {
    'scope': 'root DWG without four XREFs; isolated corpus and normalization, no production changes',
    'universal_acis_gate': 'rejected: proven transform fidelity failures',
    'full_root_calculation_gate': 'blocked: large block not expanded; REGION absent from normalized features',
    'corpus': corpus,
    'oda_upgraded_and_metric_retry': upgraded,
    'torus_translation_check': {
        'source_transform_tokens': transform_tokens,
        'expected_center': [128, 135, 0],
        'actual_bounds_center': actual_center,
        'area_expected': 4*math.pi**2*32*10,
        'area_actual': torus['area'],
        'translation_preserved': False,
        'note': 'Periodic seam wire count and conservative OCC bounds are not themselves proof of corruption.',
    },
    'insert_unit_controls': read('insert-unit-controls.json'),
    'large_block': read('block-profile.json'),
    'inventory': read('full-instances/inventory.json'),
    'normalization_default': read('full-normalization.json'),
    'normalization_diagnostic_budget': read('full-normalization-expanded.json'),
    'measurements': {name: read(name+'-process.json') for name in [
        'full-conversion', 'full-normalization', 'full-normalization-expanded',
        'block-profile-retry', 'inventory-instances', 'oda-upgrade-solids', 'oda-upgrade-torus',
    ]},
    'caveats': [
        'Generated transform controls supplement but do not replace the real drawing.',
        'Structural PASS is not geometric fidelity acceptance.',
        'Dice first attempt failed in the measurement harness on a degenerate edge; retry supersedes it.',
        'First block profiler failed due to a wrong constant import in the harness; retry supersedes it.',
        'A6 source-loop vs output-wire mismatch needs periodic-surface topology review.',
        'INSERT tests use LWPOLYLINE, not end-to-end inserted ACIS.',
        'Process tree RSS is sampled, not a hard OS memory limit; runs are not cold benchmarks.',
        'No Windows/Linux execution, complete XREF assembly or planting calculation acceptance.',
    ],
}
assert len(corpus) == 17 and len(upgraded) == 6
assert all(case['passed'] for case in report['insert_unit_controls'])
assert report['large_block'][0]['direct_entities'] == 506506
assert report['normalization_diagnostic_budget']['features'] == 33720
assert actual_center == [0, 0, 0]

# Keep logs and source manifests outside ignored .runtime. Do not bundle third-party
# fixture geometry: upstream commits/paths and payload hashes identify it.
for path in sorted(LAB.glob('*-process.log')):
    shutil.copyfile(path, OUT / path.name)
for name in ['cases.json', 'oda-cases.json', 'block-profile-process.json']:
    shutil.copyfile(LAB / name, OUT / name)
with (OUT / 'results.json').open('x') as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
artifacts = []
for path in sorted(OUT.iterdir()):
    if path.is_file() and path.name != 'artifact-sha256.json':
        artifacts.append({'file': path.name, 'bytes': path.stat().st_size,
                          'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
with (OUT / 'artifact-sha256.json').open('x') as f:
    json.dump(artifacts, f, indent=2)
print(json.dumps({'corpus': len(corpus), 'retries': len(upgraded),
                  'large_block': report['large_block'], 'output': str(OUT)}, ensure_ascii=False))
