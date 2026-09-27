"""Archive small native XREF receipts and compare query answers, never CAD data.

The baseline is a prior measured zone, not a synthetic expected output. This is
parity evidence only; independent finite-LINE checks live in the native report.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

from run_direct_queries import digest


def differences(left, right, path='') -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        result = []
        for key in left.keys() | right.keys():
            if key not in left or key not in right:
                result.append(path + '/' + key + ':missing')
            else:
                result.extend(differences(left[key], right[key], path + '/' + key))
        return result
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [path + ':length']
        return [item for index, (a, b) in enumerate(zip(left, right, strict=True))
                for item in differences(a, b, f'{path}/{index}')]
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)):
        return [] if math.isclose(left, right, abs_tol=1e-8, rel_tol=0) else [path + ':number']
    return [] if left == right else [path + ':value']


def without_answers(value):
    """Keep failures concise; full answers remain in the hashed native report."""
    if isinstance(value, dict):
        return {k: without_answers(v) for k, v in value.items() if k != 'answers'}
    if isinstance(value, list):
        return [without_answers(v) for v in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--run', action='append', required=True, help='label=/absolute/experiment/root')
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare-pair', action='append', default=[], help='left-label=right-label, measured fields only')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        parser.error('Evidence destination must be empty')
    baseline = json.loads(args.baseline.read_text())
    summary = {'baseline': str(args.baseline), 'baseline_sha256': digest(args.baseline), 'runs': {}}
    names = ['receipt.json', 'native.json', 'before.json', 'zone.json', 'repath.json',
             'zone-case.json', 'xref-map.json', 'request.txt', 'query.scr', 'core.log',
             'zone-request.txt', 'before-request.txt', 'repath-request.txt',
             'area-cases.json', 'native.json.area-progress.json',
             'window-case.json',
             'convert/core.log', 'convert/convert.scr', 'convert/engine.json']
    native_reports = {}
    for value in args.run:
        label, raw = value.split('=', 1)
        if '/' in label or label in {'.', '..'}:
            parser.error('Labels must be single directory names')
        root = Path(raw).resolve(strict=True)
        target = args.output / label
        target.mkdir()
        hashes = {}
        for name in names:
            source = root / name
            if not source.is_file():
                continue
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            hashes[name] = digest(destination)
        row = {'original_run': str(root), 'archived_sha256': hashes}
        if (root / 'receipt.json').is_file():
            receipt = json.loads((root / 'receipt.json').read_text())
            row['inspection_completed'] = receipt['inspection_completed']
            row['originals_unchanged'] = receipt['all_sources_and_staged_unchanged']
            row['error'] = receipt.get('error')
        if (root / 'native.json').is_file():
            report = json.loads((root / 'native.json').read_text())
            native_reports[label] = report
            inventory = report['inventory']
            controls = inventory.get('native_line_controls', [])
            row.update(visited_instances=inventory['visited_instances'], issues=inventory['issues'],
                       xref_definitions=[{key: record[key] for key in ('handle', 'name', 'status', 'database_present')}
                                         for record in report['records']],
                       line_controls=len(controls), line_controls_passed=sum(c.get('passed', False) for c in controls),
                       native_line_queries=sum(c.get('queries', 0) for c in controls),
                       tested_transform_kinds=sorted({c['transform_kind'] for c in controls}),
                       max_distance_delta=max((c.get('max_distance_delta', 0) for c in controls), default=None),
                       clips=[c for c in inventory.get('block_clip_states', []) if c['state'].get('filter_present')])
            areas = inventory.get('native_area_controls', [])
            if areas:
                row['native_areas'] = {
                    'cases': len(areas), 'passed': sum(c.get('passed', False) for c in areas),
                    'points': sum(c.get('query_points', 0) for c in areas),
                    'transform_kinds': sorted({c['transform_kind'] for c in areas if 'transform_kind' in c}),
                    'failures': [without_answers(c) for c in areas if not c.get('passed')],
                }
                affine = [c['affine_probe'] for c in areas if 'affine_probe' in c]
                if affine:
                    row['affine_probes'] = {
                        'cases': len(affine), 'passed': sum(c.get('passed', False) for c in affine),
                        'points': sum(c.get('query_points', 0) for c in affine),
                        'controls_passed': sum(c.get('controls_passed', 0) for c in affine),
                        'controls_failed': sum(c.get('controls_failed', 0) for c in affine),
                        'unknown': sum(c.get('unknown', 0) for c in affine),
                        'baseline_mismatches': sum(c.get('baseline_mismatches', 0) for c in affine),
                        'repeat_mismatches': sum(c.get('repeat_mismatches', 0) for c in affine),
                        'oracle_cases': sum(c.get('independent_straight_edge_oracle', False) for c in affine),
                        'oracle_failures': sum(c.get('oracle_failures', 0) for c in affine),
                        'max_oracle_delta': max((c.get('max_oracle_delta', 0) for c in affine), default=0),
                        'failures': [{'route': c['route'], **{k: v for k, v in c['affine_probe'].items() if k != 'answers'}}
                                     for c in areas if 'affine_probe' in c and not c['affine_probe'].get('passed')],
                    }
            if 'window_inventory' in inventory:
                window = inventory['window_inventory']
                row['window_inventory'] = {k: v for k, v in window.items() if k not in {'near', 'unlocated', 'calculation'}}
                if 'calculation' in window:
                    row['window_calculation'] = {k: v for k, v in window['calculation'].items()
                                                 if k not in {'answers', 'object_ledger', 'selected', 'controls', 'unculled_checks'}}
        if (root / 'zone.json').is_file():
            zone = json.loads((root / 'zone.json').read_text())
            compare = {key: differences(baseline[key], zone[key]) for key in ('answers', 'controls', 'selected')}
            row['zone_parity'] = {key: {'differences': len(items), 'first_differences': items[:10]}
                                  for key, items in compare.items()}
        summary['runs'][label] = row
    summary['comparisons'] = {}
    for pair in args.compare_pair:
        left, right = pair.split('=', 1)
        if left not in native_reports or right not in native_reports:
            parser.error('Pair references a run without a native report')
        a, b = native_reports[left], native_reports[right]
        def observations(report):
            inventory = report['inventory']
            return {
                'records': sorted([{k: r[k] for k in ('handle', 'name', 'status', 'database_present', 'database_units')}
                                   for r in report['records']], key=lambda r: r['handle']),
                'xref_instances': sorted([{k: r[k] for k in ('handle', 'name', 'record', 'status', 'chain', 'world_transform')}
                                         for r in inventory['xref_instances']], key=lambda r: r['chain']),
                'visited_instances': inventory['visited_instances'],
                'entity_counts': inventory['entity_counts'], 'layer_counts': inventory['layer_counts'],
                'issues': inventory['issues'],
                'line_controls': sorted(inventory.get('native_line_controls', []), key=lambda r: r['route']),
                'area_controls': sorted([without_timing(c)
                                         for c in inventory.get('native_area_controls', [])],
                                        key=lambda r: r['route']),
                'window_inventory': without_timing(inventory.get('window_inventory')),
            }
        measured_a, measured_b = observations(a), observations(b)
        summary['comparisons'][pair] = {
            'scope': 'Recorded inventory, selected native LINE/area controls and optional bounded window observations; not all street geometry parity',
            'fields': {k: {'difference_count': len(delta), 'first_differences': delta[:10]}
                       for k in measured_a for delta in [differences(measured_a[k], measured_b[k])]},
        }
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def without_timing(value):
    if isinstance(value, dict):
        return {k: without_timing(v) for k, v in value.items() if k != 'elapsed_ms'}
    if isinstance(value, list):
        return [without_timing(v) for v in value]
    return value


if __name__ == '__main__':
    raise SystemExit(main())
