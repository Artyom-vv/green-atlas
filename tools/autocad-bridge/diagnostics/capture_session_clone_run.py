"""Native clone-first follow-up; strict source state and native route mapping."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import uuid

from capture_session_run import OUTPUT, build, core, digest, dump

RUNTIME = {'runtime_object_id', 'runtime_database', 'reference_runtime_id', 'reference_redirected_runtime_id'}


def read(root: Path, name: str):
    return json.loads((root / f'{name}.json').read_text())


def evaluate(root: Path) -> dict:
    before, after, reopened = [read(root, name) for name in ('before', 'after', 'reopened')]
    units = [json.loads(p.read_text()) for p in sorted(root.glob('native-map-*.json'))]
    by_database = {u['source_database']: u for u in units}
    by_source_route = {e['route']: e for e in before['entities']}
    by_target_route = {e['route']: e for e in reopened['entities']}
    # Real streets have >100k instances. Re-scanning all native pairs for every
    # route segment is quadratic; retain exactly the same ambiguity checks.
    map_indices = {}
    for unit in units:
        index: dict[str, set[str]] = {}
        for event in unit['events']:
            for pair in event['pairs']:
                if pair['target_database'] != unit['target_database'] or pair['target_handle'] == '0':
                    continue
                for runtime_id in (pair['source_runtime_id'], pair['source_redirected_runtime_id']):
                    index.setdefault(runtime_id, set()).add(pair['target_handle'])
        map_indices[unit['source_database']] = index

    def translate(unit: dict, runtime_id: str) -> str:
        candidates = map_indices[unit['source_database']].get(runtime_id, set())
        if len(candidates) != 1:
            raise ValueError(f"native mapping requires exactly one target for {unit['file']} id {runtime_id}: {sorted(candidates)}")
        return next(iter(candidates))

    rows = []
    for entity in before['entities']:
        row = {'source_route': entity['route'], 'source_runtime_id': entity['runtime_object_id']}
        try:
            parts = entity['route'].split('/')
            mapped = []
            native_steps = []
            for i in range(len(parts)):
                source = by_source_route['/'.join(parts[:i + 1])]
                unit = by_database[source['runtime_database']]
                handle = translate(unit, source['runtime_object_id'])
                mapped.append(handle)
                native_steps.append({'source_prefix': source['route'], 'source_runtime_id': source['runtime_object_id'],
                                     'source_database': unit['source_database'], 'archive_file': unit['file'], 'target_handle': handle})
            row['archive_route'] = '/'.join(mapped)
            row['native_steps'] = native_steps
            expected = {k: v for k, v in entity.items() if k not in RUNTIME}
            expected['route'], expected['handle'] = row['archive_route'], mapped[-1]
            actual = by_target_route.get(row['archive_route'])
            if actual is None:
                row['error'] = 'mapped archive route absent after reopening'
            else:
                if 'reference_record' in entity:
                    unit = by_database[entity['runtime_database']]
                    local_record = translate(unit, entity['reference_runtime_id'])
                    row['reference_mapping'] = {
                        'source_unredirected_id': entity['reference_runtime_id'],
                        'source_redirected_id': entity['reference_redirected_runtime_id'],
                        'archive_local_record_from_native_mapping': local_record,
                        'reopened_local_record_from_nonForwardedHandle': actual['reference_original_handle'],
                        'reopened_forwarded_record': actual['reference_record'],
                        'reopened_redirected_id': actual['reference_redirected_runtime_id'],
                        'matched': local_record == actual['reference_original_handle'],
                    }
                    # Runtime graph BTR handles are not archive-local handles.
                    # Compare local identity separately using native forwarding.
                    expected.pop('reference_record'); expected.pop('reference_original_handle')
                actual = {k: v for k, v in actual.items() if k not in RUNTIME}
                if 'reference_mapping' in row:
                    actual.pop('reference_record'); actual.pop('reference_original_handle')
                row['differences'] = {k: {'source_mapped': expected.get(k), 'archive': actual.get(k)}
                                      for k in expected.keys() | actual.keys() if expected.get(k) != actual.get(k)}
                row['matched'] = not row['differences'] and row.get('reference_mapping', {}).get('matched', True)
        except (KeyError, ValueError) as error:
            row['error'] = str(error)
        rows.append(row)
    snapshots = [p for pattern in ('after-clone-*.json', 'after-save-copy-*.json') for p in sorted(root.glob(pattern))]
    snapshots += [root / 'before-copy-destruction.json', root / 'after.json']
    deltas = []
    for path in snapshots:
        state = json.loads(path.read_text())
        for key in ('database', 'document_filename', 'vars', 'graph', 'records', 'entities'):
            if before[key] != state[key]:
                deltas.append({'stage': path.name, 'section': key, 'before': before[key], 'after': state[key]})
    mapped_routes = {r['archive_route'] for r in rows if 'archive_route' in r}
    def graph_roles(state):
        # Root graph name is the DWG filename, deliberately host.dwg in the
        # archive. All actual XREF names, status, nesting and edges stay exact.
        return [dict({k: n[k] for k in ('status', 'read_substatus', 'nested', 'children', 'database_present')},
                     name='$host' if n['index'] == 0 else n['name']) for n in state['graph']['nodes']]

    source_graph, reopened_graph = graph_roles(before), graph_roles(reopened)
    graph_maps = []
    for source in before['graph']['nodes'][1:]:
        row = {'name': source['name'], 'source_handle': source['handle']}
        try:
            native_links = [r for r in rows if r.get('reference_mapping', {}).get('source_redirected_id') == source['runtime_object_id']]
            targets = {r['reference_mapping']['reopened_redirected_id'] for r in native_links if r['reference_mapping']['matched']}
            if not native_links or len(targets) != 1 or not all(r['reference_mapping']['matched'] for r in native_links):
                raise ValueError('no unique mapped native instance-to-graph chain')
            target = next(n for n in reopened['graph']['nodes'] if n['runtime_object_id'] in targets)
            row['archive_handle'] = target['handle']
            row['native_instance_chains'] = [r['source_route'] for r in native_links]
            row['matches_reopened_record'] = all(source[k] == target[k] for k in ('name', 'status', 'read_substatus', 'nested', 'children', 'database_present'))
        except (ValueError, StopIteration) as error:
            row['error'] = str(error)
        graph_maps.append(row)
    return {
        'schema': 'green-atlas.capture-native-clone-mapping/1', 'not_production_receipt': True,
        'capture_id': root.name, 'units': before['database']['units'],
        'source_state_deltas': deltas, 'native_object_maps': units,
        'source_instance_to_archive_instance': rows, 'xref_record_maps': graph_maps,
        'source_instances': len(rows), 'reopened_instances': len(reopened['entities']),
        'checks': {
            'live_state_unchanged_at_every_observed_stage': not deltas,
            'all_clone_targets_independent': all(not u['target_is_live_database'] for u in units),
            'every_source_instance_has_unique_native_mapping_and_identical_payload': all(r.get('matched', False) for r in rows),
            'no_unmapped_archive_instances': mapped_routes == set(by_target_route),
            'no_route_collisions': len(mapped_routes) == len(rows),
            'xref_graph_roles_and_statuses_match': sorted(source_graph, key=lambda n: n['name']) == sorted(reopened_graph, key=lambda n: n['name']),
            'every_xref_record_matches_native_mapping': all(r.get('matches_reopened_record', False) for r in graph_maps),
            'units_match': before['database']['units'] == reopened['database']['units'],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--variant', required=True, choices=('whole-wblock', 'selected-wblock', 'clone-objects', 'restored-wblock'))
    parser.add_argument('--name', default='clone-followup-' + uuid.uuid4().hex[:10])
    parser.add_argument('--clean', action='store_true')
    parser.add_argument('--baseline', type=Path,
                        help='Explicit frozen real-package receipt; only independent copies, no fixture edits')
    parser.add_argument('--observe', choices=('passive', 'restore-symbols'),
                        help='Control: observations/paired symbol restore only; no clone/save/repath')
    args = parser.parse_args()
    if args.baseline and (not args.clean or args.variant not in ('clone-objects', 'restored-wblock')):
        parser.error('Real-package replay requires --clean and an explicit clone candidate')
    if args.observe and not (args.baseline and args.clean):
        parser.error('Observation control requires an explicit clean real package')
    if Path(args.name).name != args.name or args.name in ('.', '..'):
        parser.error('Simple new output name required')
    root = OUTPUT / args.name
    root.mkdir()
    print(str(root), flush=True)
    receipt = {'variant': args.variant, 'observe': args.observe, 'clean': args.clean, 'disk_free_before': shutil.disk_usage(root).free, 'engines': {}}
    hashes = {}
    try:
        if receipt['disk_free_before'] < 2_000_000_000:
            raise RuntimeError('Insufficient free disk')
        source = Path(__file__).with_name('capture_session_clone_probe.cpp')
        bundle = build(root, source)
        receipt['source_sha256'] = {p.name: digest(p) for p in (source, source.with_name('capture_session_probe.cpp'), Path(__file__), source.with_name('capture_session_run.py'))}
        receipt['native_binary_sha256'] = digest(bundle / 'Contents/MacOS/CaptureSession')
        if args.baseline:
            from run_product_query_batch import stage_package
            receipt['baseline'] = str(args.baseline.resolve())
            receipt['baseline_sha256'] = digest(args.baseline)
            drawing, source_files = stage_package(json.loads(args.baseline.read_bytes()), root)
            entry = drawing.relative_to(root / 'package')
            (root / 'package').rename(root / 'source')
            for row in source_files:
                row['staged'] = str(root / 'source' / Path(row['staged']).relative_to(root / 'package'))
            receipt['real_original_files'] = source_files
        else:
            receipt['engines']['seed'] = core(root, bundle, '01-seed', 'seed')
            (root / 'source/missing.dwg').rename(root / 'source/missing.held')
            entry = Path('host.dwg')
        hashes = {p.relative_to(root / 'source').as_posix(): digest(p)
                  for p in (root / 'source').rglob('*') if p.is_file()}
        receipt['source_hashes_before'] = hashes
        receipt['engines']['capture'] = core(root, bundle, '02-capture', 'observe' if args.observe else 'capture',
                                            root / 'source' / entry, args.observe or args.variant, args.clean,
                                            timeout_seconds=180 if args.baseline else 60)
        if args.observe:
            states = read(root, 'observations')
            receipt['observations'] = {
                'entities_and_records_unchanged': all(
                    s[k] == states[0][k] for s in states for k in ('entities_sha256', 'records_sha256')),
                'dbmod': [{'stage': s['stage'], 'value': s['vars']['DBMOD']['value']} for s in states]}
        else:
            receipt['engines']['repath'] = core(root, bundle, '03-repath', 'repath')
            (root / 'source').rename(root / 'sealed-source')
            receipt['engines']['reopen'] = core(root, bundle, '04-reopen', 'inspect', root / 'isolated/host.dwg', 'reopened',
                                               timeout_seconds=180 if args.baseline else 60)
            receipt['evaluation'] = evaluate(root)
            dump(root / 'mapping-receipt.json', receipt['evaluation'])
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        receipt['error'] = str(error)
    finally:
        source = root / ('sealed-source' if (root / 'sealed-source').exists() else 'source')
        receipt['source_files_unchanged'] = bool(hashes) and all((source / name).is_file() and digest(source / name) == sha for name, sha in hashes.items())
        if args.baseline:
            receipt['real_original_files_unchanged'] = bool(receipt.get('real_original_files')) and all(
                digest(Path(row['source'])) == row['sha256'] for row in receipt['real_original_files'])
        receipt['saved_files'] = [{'file': str(p.relative_to(root)), 'sha256': digest(p), 'bytes': p.stat().st_size}
                                  for folder in ('raw', 'isolated') if (root / folder).exists()
                                  for p in sorted((root / folder).glob('*.dwg'))]
        # Keep semantic evidence distinct from abnormal Core process teardown.
        # A script completion marker does not erase a non-zero exit code.
        receipt['engines'] = {p.parent.name: json.loads(p.read_text()) for p in sorted(root.glob('0*/engine.json'))}
        checks = receipt.get('evaluation', {}).get('checks', {})
        receipt['capture_checks_passed'] = not receipt.get('error') and receipt['source_files_unchanged'] and bool(checks) and all(checks.values())
        expected_stages = 1 if args.observe else 3 if args.baseline else 4
        receipt['all_core_stages_exit_zero'] = len(receipt['engines']) == expected_stages and all(e['exit_code'] == 0 and e['commands_completed'] and not e['owned_process_terminated'] for e in receipt['engines'].values())
        receipt['passed'] = receipt['capture_checks_passed'] and receipt['all_core_stages_exit_zero']
        dump(root / 'clone-receipt.json', receipt)
    print(json.dumps({k: v for k, v in receipt.items() if k not in ('evaluation', 'engines', 'source_hashes_before', 'saved_files', 'real_original_files')}, indent=2), flush=True)
    if 'evaluation' in receipt:
        print(json.dumps(receipt['evaluation']['checks'], indent=2), flush=True)
    return int(not receipt['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
