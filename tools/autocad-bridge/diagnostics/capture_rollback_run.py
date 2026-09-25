"""Bounded native transaction controls on private CAD copies, never the GUI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import uuid

from capture_session_run import OUTPUT, build, core, digest, dump


def compare(root: Path) -> dict:
    before = json.loads((root / 'before-control.json').read_bytes())
    after = json.loads((root / 'after-abort.json').read_bytes())
    during = json.loads((root / 'during-vars.json').read_bytes())
    # Keep all differences, including clock metadata. No modified flag reset,
    # tolerance or ignored geometry discrepancy makes a failed capture pass.
    deltas = {}
    for section in before.keys() | after.keys():
        left, right = before.get(section), after.get(section)
        if left == right:
            continue
        if isinstance(left, dict) and isinstance(right, dict):
            deltas[section] = {k: {'before': left.get(k), 'after': right.get(k)}
                               for k in left.keys() | right.keys() if left.get(k) != right.get(k)}
        else:
            deltas[section] = {'before_count': len(left) if isinstance(left, list) else None,
                               'after_count': len(right) if isinstance(right, list) else None,
                               'identical': False}
    return {'all_observed_source_state_restored': not deltas,
            'source_differences': deltas,
            'dbmod': [before['vars']['DBMOD']['value'], during['DBMOD']['value'], after['vars']['DBMOD']['value']],
            'source_entity_count': len(before['entities']),
            'entities_identical': before['entities'] == after['entities'],
            'xref_graph_identical': before['graph'] == after['graph'],
            'block_records_identical': before['records'] == after['records']}


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--dirty', action='store_true', help='Synthetic unsaved host/XREF edits only')
    parser.add_argument('--controls', nargs='+', default=['passive', 'object-edit', 'symbols', 'wblock'],
                        choices=['passive', 'object-edit', 'symbols', 'wblock', 'restored-wblock', 'model-wblock', 'xref-block',
                                 'field-symbols', 'field-wblock', 'field-model-wblock'])
    args = parser.parse_args()
    if args.baseline and args.dirty:
        parser.error('Real street controls do not edit source entities')
    root = OUTPUT / ('rollback-' + uuid.uuid4().hex[:10])
    root.mkdir()
    print(str(root), flush=True)
    if shutil.disk_usage(root).free < 3_000_000_000:
        raise RuntimeError('Insufficient free disk for bounded controls')
    source = Path(__file__).with_name('capture_rollback_probe.cpp')
    bundle = build(root, source)
    receipt = {'schema': 'green-atlas.capture-rollback-control/1', 'not_production_receipt': True,
               'binary_sha256': digest(bundle / 'Contents/MacOS/CaptureSession'),
               'source_sha256': {p.name: digest(p) for p in (source, source.with_name('capture_session_clone_probe.cpp'),
                                  source.with_name('capture_session_probe.cpp'), Path(__file__))},
               'baseline': str(args.baseline.resolve()) if args.baseline else None,
               'dirty_fixture': args.dirty, 'controls': []}
    for variant in args.controls:
        case = root / variant
        case.mkdir()
        row = {'variant': variant, 'root': str(case)}
        source_files = []
        try:
            if args.baseline:
                from run_product_query_batch import stage_package
                drawing, source_files = stage_package(json.loads(args.baseline.read_bytes()), case)
                entry = drawing.relative_to(case / 'package')
                (case / 'package').rename(case / 'source')
            else:
                row['seed_engine'] = core(case, bundle, '01-seed', 'seed')
                (case / 'source/missing.dwg').rename(case / 'source/missing.held')
                entry = Path('host.dwg')
            hashes = {p.relative_to(case / 'source').as_posix(): digest(p)
                      for p in (case / 'source').rglob('*') if p.is_file()}
            row['engine'] = core(case, bundle, '02-control', 'rollback', case / 'source' / entry,
                                 variant, not args.dirty, timeout_seconds=180 if args.baseline else 60)
            row.update(compare(case))
            row['source_files_unchanged'] = all(digest(case / 'source' / p) == sha for p, sha in hashes.items())
            row['original_files_unchanged'] = all(digest(Path(f['source'])) == f['sha256'] for f in source_files)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            row['error'] = str(error)
        row['passed'] = (row.get('all_observed_source_state_restored', False)
                         and row.get('source_files_unchanged', False) and row.get('original_files_unchanged', False)
                         and row.get('engine', {}).get('exit_code') == 0
                         and not row.get('engine', {}).get('owned_process_terminated', True))
        receipt['controls'].append(row)
        dump(case / 'receipt.json', row)
        print(json.dumps({k: v for k, v in row.items() if k not in ('seed_engine', 'engine', 'source_differences')}, ensure_ascii=False), flush=True)
    dump(root / 'comparison.json', receipt)
    return int(not all(row['passed'] for row in receipt['controls']))


if __name__ == '__main__':
    raise SystemExit(main())
