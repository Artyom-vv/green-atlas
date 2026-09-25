"""Archive real native results and independently check a finite LINE distance.

No CAD parsing or polygon reconstruction. Endpoints come from the native
inventory; the analytic distance is an experimental oracle, not an importer.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import shutil
from pathlib import Path

TOLERANCE_M = 1e-8


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def segment_distance(point: list, start: list, end: list) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    if denominator == 0:
        raise ValueError('Degenerate line cannot be this distance oracle')
    x, y = point[0] - start[0], point[1] - start[1]
    t = max(0.0, min(1.0, (x * dx + y * dy) / denominator))
    return math.hypot(x - t * dx, y - t * dy)


def check_run(report: dict, line: dict, receipt: dict) -> dict:
    observations = report['answers'] + [row['actual'] for row in report['controls']]
    errors = [abs(segment_distance(row['point'], line['start'], line['end'])
                  - row['utility']['distance_xy']) for row in observations]
    selected = report['selected']
    answers = {tuple(row['point']): row for row in report['answers']}
    min_spacing = min((math.dist(a, b) for a, b in itertools.combinations(selected, 2)),
                      default=None)
    selection_invalid = sum(answers[tuple(p)]['result'] != 'clear_of_selected_objects_only'
                            for p in selected)
    failures = sum(row['utility']['status'] != 0
                   or row['building']['status'] != 0 or not row['building']['distance_complete']
                   or row['building']['membership'] == 'unknown'
                   or row['site']['status'] != 0 or not row['site']['distance_complete']
                   or row['site']['membership'] == 'unknown' for row in observations)
    elevated = report['native_line_elevation_check']
    passed = (receipt['experiment_expectations_met'] and failures == 0
              and max(errors) <= TOLERANCE_M and selection_invalid == 0
              and len(selected) == len({tuple(p) for p in selected})
              and (min_spacing is None or min_spacing >= report['policy']['spacing_m'])
              and report['controls_passed'] == len(report['controls']) > 0
              and elevated['status'] == 0 and elevated['mismatches'] == 0 and elevated['queries'] > 0)
    return {
        'grid_points': report['query_count'], 'repeats': report['repeats'],
        'reason_counts': report['reason_counts'], 'batch_ms': report['batch_ms'],
        'native_load_ms': report['load_ms'], 'native_prepare_ms': report['prepare_ms'],
        'oracle_observations': len(errors), 'segment_distance_max_error_m': max(errors),
        'unknown_or_failed_observations': failures, 'selected_count_capped_at_81': len(selected),
        'minimum_selected_spacing_m': min_spacing, 'invalid_selected_points': selection_invalid,
        'controls_passed': report['controls_passed'], 'controls_failed': report['controls_failed'],
        'repeat_mismatches': report['repeat_mismatches'],
        'manual_recheck_mismatches': report['manual_recheck_mismatches'],
        'height_query_count': report['height_query_count'],
        'height_query_mismatches': report['height_query_mismatches'],
        'native_line_elevation_check': elevated, 'engine': receipt['query_engine'], 'passed': passed,
    }


def compare(a: dict, b: dict) -> dict:
    same_inputs = all(a[k] == b[k] for k in ('source_sha256', 'network_sha256', 'building', 'site', 'utility', 'policy'))
    same_objects = all(a[k] == b[k] for k in ('building', 'site', 'utility', 'policy'))
    left = a['answers'] + [row['actual'] for row in a['controls']]
    right = b['answers'] + [row['actual'] for row in b['controls']]
    max_delta = 0.0
    mismatches = 0
    for x, y in zip(left, right, strict=True):
        mismatch = any(x[k] != y[k] for k in ('point', 'result', 'reason'))
        for kind in ('building', 'site', 'utility'):
            mismatch |= x[kind]['status'] != y[kind]['status']
            if kind != 'utility':
                mismatch |= x[kind]['membership'] != y[kind]['membership']
                mismatch |= x[kind]['distance_complete'] != y[kind]['distance_complete']
            field = 'distance_xy' if kind == 'utility' else 'distance'
            if x[kind][field] is None or y[kind][field] is None:
                mismatch |= x[kind][field] != y[kind][field]
            else:
                delta = abs(x[kind][field] - y[kind][field])
                max_delta = max(max_delta, delta)
                mismatch |= delta > TOLERANCE_M
        mismatches += bool(mismatch)
    return {'same_inputs': same_inputs, 'observations': len(left),
            'same_object_selection_and_policy': same_objects,
            'query_mismatches': mismatches, 'max_distance_delta_m': max_delta,
            'same_selected_points': a['selected'] == b['selected']}


def archive(source: Path, destination: Path) -> None:
    destination.mkdir()
    for name in ('native.json', 'receipt.json', 'case.json', 'request.txt', 'core.log', 'query.scr'):
        shutil.copy2(source / name, destination / name)
    if (source / 'convert/core.log').is_file():
        shutil.copy2(source / 'convert/core.log', destination / 'conversion-core.log')
        shutil.copy2(source / 'convert/convert.scr', destination / 'conversion.scr')
    if (source / 'convert-source/core.log').is_file():
        shutil.copy2(source / 'convert-source/core.log', destination / 'source-conversion-core.log')
        shutil.copy2(source / 'convert-source/convert.scr', destination / 'source-conversion.scr')


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--runs', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--regenerated', type=Path, help='Additional run from original DWGs, converted anew')
    parser.add_argument('--source-dwg-sha256', help='Previously recorded original topography DWG identity')
    args = parser.parse_args()
    if len(args.runs) < 2:
        parser.error('Need at least two fresh-process runs')
    if args.regenerated and not args.source_dwg_sha256:
        parser.error('Regeneration comparison requires the recorded original DWG SHA')
    args.output.mkdir(parents=True, exist_ok=False)
    inventory = read(args.inventory / 'native.json')
    reports = [read(path / 'native.json') for path in args.runs]
    receipts = [read(path / 'receipt.json') for path in args.runs]
    line = next(row for row in inventory['inventory']['nearest']
                if row['handle'] == reports[0]['utility']['handle'])
    if line['entity_type'] != 'AcDbLine':
        raise ValueError('Analytic oracle supports only the selected native LINE')
    summary = {
        'schema': 'green-atlas.native-zone-evidence/1',
        'scope': 'Three selected CAD objects; not whole-street or product acceptance',
        'oracle': {'kind': 'finite XY segment, not a CAD parser', 'native_endpoint_record': line},
        'same_network_as_inventory': all(r['network_sha256'] == inventory['network_sha256'] for r in reports),
        'same_binary': len({r['native_binary_sha256'] for r in receipts}) == 1,
        'native_binary_sha256': receipts[0]['native_binary_sha256'],
        'runs': [check_run(r, line, receipt) for r, receipt in zip(reports, receipts, strict=True)],
        'fresh_process_comparisons': [compare(reports[0], r) for r in reports[1:]],
    }
    summary['passed'] = (summary['same_network_as_inventory'] and summary['same_binary']
        and all(r['passed'] for r in summary['runs'])
        and all(c['same_inputs'] and c['query_mismatches'] == 0 and c['same_selected_points']
                for c in summary['fresh_process_comparisons']))
    archive(args.inventory, args.output / 'inventory')
    for i, path in enumerate(args.runs):
        archive(path, args.output / f'run-{i+1}')
    if args.regenerated:
        regenerated = read(args.regenerated / 'native.json')
        regenerated_receipt = read(args.regenerated / 'receipt.json')
        expected_originals = [args.source_dwg_sha256,
                              read(args.inventory / 'receipt.json')['original_sha256'][1]]
        cross = compare(reports[0], regenerated)
        summary['regenerated_from_dwgs'] = {
            'expected_original_sha256': expected_originals,
            'originals_match': regenerated_receipt['original_sha256'] == expected_originals,
            'source_conversion_engine': regenerated_receipt['source_conversion_engine'],
            'network_conversion_engine': regenerated_receipt['conversion_engine'],
            'run': check_run(regenerated, line, regenerated_receipt), 'comparison': cross,
        }
        regen = summary['regenerated_from_dwgs']
        summary['passed'] &= (regen['originals_match'] and regen['run']['passed']
                              and cross['same_object_selection_and_policy']
                              and cross['query_mismatches'] == 0 and cross['same_selected_points'])
        archive(args.regenerated, args.output / 'regenerated')
    summary['evidence_sha256'] = {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in args.output.rglob('*') if p.is_file()}
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(not summary['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
