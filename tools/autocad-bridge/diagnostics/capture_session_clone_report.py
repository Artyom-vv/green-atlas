"""Derive clone-first evidence receipts; never overwrite prior run receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from capture_session_run import OUTPUT, REPO, digest, dump


def read(root: Path, name: str):
    return json.loads((root / f'{name}.json').read_text())


def differences(a, b, path='') -> list:
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for key in sorted(a.keys() | b.keys()) for d in differences(a.get(key), b.get(key), f'{path}/{key}')]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in differences(x, y, f'{path}/{i}')]
    return [] if a == b else [{'path': path, 'before': a, 'after': b}]


def summary(root: Path) -> dict:
    receipt = read(root, 'clone-receipt')
    engines = {p.parent.name: read(p.parent, 'engine') for p in sorted(root.glob('0*/engine.json'))}
    result = {k: receipt.get(k) for k in ('variant', 'clean', 'error', 'source_files_unchanged', 'source_sha256', 'native_binary_sha256')}
    result['processes'] = {k: {p: e[p] for p in ('pid', 'exit_code', 'commands_completed', 'owned_process_terminated', 'wall_seconds')} for k, e in engines.items()}
    result['all_four_processes_exit_zero'] = len(engines) == 4 and all(e['exit_code'] == 0 and e['commands_completed'] and not e['owned_process_terminated'] for e in engines.values())
    if not (root / 'before.json').exists():
        return result
    before = read(root, 'before')
    result['observed_live_deltas'] = []
    states = sorted(root.glob('after-clone-*.json')) + sorted(root.glob('after-save-copy-*.json'))
    states += [p for p in (root / 'before-copy-destruction.json', root / 'after.json') if p.exists()]
    for p in states:
        delta = differences(before, json.loads(p.read_text()))
        if delta:
            result['observed_live_deltas'].append({'stage': p.name, 'changes': delta})
    result['source_dbmod_before'] = before['vars']['DBMOD']['value']
    if (root / 'after.json').exists():
        result['source_dbmod_after'] = read(root, 'after')['vars']['DBMOD']['value']
    if (root / 'mapping-receipt.json').exists():
        mapping = read(root, 'mapping-receipt')
        result['checks'] = mapping['checks']
        result['source_instances'] = mapping['source_instances']
        result['reopened_instances'] = mapping['reopened_instances']
        result['strict_capture_and_process_guards_pass'] = bool(receipt['source_files_unchanged']) and all(mapping['checks'].values()) and result['all_four_processes_exit_zero']
    return result


def capture_receipt(root: Path) -> dict:
    execution = read(root, 'clone-receipt')
    mapping = read(root, 'mapping-receipt')
    states = {name: read(root, name) for name in ('opened', 'before', 'after', 'reopened')}
    before, reopened = states['before'], states['reopened']
    saved = execution['saved_files']
    assert all(digest(root / f['file']) == f['sha256'] for f in saved)
    package = [dict(f, file=Path(f['file']).name) for f in saved if f['file'].startswith('isolated/')]
    canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    source_hashes = execution['source_hashes_before']
    sealed = root / 'sealed-source'
    assert all(digest(sealed / name) == value for name, value in source_hashes.items())
    file_map = {f['file']: f for f in saved}
    graph_maps = {m['source_handle']: m for m in mapping['xref_record_maps']}
    unloaded = {u['handle']: u for u in read(root, 'unloaded-copies')}
    graph = []
    for source in before['graph']['nodes']:
        unit = next((u for u in mapping['native_object_maps'] if u['unit'] == source['handle']), None)
        name = unit['file'] if unit else unloaded[source['handle']]['file'] if source['handle'] in unloaded else None
        authored = source.get('authored_path', before['database']['filename'] if source['index'] == 0 else '')
        resolved = source.get('resolved_path', before['database']['filename'] if source['index'] == 0 else '')
        authored_hash = source_hashes.get(Path(authored).name) if authored and Path(authored).parent == root / 'source' else None
        graph.append({
            'source_record': source,
            'reopened_record_mapping': graph_maps.get(source['handle']),
            'authored': {'path': authored, 'sha256_observed_before': authored_hash},
            'resolved': {'path': resolved, 'sha256_observed_before': source.get('resolved_disk_sha256', before['database']['runtime_file_sha256']) or None},
            'runtime_database_file': {'path': source['database']['filename'], 'sha256_observed_before': source['database']['runtime_file_sha256'] or None,
                                      'kind': 'host_source_file' if source['index'] == 0 else 'loaded_xref_cache' if source['database_present'] else 'no_loaded_database',
                                      'not_a_hash_of_unsaved_memory': True},
            'archive': {'method': 'native_clone_private_saveAs' if unit else 'exact_unloaded_disk_copy_then_private_saveAs' if name else 'unavailable_not_archived',
                        'raw': file_map.get(f'raw/{name}'), 'isolated': file_map.get(f'isolated/{name}')},
            'no_claim_authored_cache_archive_bytes_equal': True,
        })
    old_by_route = {e['route']: e for e in states['opened']['entities']}
    new_by_route = {e['route']: e for e in reopened['entities']}
    source_by_route = {e['route']: e for e in before['entities']}
    unsaved = []
    for route in mapping['source_instance_to_archive_instance']:
        src = source_by_route[route['source_route']]
        old = old_by_route.get(src['route'])
        if old is None or old.get('vertices') != src.get('vertices'):
            target = new_by_route[route['archive_route']]
            unsaved.append({'source_route': src['route'], 'archive_route': target['route'], 'absent_from_opened_source': old is None,
                            'opened_vertices': old.get('vertices') if old else None, 'unsaved_vertices': src.get('vertices'),
                            'reopened_vertices': target.get('vertices'), 'reopened_world_vertices': target.get('world_vertices'),
                            'native_mapping_and_full_payload_match': route['matched']})
    result = {
        'schema': 'green-atlas.capture-native-clone-experiment/1', 'not_production_receipt': True,
        'production_ready': False, 'capture_id': root.name,
        'operation': 'forced whole host wblock + XREF wblockCloneObjects with native BTR dependencies and private reference repair + private saveAs/repath',
        'native_binary_sha256': execution['native_binary_sha256'], 'source_sha256_at_build': execution['source_sha256'],
        'host_sha256': digest(root / 'isolated/host.dwg'), 'package_sha256': hashlib.sha256(canonical).hexdigest(),
        'package_sha256_recipe': 'SHA256 UTF8 JSON(package_files sorted filename, sort_keys=True, separators=(comma,colon))',
        'package_files': package, 'saved_files': saved,
        'native_revision_before': before['database'], 'native_revision_after_capture': states['after']['database'],
        'native_revision_reopened': reopened['database'], 'units': before['database']['units'],
        'loaded_graph_at_capture': graph, 'graph_reopened': reopened['graph'],
        'native_source_instance_to_archive_instance': mapping['source_instance_to_archive_instance'],
        'native_id_mapping_files': [{'file': p.name, 'sha256': digest(p)} for p in sorted(root.glob('native-map-*.json'))],
        'native_reference_repairs': [item for p in sorted(root.glob('native-reference-repairs-*.json')) for item in json.loads(p.read_text())],
        'unsaved_controls': unsaved, 'strict_evidence': summary(root),
        'source_files_before': source_hashes, 'source_files_after': {name: digest(sealed / name) for name in source_hashes},
        'source_old_directory_unavailable_at_reopen': not (root / 'source').exists(),
        'snapshot_files': [{'file': f'{name}.json', 'sha256': digest(root / f'{name}.json')} for name in states],
        'limits': [
            'Synthetic LINE/polyline/INSERT graph only, not a complete arbitrary AutoCAD DB proof.',
            'Whole-host wblock omits unreferenced symbols and application NOD data unless explicitly handled.',
            'XREF clones include model-space entities and primary BTR dependencies; unrelated headers/layouts/NOD data are not covered. Only INSUNITS copied explicitly.',
            'Core capture completed but exit 254 is unexplained and remains a lifecycle blocker.',
            'All metadata deltas including timers stay in the negative guard. No timer reset, DBMOD suppression or live restore/save.',
            'Modal single-process scratch capture does not prove GUI document/XREF locking, undo/redo or atomicity against custom reactors/concurrent writers.',
            'Authored/resolved/cache/archive hashes are distinct; no hash identifies unsaved memory until serialization.',
            'No cyclic/shared-name graph, nested unloaded dependency tree, REFEDIT, proxy entity, external asset, Windows or real user document acceptance.',
        ],
    }
    return result


def main() -> None:
    if sys.argv[1:] == ['--inventory-only']:
        files = [p for folder in OUTPUT.glob('followup-*') if folder.is_dir() for p in folder.rglob('*') if p.is_file()]
        files += [OUTPUT / name for name in ('CLONE-FOLLOWUP.md', 'clone-followup-summary.json', 'followup-validation.json')]
        files += list((REPO / 'tools/autocad-bridge/diagnostics').glob('capture_session_*'))
        files = sorted(set(p for p in files if p.is_file()))
        manifest = [{'path': str(p.relative_to(REPO)), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files]
        dump(OUTPUT / 'created-files-followup.json', {'self_sha_excluded': True, 'files': manifest})
        print(json.dumps({'inventoried_files': len(files), 'bytes': sum(p['bytes'] for p in manifest)}))
        return
    finals = ('followup-objects-dirty-final', 'followup-objects-clean-final')
    for name in finals:
        dump(OUTPUT / name / 'followup-capture-receipt.json', capture_receipt(OUTPUT / name))
    runs = {p.parent.name: summary(p.parent) for p in sorted(OUTPUT.glob('followup-*/clone-receipt.json'))}
    dump(OUTPUT / 'clone-followup-summary.json', {
        'schema': 'green-atlas.capture-clone-followup/1', 'production_ready': False,
        'strict_nonmutating_arbitrary_database_capture_proven': False,
        'runs': runs, 'final_receipts': [f'{name}/followup-capture-receipt.json' for name in finals],
        'historical_receipts_preserved': True,
        'old_dirty_runner_passed_flag_did_not_include_process_exit_guard': True,
    })
    print(json.dumps({name: {k: v for k, v in read(OUTPUT / name, 'followup-capture-receipt').items() if k in ('host_sha256', 'package_sha256', 'production_ready')}
                      for name in finals}, indent=2))


if __name__ == '__main__':
    main()
