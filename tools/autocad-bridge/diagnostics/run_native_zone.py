"""Run a bounded native zone experiment; optional AutoCAD conversion of utility DWG.

Original CAD files and user projects remain unchanged. No CAD geometry parser.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from run_direct_queries import digest, quoted, run_core


def convert_dwg(root: Path, drawing: Path, output: Path, audit: bool = False, purge_regapps: bool = False) -> dict:
    """AutoCAD DXFOUT on an owned copy; no product exporter is loaded."""
    root.mkdir()
    script = root / 'convert.scr'
    # Legacy script input can decode literal UTF-8 filenames with a codepage.
    # Keep the Unicode directory in AutoCAD itself; use an ASCII output leaf.
    if output.parent == drawing.parent and output.name.isascii():
        destination = f'(strcat (getvar "DWGPREFIX") {quoted(output.name)})'
    elif str(output).isascii():
        destination = quoted(output)
    else:
        raise ValueError('DXFOUT needs an ASCII leaf in the drawing directory')
    script.write_text('(setvar "FILEDIA" 0)\n'
                      + ('(command "_.AUDIT" "_Y")\n' if audit else '') +
                      ('(command "_.-PURGE" "_Regapps" "*" "_N")\n' if purge_regapps else '') +
                      f'(command "_.DXFOUT" {destination} "16")\n'
                      f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)\n'
                      '_QUIT\n_Y\n\n')
    engine = run_core(root, drawing, script, 120)
    engine['audit_before_dxfout'] = audit
    engine['purge_unused_regapps_before_dxfout'] = purge_regapps
    (root / 'engine.json').write_text(json.dumps(engine, indent=2) + '\n')
    if not output.is_file():
        raise ValueError('AutoCAD did not produce DXF')
    return engine


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    for name in ('source', 'networks', 'bundle', 'case', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error('Output must be empty')
    if shutil.disk_usage(root).free < 400_000_000:
        parser.error('Need 400 MB free for bounded native experiment')
    source, networks = args.source.resolve(strict=True), args.networks.resolve(strict=True)
    if source.suffix.lower() not in {'.dxf', '.dwg'} or networks.suffix.lower() not in {'.dxf', '.dwg'}:
        parser.error('Inputs must be DWG/DXF; DWG is converted by AutoCAD before querying DXF')
    original_hashes = [digest(source), digest(networks)]
    drawing = root / 'Drawing.dxf'
    source_copy = root / ('Drawing' + source.suffix.lower())
    network_copy = root / ('Networks' + networks.suffix.lower())
    network_dxf = root / 'Networks.dxf'
    shutil.copy2(source, source_copy)
    shutil.copy2(networks, network_copy)
    bundle = root / 'NativeZone.dbx'
    shutil.copytree(args.bundle, bundle)
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
    case = json.loads(args.case.read_text())
    receipt = {'source': str(source), 'networks': str(networks), 'original_sha256': original_hashes,
               'native_binary_sha256': digest(bundle / 'Contents/MacOS/GreenAtlasBridge')}
    try:
        if source.suffix.lower() == '.dwg':
            receipt['source_conversion_engine'] = convert_dwg(root / 'convert-source', source_copy, drawing)
        if networks.suffix.lower() == '.dwg':
            receipt['conversion_engine'] = convert_dwg(root / 'convert', network_copy, network_dxf)
        source_dxf_hash = digest(drawing)
        network_dxf_hash = digest(network_dxf)
        request = [str(drawing), str(network_dxf), str(root / 'native.json'), case['mode'],
                   case['building_handle'], case['site_handle'], case.get('utility_handle', ''),
                   ' '.join(map(str, case['window'])), ' '.join(map(str, case['policy'])),
                   str(len(case.get('controls', [])))]
        for c in case.get('controls', []):
            request.append(' '.join(map(str, [*c['point'], c['expected'], c['reason'], c['label']])))
        (root / 'request.txt').write_text('\n'.join(request) + '\n')
        shutil.copy2(args.case, root / 'case.json')
        script = root / 'query.scr'
        script.write_text(f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})\n(setvar "FILEDIA" 0)\n'
                          f'(arxload {quoted(bundle)})\nGAZONEFILE\n{root / "request.txt"}\n'
                          f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)\n'
                          '_QUIT\n_Y\n\n')
        receipt['query_engine'] = run_core(root, drawing, script, 180)
        report = json.loads((root / 'native.json').read_text())
        receipt['source_dxf_sha256'] = source_dxf_hash
        receipt['source_dxf_unchanged'] = digest(drawing) == source_dxf_hash
        receipt['network_dxf_sha256'] = network_dxf_hash
        receipt['network_dxf_unchanged'] = digest(network_dxf) == network_dxf_hash
        receipt['native_sources_unchanged'] = report['sources_unchanged']
        receipt['report_source_matches'] = (report['source_sha256'] == source_dxf_hash
                                            and report['network_sha256'] == network_dxf_hash)
        receipt['summary'] = {k: v for k, v in report.items() if k not in {'answers', 'inventory', 'controls', 'selected'}}
        receipt['summary']['selected_count'] = len(report.get('selected', []))
        receipt['case_expectations_met'] = (case['mode'] == 'inventory' or (
            report.get('controls_failed') == 0 and report.get('repeat_mismatches') == 0
            and report.get('manual_recheck_mismatches') == 0
            and report.get('height_query_mismatches') == 0
            and report.get('unavailable_observations') == 0
            and report.get('reason_counts', {}).get('native_query_unavailable', 0) == 0
            and (not case.get('require_native_line_elevation_check') or (
                report.get('native_line_elevation_check', {}).get('status') == 0
                and report['native_line_elevation_check']['queries'] > 0
                and report['native_line_elevation_check']['mismatches'] == 0))
        ))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        receipt['error'] = str(error)
    finally:
        receipt['originals_and_staged_inputs_unchanged'] = (
            original_hashes == [digest(source), digest(networks)] == [digest(source_copy), digest(network_copy)])
        receipt['experiment_expectations_met'] = (not receipt.get('error')
            and receipt['originals_and_staged_inputs_unchanged']
            and receipt.get('native_sources_unchanged') and receipt.get('network_dxf_unchanged')
            and receipt.get('source_dxf_unchanged')
            and receipt.get('report_source_matches') and receipt.get('case_expectations_met')
            and receipt.get('query_engine', {}).get('commands_completed')
            and (networks.suffix.lower() != '.dwg'
                 or receipt.get('conversion_engine', {}).get('commands_completed'))
            and (source.suffix.lower() != '.dwg'
                 or receipt.get('source_conversion_engine', {}).get('commands_completed')))
        (root / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False, indent=2), flush=True)
    return int(not receipt['experiment_expectations_met'])


if __name__ == '__main__':
    raise SystemExit(main())
