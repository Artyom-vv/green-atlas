"""Run product native capture on independent CAD fixtures, with reopen proof."""
from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path

from capture_session_run import OUTPUT, build, core, digest, dump, geometry


def evaluate(root: Path) -> dict:
    read = lambda name: json.loads((root / name).read_text())
    before, after = read('before.json'), read('after.json')
    reopened = read('reopened.json')
    mapping = read('package/instances.json')
    targets = {row['route']: row for row in reopened['entities']}
    originals = {row['route']: row for row in before['entities']}
    differences = []
    ignored = {'route', 'handle', 'runtime_object_id', 'runtime_database',
               'reference_record', 'reference_runtime_id', 'reference_original_handle',
               'reference_redirected_runtime_id'}
    for row in mapping:
        expected = originals[row['source_route']]
        actual = targets.get(row['archive_route'], {})
        delta = {key: [value, actual.get(key)] for key, value in expected.items()
                 if key not in ignored and value != actual.get(key)}
        if delta:
            differences.append({'source': row['source_route'], 'archive': row['archive_route'], 'differences': delta})
    routes = [row['archive_route'] for row in mapping]
    return {
        'observed_source_geometry_unchanged': geometry(before) == geometry(after),
        'source_filename_unchanged': before['database']['filename'] == after['database']['filename'],
        'source_dbmod_before': before['vars']['DBMOD']['value'],
        'source_dbmod_after': after['vars']['DBMOD']['value'],
        'all_observed_instances_mapped_once': len(set(routes)) == len(routes) == len(originals)
            and set(routes) == set(targets),
        'source_instances': len(originals), 'reopened_instances': len(targets),
        'differences': differences,
        'scope': 'native identities/classes/layers/transforms; vertices for lines/polylines only, not all CAD payloads',
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, help='Copy an existing flat DWG directory; never operate on the supplied files')
    parser.add_argument('--entry', default='host.dwg')
    parser.add_argument('--clean', action='store_true')
    args = parser.parse_args()
    root = OUTPUT / ('product-' + uuid.uuid4().hex[:10])
    root.mkdir(parents=True)
    print(root, flush=True)
    bundle = build(root, Path(__file__).with_name('product_capture_probe.cpp'))
    engines = {}
    if args.source:
        shutil.copytree(args.source, root / 'source')
    else:
        engines['seed'] = core(root, bundle, '01-seed', 'seed')
        # Missing ref is a deliberate fixture. Keep the source recoverable.
        (root / 'source/missing.dwg').rename(root / 'missing-control.dwg')
    originals = {p.name: digest(p) for p in (root / 'source').glob('*.dwg')}
    engines['capture'] = core(root, bundle, '02-capture', 'capture',
        root / 'source' / args.entry, clean=args.clean, timeout_seconds=180)
    engines['reopen'] = core(root, bundle, '03-reopen', 'inspect',
        root / 'package/host.dwg', clean=True, timeout_seconds=180)
    result = evaluate(root)
    result['original_disk_files_unchanged'] = originals == {
        p.name: digest(p) for p in (root / 'source').glob('*.dwg')}
    result['engines'] = engines
    result['all_engines_exit_zero'] = all(row['exit_code'] == 0 for row in engines.values())
    dump(root / 'receipt.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
