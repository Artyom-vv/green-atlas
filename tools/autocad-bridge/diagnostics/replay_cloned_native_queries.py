"""Read-only native-query parity across an explicit clone map, never a fallback.

Runs the existing native worker on independent source/archive copies. A valid
reply does not waive nonzero Core exit or qualify the capture for installation.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from capture_session_run import digest, dump


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    for name in ('capture', 'baseline', 'case', 'worker'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    capture = args.capture.resolve(strict=True)
    receipt = json.loads((capture / 'clone-receipt.json').read_bytes())
    if not receipt['clean'] or receipt['baseline_sha256'] != digest(args.baseline):
        raise ValueError('Comparison requires the exact clean-source baseline')
    mapping_file = capture / 'indexed-evaluation.json'
    mapping = json.loads(mapping_file.read_bytes())
    rows = mapping['source_instance_to_archive_instance']
    if not all(row.get('matched') for row in rows):
        raise ValueError('Unverified archive instance mapping')
    routes = {row['source_route']: row['archive_route'] for row in rows}
    if len(routes) != len(rows) or len(set(routes.values())) != len(rows):
        raise ValueError('Ambiguous source/archive identity')
    root = Path(tempfile.mkdtemp(prefix='ga-cloned-query-', dir='/private/tmp'))
    archive_baseline = {'package': str(capture / 'isolated'),
                        'source': str(capture / 'isolated/host.dwg'), 'files': []}
    for row in receipt['saved_files']:
        if Path(row['file']).parent != Path('isolated'):
            continue
        source = capture / row['file']
        if digest(source) != row['sha256']:
            raise ValueError('Archive file changed since capture')
        archive_baseline['files'].append({'source': str(source), 'sha256': row['sha256']})
    dump(root / 'archive-baseline.json', archive_baseline)
    original = json.loads(args.case.read_bytes())
    translated = {**original, 'targets': [
        {**target, 'route': routes[target['route']],
         'additional_routes': [routes[r] for r in target.get('additional_routes', [])]}
        for target in original['targets']]}
    dump(root / 'archive-case.json', translated)
    result = {'scope': 'selected native measurements across native clone mapping; not product acceptance',
              'root': str(root), 'capture': str(capture), 'mapping_sha256': digest(mapping_file),
              'case_sha256': digest(args.case), 'runs': {}}
    print(json.dumps({'root': str(root)}), flush=True)
    for label, baseline, case in (('source', args.baseline, args.case),
                                  ('archive', root / 'archive-baseline.json', root / 'archive-case.json')):
        completed = subprocess.run([
            sys.executable, str(Path(__file__).with_name('run_product_query_batch.py')),
            '--baseline-receipt', str(baseline), '--case', str(case), '--worker', str(args.worker),
            '--output', str(root / label)], check=False)
        result['runs'][label] = {'runner_exit': completed.returncode,
                                **json.loads((root / label / 'receipt.json').read_bytes())}
    source = result['runs']['source'].get('validated_reply')
    archive = result['runs']['archive'].get('validated_reply')
    differences = []
    measurements = 0
    if source and archive:
        for before, after in zip(source['objects'], archive['objects'], strict=True):
            for key in ('entity_type', 'layer', 'capability', 'interior_known', 'preparation_error'):
                if before[key] != after[key]:
                    differences.append({'route': before['route'], 'field': key,
                                        'source': before[key], 'archive': after[key]})
            for a, b in zip(before['answers'], after['answers'], strict=True):
                measurements += 1
                da, db = a['distance_units'], b['distance_units']
                same_distance = da == db or (da is not None and db is not None
                    and math.isclose(da, db, abs_tol=1e-8, rel_tol=1e-12))
                if not same_distance or {k: v for k, v in a.items() if k != 'distance_units'} != {
                        k: v for k, v in b.items() if k != 'distance_units'}:
                    differences.append({'route': before['route'], 'source': a, 'archive': b})
    result.update(measurements=measurements, differences=differences,
                  measurements_match=bool(source and archive) and not differences,
                  all_core_exit_zero=all(row.get('engine', {}).get('exit_code') == 0
                                        for row in result['runs'].values()),
                  source_files_unchanged=all(row['sources_unchanged'] for row in result['runs'].values()),
                  product_qualified=False)
    dump(root / 'comparison.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'runs'}, ensure_ascii=False), flush=True)
    return int(not (result['measurements_match'] and result['all_core_exit_zero']
                    and result['source_files_unchanged']))


if __name__ == '__main__':
    raise SystemExit(main())
