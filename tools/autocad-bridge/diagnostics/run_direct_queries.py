"""Run native BRep queries in an isolated AutoCAD process, without CAD export.

The JSON case manifest identifies source handles and explicitly labelled controls.
Only small query answers are serialized; no calculation polygons are constructed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

CORE = Path('/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app/Contents/Helpers/'
            'AcCoreConsole.app/Contents/MacOS/AcCoreConsole')


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def quoted(path: Path | str) -> str:
    return json.dumps(str(path), ensure_ascii=False)


def run_core(root: Path, drawing: Path, script: Path, timeout: int) -> dict:
    profile = root / 'profile'
    profile.mkdir()
    command = ['/usr/bin/arch', '-x86_64', str(CORE), '/i', str(drawing),
               '/s', str(script), '/isolate', f'ga-direct-{root.name}', str(profile)]
    started = time.monotonic()
    stopped = False
    with (root / 'core.log').open('wb') as log:
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        completed = None
        while child.poll() is None:
            if (root / 'completed').is_file() and completed is None:
                completed = time.monotonic()
            if (time.monotonic() - started > timeout
                    or (completed is not None and time.monotonic() - completed > 15)):
                # Only the process group created by this experiment is stopped.
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
                stopped = True
                break
            time.sleep(0.25)
    return {'exit_code': child.returncode, 'commands_completed': completed is not None
            or (root / 'completed').is_file(), 'owned_process_terminated': stopped,
            'wall_seconds': time.monotonic() - started}


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    bundle = args.bundle.resolve(strict=True)
    cases = json.loads(args.cases.read_text())
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error('Output must be empty; existing experiments are never overwritten')
    if source.suffix.lower() not in {'.dxf', '.dwg'}:
        parser.error('Input must be AutoCAD DWG or DXF')
    if shutil.disk_usage(root).free < source.stat().st_size + 300_000_000:
        parser.error('Not enough space for an isolated drawing copy')
    before = digest(source)
    drawing = root / ('Drawing' + source.suffix.lower())
    shutil.copy2(source, drawing)
    staged_bundle = root / 'DirectQueries.dbx'
    shutil.copytree(bundle, staged_bundle)
    subprocess.run(['codesign', '--verify', '--strict', str(staged_bundle)], check=True)
    request = [str(drawing), str(root / 'native.json'), str(len(cases))]
    for case in cases:
        chain, controls = case.get('chain', []), case.get('controls', [])
        request += [case['name'], case['handle'], str(len(chain)), *chain,
                    str(case.get('grid', 21)), str(case.get('repeats', 5)),
                    str(case.get('clearance', 1)), str(len(controls))]
        for control in controls:
            request.append(' '.join(map(str, [*control['point'], control['expected'], control['label']])))
    (root / 'request.txt').write_text('\n'.join(request) + '\n')
    (root / 'cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n')
    script = root / 'query.scr'
    script.write_text(
        f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload {quoted(staged_bundle)})\nGADIRECTFILE\n{root / "request.txt"}\n'
        f'(setq gaDone (open {quoted(root / "completed")} "w")) (write-line "complete" gaDone) (close gaDone)\n'
        '_QUIT\n_Y\n\n', encoding='utf-8')
    receipt = {'source': str(source), 'source_sha256': before,
               'cases_sha256': digest(args.cases),
               'native_binary_sha256': digest(staged_bundle / 'Contents/MacOS/GreenAtlasBridge')}
    try:
        receipt['engine'] = run_core(root, drawing, script, args.timeout)
        report = json.loads((root / 'native.json').read_text())
        receipt['report_bytes'] = (root / 'native.json').stat().st_size
        receipt['units_code'] = report['units_code']
        receipt['database_load_ms'] = report['native_database_load_ms']
        receipt['cases'] = [{key: row[key] for key in (
            'name', 'error', 'query_count', 'prepare_ms', 'native_edges', 'batch_ms',
            'controls_passed', 'controls_failed', 'repeat_stable', 'unknown',
            'instance_definition_membership_mismatches', 'blocked_inside_or_edge',
            'blocked_clearance', 'clear_of_selected_object_only', 'batch_native_containment_ms',
            'batch_native_closest_point_ms', 'alternative_closestPointTo_batch_ms',
            'alternative_mismatches', 'alternative_max_distance_delta', 'exact_gelib_curves',
            'gelib_batch_ms', 'gelib_mismatches', 'gelib_max_distance_delta') if key in row}
            for row in report['cases']]
        receipt['report_source_unchanged'] = report['source_unchanged']
        receipt['report_source_matches'] = report['source_sha256'] == before
        receipt['case_expectations_met'] = []
        for case, actual in zip(cases, report['cases'], strict=True):
            expected_error = case.get('expected_error_prefix')
            if expected_error:
                passed = actual.get('error', '').startswith(expected_error)
            else:
                passed = (not actual.get('error') and actual.get('controls_passed', 0) > 0
                          and actual.get('controls_failed') == 0 and actual.get('unknown') == 0
                          and actual.get('repeat_stable') is True
                          and actual.get('instance_definition_membership_mismatches') == 0
                          and actual.get('alternative_mismatches', 0) == 0
                          and actual.get('gelib_mismatches', 0) == 0)
            receipt['case_expectations_met'].append({'name': case['name'], 'passed': passed,
                                                     'expected_error_prefix': expected_error})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        receipt['error'] = str(error)
    finally:
        receipt['source_unchanged'] = digest(source) == before == digest(drawing)
        receipt['experiment_expectations_met'] = (
            not receipt.get('error') and receipt['source_unchanged']
            and receipt.get('report_source_unchanged') and receipt.get('report_source_matches')
            and receipt.get('engine', {}).get('commands_completed')
            and len(receipt.get('case_expectations_met', [])) == len(cases)
            and all(row['passed'] for row in receipt.get('case_expectations_met', [])))
        (root / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    # A recorded native rejection is an experimental result, not a missing report.
    return int(not receipt['experiment_expectations_met'])


if __name__ == '__main__':
    raise SystemExit(main())
