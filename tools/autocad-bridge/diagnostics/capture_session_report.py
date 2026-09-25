"""Aggregate existing scratch evidence without modifying historical receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO / 'artifacts/native-session-capture-20260923'


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(root: Path, name: str):
    return json.loads((root / f'{name}.json').read_text())


def without_runtime(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != 'runtime_object_id'}


def summary(root: Path) -> dict:
    receipt = read(root, 'receipt')
    result = {k: receipt.get(k) for k in ('variant', 'clean', 'error', 'source_files_unchanged', 'native_binary_sha256', 'native_source_sha256')}
    result['engines'] = {p.parent.name: read(p.parent, 'engine') for p in root.glob('*/engine.json')}
    if not (root / 'reopened.json').exists():
        return result
    opened, before, host, after, reopened = [read(root, name) for name in ('opened', 'before', 'after-host', 'after', 'reopened')]
    result['dbmod'] = {name: state['vars']['DBMOD']['value'] for name, state in [('opened', opened), ('before', before), ('after_host', host), ('after_all', after), ('reopened', reopened)]}
    result['entity_counts'] = {name: len(state['entities']) for name, state in [('before', before), ('reopened', reopened)]}
    result['reopened_payload_identical_including_handles'] = list(map(without_runtime, before['entities'])) == list(map(without_runtime, reopened['entities']))
    result['reopened_entity_deltas'] = [
        {'index': i, 'changes': {k: {'before': x.get(k), 'after': y.get(k)} for k in x.keys() | y.keys() if k != 'runtime_object_id' and x.get(k) != y.get(k)}}
        for i, (x, y) in enumerate(zip(before['entities'], reopened['entities'])) if without_runtime(x) != without_runtime(y)]
    result['view_and_path_vars_unchanged'] = {k: v for k, v in before['vars'].items() if k != 'DBMOD'} == {k: v for k, v in after['vars'].items() if k != 'DBMOD'}
    result['xref_records_unchanged_in_source'] = before['records'] == after['records']
    result['graph_before'] = [{k: n[k] for k in ('name', 'handle', 'status', 'nested', 'database_present')} for n in before['graph']['nodes']]
    result['graph_reopened'] = [{k: n[k] for k in ('name', 'handle', 'status', 'nested', 'database_present')} for n in reopened['graph']['nodes']]
    result['live_database_metadata_deltas'] = [
        {'handle': x['handle'], 'changes': {k: {'before': v, 'after': y['database'].get(k)} for k, v in x['database'].items() if v != y['database'].get(k)}}
        for x, y in zip(before['graph']['nodes'], after['graph']['nodes'], strict=True) if x['database'] != y['database']]
    result['source_metadata_and_dbmod_unchanged'] = not result['live_database_metadata_deltas'] and before['vars']['DBMOD'] == after['vars']['DBMOD']
    result['host_guid_unchanged_despite_unsaved_edit'] = (opened['database']['version_guid'] == before['database']['version_guid'] and opened['entities'] != before['entities'])
    result['native_operations'] = read(root, 'actions')
    return result


def main() -> None:
    names = ['raw-01', 'clone-02', 'restore-06', 'clean-01', 'dxf-clean-01', 'final-saveas']
    report = {
        'schema': 'green-atlas.capture-session-research/1',
        'strict_nonmutating_capture_proven': False,
        'conclusion': 'Reopening payload is reproducible with host saveAs(false) plus restored XREF saveAs, but clean source DBMOD changes 0->1 and save metadata changes. Do not treat the candidate as a non-mutating production capture.',
        'runs': {name: summary(ROOT / name) for name in names},
        'all_attempts': {p.parent.name: read(p.parent, 'receipt').get('error') for p in sorted(ROOT.glob('*/receipt.json'))},
        'sources': [
            'https://help.autodesk.com/cloudhelp/2027/ENU/OARX-RefGuide/files/OARX-RefGuide-AcDbDatabase__saveAs_ACHAR__bool_AcDb__AcDbDwgVersion_SecurityParams_.html',
            'https://help.autodesk.com/cloudhelp/2027/ENU/OARX-RefGuide/files/OARX-RefGuide-AcDbDatabase__restoreOriginalXrefSymbols.html',
            'https://help.autodesk.com/cloudhelp/2027/ENU/OARX-RefGuide/files/OARX-RefGuide-AcDbDatabase__restoreForwardingXrefSymbols.html',
        ],
    }
    with (ROOT / 'comparison.json').open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    # This inventories all extant generated files, including native profiles and
    # failed attempts. The inventory cannot include its own SHA.
    files = sorted(p for p in ROOT.rglob('*') if p.is_file())
    files += sorted((REPO / 'tools/autocad-bridge/diagnostics').glob('capture_session_*'))
    inventory = [{'path': str(p.relative_to(REPO)), 'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files]
    with (ROOT / 'created-files.json').open('x') as stream:
        json.dump(inventory, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(ROOT / 'comparison.json'), 'inventoried_files': len(files),
                      'inventoried_bytes': sum(r['bytes'] for r in inventory)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
