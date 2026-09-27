"""Archive bounded native query evidence and compare fresh-process answers.

This validates experimental consistency, not the completeness of a street.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
from pathlib import Path

TOLERANCE_UNITS = 1e-8


def compare(left: dict, right: dict) -> dict:
    differences = []
    delta = 0.0
    count = 0
    if left['units_code'] != right['units_code']:
        differences.append('units_code')
    a_cases = {case['name']: case for case in left['cases']}
    b_cases = {case['name']: case for case in right['cases']}
    if a_cases.keys() != b_cases.keys():
        differences.append('case_names')
    for name in sorted(a_cases.keys() & b_cases.keys()):
        a, b = a_cases[name], b_cases[name]
        if a.get('error') != b.get('error'):
            differences.append(f'{name}:native_error')
        if len(a.get('answers', [])) != len(b.get('answers', [])):
            differences.append(f'{name}:answer_count')
            continue
        for i, (x, y) in enumerate(zip(a.get('answers', []), b.get('answers', []), strict=True)):
            count += 1
            for key in ('label', 'expected', 'status', 'membership', 'container',
                        'distance_complete', 'decision'):
                if x[key] != y[key]:
                    differences.append(f'{name}:{i}:{key}')
            if max(abs(p - q) for p, q in zip(x['point'], y['point'], strict=True)) > TOLERANCE_UNITS:
                differences.append(f'{name}:{i}:query_point')
            if x['distance_complete'] and y['distance_complete']:
                change = abs(x['distance_units'] - y['distance_units'])
                delta = max(delta, change)
                if change > TOLERANCE_UNITS:
                    differences.append(f'{name}:{i}:distance')
    return {'answer_pairs': count, 'differences': differences,
            'max_distance_delta_units': delta, 'equivalent': not differences}


def summarize(report: dict) -> dict:
    rows = []
    for case in report['cases']:
        row = {key: case[key] for key in ('name', 'handle', 'chain', 'method', 'error',
               'native_edges', 'exact_gelib_curves', 'prepare_ms', 'query_count', 'repeats',
               'repeat_stable', 'controls_passed', 'controls_failed', 'unknown',
               'instance_definition_membership_mismatches', 'gelib_mismatches',
               'gelib_max_distance_delta') if key in case}
        if 'batch_ms' in case:
            row['baseline_median_ms'] = statistics.median(case['batch_ms'])
        if 'gelib_batch_ms' in case:
            row['gelib_median_ms'] = statistics.median(case['gelib_batch_ms'])
            row['speed_ratio'] = row['baseline_median_ms'] / row['gelib_median_ms']
        rows.append(row)
    return {'source_sha256': report['source_sha256'], 'units_code': report['units_code'],
            'source_unchanged': report['source_unchanged'],
            'database_load_ms': report['native_database_load_ms'],
            'process_peak_rss_bytes_macos': report['process_peak_rss_bytes_macos'], 'cases': rows}


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--run', action='append', required=True, help='unique-label=existing-run-directory')
    parser.add_argument('--compare', action='append', default=[], help='left-label:right-label')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error('Output must be empty')
    reports = {}
    for spec in args.run:
        label, directory = spec.split('=', 1)
        if not re.fullmatch(r'[a-z0-9-]+', label) or label in reports:
            parser.error('Labels must be unique lowercase letters/digits/hyphens')
        root = Path(directory)
        reports[label] = json.loads((root / 'native.json').read_text())
        dest = args.output / label
        dest.mkdir()
        for filename in ('native.json', 'receipt.json', 'cases.json', 'request.txt', 'query.scr', 'core.log'):
            shutil.copy2(root / filename, dest / filename)
    result = {'scope': 'Selected source objects only; not complete street or product acceptance',
              'runs': {label: summarize(report) for label, report in reports.items()}, 'comparisons': {}}
    for pair in args.compare:
        left, right = pair.split(':', 1)
        result['comparisons'][pair] = compare(reports[left], reports[right])
    (args.output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
