"""Persist ROI experiment measurements, including failed strict metadata checks."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT/'.runtime/cad-roi-20260916'
OUT = ROOT/'docs/implementation/2026-09-16-roi-extraction'
OUT.mkdir(exist_ok=True)


def read(path):
    return json.loads(path.read_text())


extraction = read(LAB/'extraction.json')
raw_changes = extraction.pop('changed_checked_attributes')
assert len(raw_changes) == 5242
assert all(c['before']['type'] == 'LWPOLYLINE' and
           c['before']['attrs'].get('elevation') == '0.0' and
           {k:v for k,v in c['before']['attrs'].items() if k != 'elevation'} == c['after']['attrs']
           for c in raw_changes)
extraction['initial_strict_attribute_differences'] = {
    'count': len(raw_changes), 'cause': 'explicit default LWPOLYLINE elevation=0 omitted on serialization',
    'example': raw_changes[0], 'superseded_by': 'verification-final.json',
}
verification = read(LAB/'verification-final.json')
assert verification['extraction_preservation_passed']
assert not verification['all_selected_entities_normalized']
report = dict(scope='real embedded block only; not a complete site or XREF assembly',
              extraction=extraction, verification=verification,
              normalization=read(LAB/'normalization.json'),
              wrapped_control=read(LAB/'wrapped-control.json'),
              processes={})
for name in ('extraction','normalization','verification','verification-defaults','verification-final','wrapped'):
    stem = 'cad-roi-'+name+'-process'
    report['processes'][name] = read(ROOT/'.runtime'/(stem+'.json'))
    shutil.copyfile(ROOT/'.runtime'/(stem+'.log'), OUT/(stem+'.log'))
for name in ('verification.json','verification-defaults.json'):
    shutil.copyfile(LAB/name, OUT/name)
with (OUT/'results.json').open('x') as f:
    json.dump(report, f, indent=2, ensure_ascii=False)
print(json.dumps(dict(extraction_passed=True, normalized_missing=len(verification['normalized_missing']),
                      wrapped_blocked=report['wrapped_control']['blocking_features'], output=str(OUT))))
