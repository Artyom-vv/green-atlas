"""Compare complete normalization of local DXF fixtures against a Git revision.

No database or source writes. This checks correctness; one run per file is
deliberately not a performance acceptance benchmark.
"""
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps/api'))
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.capacity import SourceGeometryCapacity


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def main():
    source, output = map(Path, sys.argv[1:3])
    revision = sys.argv[3]
    output.mkdir(parents=True, exist_ok=False)
    baseline = output / 'baseline_adapter.py'
    baseline_source = Path(revision)
    baseline.write_bytes(baseline_source.read_bytes() if baseline_source.is_file() else subprocess.check_output(
        ['git', 'show', f'{revision}:apps/api/app/dxf_import/adapters.py'], cwd=ROOT))
    spec = importlib.util.spec_from_file_location('baseline_adapter', baseline)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = []
    for path in ([source] if source.is_file() else sorted(source.glob('*.dxf'))):
        content = path.read_bytes()
        record = dict(file=path.name, source_sha256=hashlib.sha256(content).hexdigest())
        capacity = SourceGeometryCapacity.process_bounded()
        for name, reader in [('baseline', module.EzdxfReader(capacity=capacity)), ('current', EzdxfReader(capacity=capacity))]:
            start = time.monotonic()
            result = reader.read_prepared_file(path).model_dump(mode='json')
            record[name] = dict(seconds=time.monotonic()-start,
                                full_result_sha256=digest(result),
                                geometry_sha256=digest(result['geometry']),
                                layers_sha256=digest(result['layers']),
                                features=len(result['geometry']['feature_collection']['features']),
                                incomplete_layers=[layer['source_name'] for layer in result['layers'] if not layer['geometry_complete']],
                                warnings=result['warnings'])
            del result
            gc.collect()
        record['equal'] = record['baseline']['full_result_sha256'] == record['current']['full_result_sha256']
        record['source_unchanged'] = hashlib.sha256(path.read_bytes()).hexdigest() == record['source_sha256']
        results.append(record)
        report = dict(revision=revision, scope='full normalized results; no project publication or speed gate', files=results)
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf8')
        print(json.dumps(record), flush=True)
    return 0 if results and all(r['equal'] and r['source_unchanged'] for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
