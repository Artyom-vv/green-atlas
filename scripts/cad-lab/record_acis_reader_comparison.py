"""Collect measured ACIS lab evidence; no production changes."""
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.runtime/cad-comparison-20260916'
OUT = ROOT / 'docs/implementation/2026-09-16-acis-reader-comparison'
OUT.mkdir(exist_ok=False)


def load(path):
    return json.loads(path.read_text())


def git(*args):
    return subprocess.check_output(['git', '-C', str(LAB / 'InventorLoader'), *args], text=True).strip()


golden = load(ROOT / 'docs/implementation/2026-09-15-official-implementation/cad/indexed-patches/active-index-audit.json')
expected = {x['handle']: x['sha256'] for x in golden['modeler_records']}
report = dict(scope='four original planar REGION SAB, not full CAD fidelity',
              inventorloader_commit=git('rev-parse', 'HEAD'),
              inventorloader_dirty=git('status', '--porcelain'),
              freecad_dmg_sha256=hashlib.file_digest((LAB / 'freecad-arm64.dmg').open('rb'), 'sha256').hexdigest(),
              runs=[])
assert not report['inventorloader_dirty']
for name in ['acis-native-1', 'acis-native-2', 'acis-step-1', 'acis-step-2', 'acis-step-3']:
    run = dict(name=name, process=load(LAB / (name + '-process.json')),
               probe=load(LAB / name / 'results.json'))
    check = LAB / name / 'reference-check.json'
    if check.exists():
        run['geometry_check'] = load(check)
    for case in run['probe']['cases']:
        assert expected[case['handle']] == case['sha256']
    run['accepted'] = (run['process']['status'] == 'completed' and
                       run.get('geometry_check', {}).get('status') == 'passed')
    shutil.copyfile(LAB / (name + '-process.log'), OUT / (name + '.log'))
    report['runs'].append(run)
step_runs = [r for r in report['runs'] if r['name'].startswith('acis-step')]
report['step_all_passed'] = len(step_runs) == 3 and all(r['accepted'] for r in step_runs)
report['step_median_seconds'] = statistics.median(r['process']['elapsed_seconds'] for r in step_runs)
report['step_max_sampled_rss_mib'] = max(r['process']['peak_tree_rss_bytes'] for r in step_runs) / 1024**2
report['artifacts'] = []
for case in step_runs[0]['probe']['cases']:
    handle = case['handle']
    for src in [Path(case['step']), LAB / 'acis-step-1' / (handle + '-StepShape.brep')]:
        dst = OUT / (handle + src.suffix)
        shutil.copyfile(src, dst)
        report['artifacts'].append(dict(path=dst.name, sha256=hashlib.sha256(dst.read_bytes()).hexdigest()))
with (OUT / 'results.json').open('x') as stream:
    json.dump(report, stream, indent=2, ensure_ascii=False)
    stream.write('\n')
print(json.dumps({k: v for k, v in report.items() if k not in ('runs', 'artifacts')}))
