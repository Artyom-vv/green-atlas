"""Bounded, standalone ObjectARX save/reopen experiment; synthetic CAD only.

Creates all output under artifacts/native-session-capture-20260923, never
installs a bundle or interacts with an existing AutoCAD/GUI process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import time
import uuid

REPO = Path(__file__).resolve().parents[3]
OUTPUT = REPO / 'artifacts/native-session-capture-20260923'
ACAD = Path('/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app')
CORE = ACAD / 'Contents/Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole'


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def dump(path: Path, value) -> None:
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def build(root: Path, source: Path | None = None) -> Path:
    sdk = REPO / '.local/objectarx-2027'
    bundle = root / 'CaptureSession.dbx'
    binary = bundle / 'Contents/MacOS/CaptureSession'
    binary.parent.mkdir(parents=True)
    with (bundle / 'Contents/Info.plist').open('xb') as stream:
        plistlib.dump({'CFBundleExecutable': 'CaptureSession',
                      'CFBundleIdentifier': 'ru.green-atlas.capture-session-experiment',
                      'CFBundleName': 'Capture Session Experiment',
                      'CFBundlePackageType': 'BNDL', 'CFBundleVersion': '1'}, stream)
    command = ['xcrun', 'clang++', '-std=c++17', '-arch', 'x86_64',
               '-mmacosx-version-min=14.0', '-bundle', '-O1', '-DNDEBUG',
               '-D_ADESK_MAC_', '-DOSX_SYSTEM', '-D_NATIVE_WCHAR_T_DEFINED',
               '-DUNICODE', '-DACDB_EXT', '-Wno-deprecated-declarations',
               '-Wno-nonportable-include-path', '-Wno-extra-tokens',
               '-include', str(REPO / 'tools/autocad-bridge/native/prefix.pch'),
               '-I', str(sdk / 'inc'), '-L', str(ACAD / 'Contents/Frameworks'),
               '-F', str(ACAD / 'Contents/Frameworks'),
               '-lacfirst', '-lwinapi', '-lacdb', '-laccore', '-lgelib', '-lAcPal',
               str(source or Path(__file__).with_name('capture_session_probe.cpp')),
               '-o', str(binary)]
    dump(root / 'build-command.json', command)
    with (root / 'build.log').open('xb') as log:
        subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(bundle)], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
    return bundle


def core(root: Path, bundle: Path, stage: str, mode: str,
         drawing: Path | None = None, variant: str = '', clean: bool = False,
         timeout_seconds: int = 60) -> dict:
    if shutil.disk_usage(root).free < 2_000_000_000:
        raise RuntimeError('Less than 2 GB free; no Core launch')
    stage_root = root / stage
    stage_root.mkdir()
    profile = stage_root / 'profile'
    profile.mkdir()
    request = stage_root / 'request.txt'
    request.write_text(f'{mode}\n{root}\n{variant}\n{"clean" if clean else "edit"}\n')
    script = stage_root / 'probe.scr'
    # Core's SCR reader treats UTF-8 paths as ANSI on this Mac. Keep SCR ASCII;
    # native request.txt is UTF-8, and argv paths are passed without a shell.
    script.write_text('(setvar "SECURELOAD" 0)\n(setvar "FILEDIA" 0)\n'
                      '(arxload (getenv "GA_CAPTURE_SESSION_BUNDLE"))\nGACAPTURESESSIONPROBE\nrequest.txt\n'
                      '(setq gaCaptureDone (open "completed" "w")) (close gaCaptureDone)\n'
                      '_QUIT\n_Y\n\n')
    command = ['/usr/bin/arch', '-x86_64', str(CORE)]
    if drawing is None:
        drawing = stage_root / 'Empty.dwg'
        shutil.copy2(ACAD / 'Contents/Resources/UserDataCache/en-us/Template/acadiso.dwt', drawing)
    command += ['/i', str(drawing)]
    command += ['/s', str(script), '/isolate', f'ga-capture-{uuid.uuid4().hex}', str(profile)]
    started, completed, stopped = time.monotonic(), None, False
    with (stage_root / 'core.log').open('xb') as log:
        child = subprocess.Popen(command, cwd=stage_root, stdin=subprocess.DEVNULL,
                                 env={**os.environ, 'GA_CAPTURE_SESSION_BUNDLE': str(bundle)},
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        while child.poll() is None:
            if (stage_root / 'completed').exists() and completed is None:
                completed = time.monotonic()
            if time.monotonic() - started > timeout_seconds or (completed and time.monotonic() - completed > 8):
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                stopped = True
                break
            time.sleep(0.2)
    result = {'command': command, 'pid': child.pid, 'exit_code': child.returncode,
              'wall_seconds': time.monotonic() - started,
              'commands_completed': (stage_root / 'completed').exists(),
              'owned_process_terminated': stopped}
    dump(stage_root / 'engine.json', result)
    print(json.dumps({'stage': stage, **{k: v for k, v in result.items() if k != 'command'}}), flush=True)
    if not result['commands_completed'] or (root / 'native-error.txt').exists():
        raise RuntimeError(f'Incomplete native stage: {stage}; see logs')
    return result


def geometry(snapshot: dict) -> list:
    return [{k: v for k, v in row.items() if k not in ('runtime_object_id', 'runtime_database', 'reference_runtime_id', 'reference_redirected_runtime_id')}
            for row in snapshot['entities']]


def native_graph(snapshot: dict) -> list:
    return [{k: row[k] for k in ('name', 'handle', 'status', 'read_substatus', 'nested', 'database_present', 'children')}
            for row in snapshot['graph']['nodes']]


def evaluate(root: Path, clean: bool = False, variant: str = 'restore') -> dict:
    read = lambda name: json.loads((root / f'{name}.json').read_text())
    before, after, reopened = read('before'), read('after'), read('reopened')
    host = read('after-host')
    files = [{'file': str(p.relative_to(root)), 'sha256': digest(p), 'bytes': p.stat().st_size}
             for folder in ('raw', 'isolated') for p in sorted((root / folder).iterdir()) if p.suffix in ('.dwg', '.dxf')]
    actions = read('actions')
    unloaded = read('unloaded-copies') if (root / 'unloaded-copies.json').exists() else []
    package = [dict(row, file=Path(row['file']).name) for row in files if row['file'].startswith('isolated/')]
    package_bytes = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    nodes = before['graph']['nodes']
    for node in nodes:
        action = next((r for r in actions['xrefs'] + unloaded if r['handle'] == node['handle']), None)
        archive_name = action['file'] if action else 'host.dwg' if node['index'] == 0 else None
        node['archive_file'] = archive_name
        node['archive_method'] = action.get('method', 'native_loaded_DB_save') if action else 'native_host_save' if node['index'] == 0 else 'unavailable_not_archived'
        if variant == 'native-dxf' and node['archive_method'] in ('native_loaded_DB_save', 'native_host_save'):
            node['archive_method'] = 'native_dxfOut_dxfIn_private_saveAs'
        if variant == 'restore-wblock' and node['archive_method'] == 'native_loaded_DB_save':
            node['archive_method'] = 'restored_loaded_XREF_forced_wblock_copy_saveAs'
        node['raw_archive_sha256'] = digest(root / 'raw' / archive_name) if archive_name else None
        node['isolated_archive_sha256'] = digest(root / 'isolated' / archive_name) if archive_name else None
    return {
        'schema': 'green-atlas.capture-session-experiment/1',
        'capture_id': root.name,
        'not_production_receipt': True,
        'archive_strategy': variant,
        'host_sha256': digest(root / 'isolated/host.dwg'),
        'package_sha256': hashlib.sha256(package_bytes).hexdigest(),
        'package_sha256_recipe': 'SHA256(UTF-8 compact sort_keys JSON of final package files sorted by filename; fields file, sha256, bytes)',
        'package_files': package,
        'clean_source_control': clean,
        'native_revision_before': before['database'],
        'native_revision_reopened': reopened['database'],
        'units': before['database']['units'],
        'loaded_graph_at_capture': nodes,
        'saved_files': files,
        'operations': actions,
        'unloaded_disk_copies': unloaded,
        'live_xref_revision_deltas': [
            {'handle': b['handle'], 'before': b['database']['version_guid'],
             'after': a['database']['version_guid']}
            for b, a in zip(before['graph']['nodes'], after['graph']['nodes'], strict=True)
            if b['database']['version_guid'] != a['database']['version_guid']],
        'live_database_metadata_deltas': [
            {'handle': b['handle'], 'fields': {k: {'before': v, 'after': a['database'].get(k)}
             for k, v in b['database'].items() if v != a['database'].get(k)}}
            for b, a in zip(before['graph']['nodes'], after['graph']['nodes'], strict=True)
            if b['database'] != a['database']],
        'checks': {
            'two_loaded_xref_databases_archived': len(actions['xrefs']) == 2,
            'fixture_has_all_expected_instances': len(before['entities']) == (11 if clean else 12),
            'host_save_eOk': actions['host_save_status'] == 0,
            'xref_save_restore_eOk': all(all(r[k] == 0 for k in ('restore_status', 'save_status', 'forward_status')) for r in actions['xrefs']),
            'source_vars_unchanged_after_host_save': before['vars'] == host['vars'],
            'source_vars_unchanged_after_xref_saves': before['vars'] == after['vars'],
            'source_filename_unchanged': before['database']['filename'] == after['database']['filename'],
            'source_document_filename_unchanged': before['document_filename'] == after['document_filename'],
            'source_xref_paths_status_unchanged': before['records'] == after['records'],
            'source_native_graph_unchanged': native_graph(before) == native_graph(after),
            'source_database_save_metadata_unchanged': all(b['database'] == a['database'] for b, a in zip(before['graph']['nodes'], after['graph']['nodes'], strict=True)),
            'source_geometry_unchanged': geometry(before) == geometry(after),
            'reopened_geometry_identical': geometry(before) == geometry(reopened),
            'reopened_graph_identical': native_graph(before) == native_graph(reopened),
            'unsaved_vertex_control_matches_scenario': any(e.get('vertices', [])[1:2] == [[17.125, -2.5, 0]] for e in reopened['entities']) == (not clean),
            'unsaved_added_line_control_matches_scenario': any(e.get('vertices') == [[777.125, 888.25, 9], [779.5, 889.75, 9]] for e in reopened['entities']) == (not clean),
            'unsaved_loaded_xref_control_matches_scenario': sum(e.get('vertices', [])[1:2] == [[13.75, 4.125, 0]] for e in reopened['entities']) == (0 if clean else 2),
            'baseline_xref_has_no_unsaved_edit': not any(e.get('vertices', [])[1:2] == [[13.75, 4.125, 0]] for e in read('opened')['entities']),
            'baseline_has_no_unsaved_edits': not any(e.get('vertices', [])[1:2] == [[17.125, -2.5, 0]] for e in read('opened')['entities']),
            'source_dbmod_matches_scenario_before_after': (before['vars']['DBMOD']['value'] != 0) == (not clean) and (after['vars']['DBMOD']['value'] != 0) == (not clean),
            'loaded_xrefs_resolve_inside_package': all(
                not n['database_present'] or (
                    Path(n.get('resolved_path', n['database']['original_filename'])).parent == root / 'isolated'
                    and Path(n['database']['original_filename']).parent == root / 'isolated')
                for n in reopened['graph']['nodes']),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--variant', choices=('restore', 'raw', 'restore-wblock', 'native-dxf'), default='restore')
    parser.add_argument('--name', default=f'run-{uuid.uuid4().hex[:12]}')
    parser.add_argument('--clean', action='store_true', help='No fixture edits; explicitly measure clean DBMOD 0 before/after')
    args = parser.parse_args()
    if Path(args.name).name != args.name or args.name in ('.', '..'):
        parser.error('Simple new directory name required')
    OUTPUT.mkdir(exist_ok=True)
    root = OUTPUT / args.name
    root.mkdir()
    receipt = {'root': str(root), 'variant': args.variant, 'clean': args.clean, 'disk_free_before': shutil.disk_usage(root).free, 'engines': {}}
    print(str(root), flush=True)
    source_hashes = {}
    try:
        if receipt['disk_free_before'] < 2_000_000_000:
            raise RuntimeError('Less than 2 GB free')
        bundle = build(root)
        receipt['native_binary_sha256'] = digest(bundle / 'Contents/MacOS/CaptureSession')
        receipt['native_source_sha256'] = digest(Path(__file__).with_name('capture_session_probe.cpp'))
        receipt['engines']['seed'] = core(root, bundle, '01-seed', 'seed')
        # Synthetic unavailable reference; retain its bytes outside the referenced path.
        (root / 'source/missing.dwg').rename(root / 'source/missing.held')
        source_hashes = {p.name: digest(p) for p in (root / 'source').iterdir() if p.is_file()}
        receipt['source_hashes_before'] = source_hashes
        receipt['engines']['capture'] = core(root, bundle, '02-capture', 'capture', root / 'source/host.dwg', args.variant, args.clean)
        receipt['engines']['repath'] = core(root, bundle, '03-repath', 'repath')
        # Prevent successful reopening from silently loading the original scratch paths.
        (root / 'source').rename(root / 'sealed-source')
        receipt['engines']['reopen'] = core(root, bundle, '04-reopen', 'inspect', root / 'isolated/host.dwg', 'reopened')
        receipt['capture'] = evaluate(root, args.clean, args.variant)
        dump(root / 'capture-receipt.json', receipt['capture'])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        receipt['error'] = str(error)
    finally:
        source = root / ('sealed-source' if (root / 'sealed-source').exists() else 'source')
        receipt['source_files_unchanged'] = bool(source_hashes) and all((source / name).is_file() and digest(source / name) == sha for name, sha in source_hashes.items())
        receipt['disk_free_after'] = shutil.disk_usage(root).free
        receipt['checks_passed'] = not receipt.get('error') and receipt['source_files_unchanged'] and all(receipt.get('capture', {}).get('checks', {}).values())
        dump(root / 'receipt.json', receipt)
    print(json.dumps({k: v for k, v in receipt.items() if k not in ('capture', 'engines', 'source_hashes_before')}, indent=2), flush=True)
    if 'capture' in receipt:
        print(json.dumps(receipt['capture']['checks'], indent=2), flush=True)
    return int(not receipt['checks_passed'])


if __name__ == '__main__':
    raise SystemExit(main())
