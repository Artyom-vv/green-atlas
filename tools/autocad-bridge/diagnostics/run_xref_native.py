"""Stage the available CAD package and inspect AutoCAD's actual loaded XREFs.

Copy only, no original CAD saves, no alternate parser and no capture converter.
The native ledger distinguishes graph definitions from model-space instances.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from run_direct_queries import digest, quoted, run_core
from run_native_zone import convert_dwg


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    for name in ('package', 'source', 'bundle', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--zone-bundle', type=Path)
    parser.add_argument('--case', type=Path)
    parser.add_argument('--dxf', action='store_true', help='AutoCAD converts staged main DWG, then fresh process opens DXF')
    parser.add_argument('--audit-copy', action='store_true', help='Explicit AUDIT on staged DWG in memory before DXFOUT; never save input')
    parser.add_argument('--purge-unused-regapps-copy', action='store_true', help='Explicit native -PURGE Regapps on test DB before DXFOUT; no geometry purge or source save')
    parser.add_argument('--clone-copies', action='store_true', help='APFS copy-on-write staging with cp -c; fail rather than copy on unsupported filesystem')
    parser.add_argument('--xref-map', type=Path, help='Explicit record/name/file manifest, using paths relative to package; in-memory only')
    parser.add_argument('--areas', action='store_true', help='Bounded native area query/transform controls in the loaded host')
    parser.add_argument('--area-cases', type=Path, help='Optional explicit routes and controls in definition coordinates')
    parser.add_argument('--window-case', type=Path, help='Native broad-phase inventory of every available leaf near an explicit XY window')
    args = parser.parse_args()
    if bool(args.zone_bundle) != bool(args.case):
        parser.error('Zone bundle and case must be provided together')
    if args.area_cases and not args.areas:
        parser.error('Area cases require --areas')
    if args.window_case and args.areas:
        parser.error('Window inventory and bounded area controls are separate measured runs')
    if args.audit_copy and not args.dxf:
        parser.error('AUDIT is only a separate DXF conversion experiment')
    if args.purge_unused_regapps_copy and (not args.dxf or args.audit_copy):
        parser.error('Regapp purge must be a separate DXF experiment, without AUDIT')
    package, source = args.package.resolve(strict=True), args.source.resolve(strict=True)
    relative_source = source.relative_to(package)
    files = sorted(p for p in package.rglob('*') if p.is_file()
                   and p.suffix.lower() in {'.dwg', '.dxf'}
                   and 'PaxHeader' not in p.parts and not p.name.startswith('._'))
    if source not in files:
        parser.error('Source must be an ordinary DWG/DXF in the selected package')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error('Output must be empty')
    staging_bytes = 0 if args.clone_copies else sum(p.stat().st_size for p in files)
    if staging_bytes + 500_000_000 > shutil.disk_usage(root).free:
        parser.error('Insufficient space; do not delete user data')
    staged = root / 'package'
    records = []
    for path in files:
        destination = staged / path.relative_to(package)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if args.clone_copies:
            subprocess.run(['/bin/cp', '-c', '-p', str(path), str(destination)], check=True)
        else:
            shutil.copy2(path, destination)
        records.append({'source': str(path), 'staged': str(destination), 'sha256': digest(path)})
    bundle = root / 'XrefLedger.dbx'
    shutil.copytree(args.bundle, bundle)
    subprocess.run(['codesign', '--verify', '--strict', str(bundle)], check=True)
    report_path = root / 'native.json'
    request = root / 'request.txt'
    lines = [str(report_path)]
    if args.window_case:
        case = json.loads(args.window_case.read_text())
        window = case['window']
        if len(window) != 4:
            parser.error('Window requires xmin,ymin,xmax,ymax')
        lines.extend(['window', ' '.join(map(str, [*window, case['padding']]))])
        if 'calculation' in case:
            calc = case['calculation']
            lines.extend(['evaluate', f"{calc['grid_step']} {calc['spacing']}",
                          calc['site_route'], str(len(calc['layer_rules']))])
            for rule in calc['layer_rules']:
                lines.extend([rule['layer'], f"{rule['role']} {rule['clearance']}"])
            lines.append(str(len(calc['blocked_controls'])))
            for point in calc['blocked_controls']:
                lines.append(' '.join(map(str, point)))
        shutil.copy2(args.window_case, root / 'window-case.json')
    if args.areas:
        cases = json.loads(args.area_cases.read_text()) if args.area_cases else []
        lines.extend(['areas', str(len(cases))])
        for case in cases:
            controls = case.get('controls', [])
            lines.extend([case['route'], str(len(controls))])
            for c in controls:
                lines.append(' '.join(map(str, [*c['point'], c['expected'], c['label']])))
        (root / 'area-cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2) + '\n')
    request.write_text('\n'.join(lines) + '\n')
    drawing = staged / relative_source
    receipt = {'source': str(source), 'package': str(package), 'files': records,
               'staging': 'APFS independent copy-on-write clones' if args.clone_copies else 'copies',
               'native_binary_sha256': digest(bundle / 'Contents/MacOS/GreenAtlasBridge')}
    try:
        inspect(args, root, drawing, receipt)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        receipt['error'] = str(error)
        conversion_receipt = root / 'convert/engine.json'
        if conversion_receipt.is_file():
            receipt['conversion_engine'] = json.loads(conversion_receipt.read_text())
    finally:
        receipt['all_sources_and_staged_unchanged'] = all(
            digest(Path(r['source'])) == r['sha256'] == digest(Path(r['staged'])) for r in records)
        receipt['inspection_completed'] = bool(not receipt.get('error') and receipt['all_sources_and_staged_unchanged']
                                            and receipt.get('drawing_unchanged')
                                            and receipt.get('engine', {}).get('commands_completed')
                                            and (not args.dxf or receipt.get('conversion_engine', {}).get('commands_completed'))
                                            and (not args.zone_bundle or receipt.get('zone_checks_passed')))
        (root / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'files'}, ensure_ascii=False, indent=2))
    return int(not receipt['inspection_completed'])


def inspect(args: argparse.Namespace, root: Path, drawing: Path, receipt: dict) -> None:
    if args.dxf:
        if drawing.suffix.lower() != '.dwg':
            raise ValueError('DXFOUT requires the main source DWG')
        output = drawing.parent / 'NativeReplay.dxf'
        if output.exists():
            raise ValueError('Reserved DXF replay name already exists')
        receipt['conversion_engine'] = convert_dwg(root / 'convert', drawing, output, audit=args.audit_copy,
                                                   purge_regapps=args.purge_unused_regapps_copy)
        drawing = output
    drawing_sha = digest(drawing)
    receipt['drawing'] = str(drawing)
    receipt['drawing_sha256'] = drawing_sha
    zone_script = ''
    if args.zone_bundle:
        zone_bundle = root / 'XrefZone.dbx'
        shutil.copytree(args.zone_bundle, zone_bundle)
        subprocess.run(['codesign', '--verify', '--strict', str(zone_bundle)], check=True)
        case = json.loads(args.case.read_text())
        if 'controls_from' in case:
            case['controls'] = json.loads(Path(case['controls_from']).read_text())['controls']
        receipt['zone_binary_sha256'] = digest(zone_bundle / 'Contents/MacOS/GreenAtlasBridge')
        request_lines = [str(drawing), str(drawing), str(root / 'zone.json'), 'evaluate',
                         case['building_handle'], case['site_handle'], case['utility_handle'],
                         ' '.join(map(str, case['window'])), ' '.join(map(str, case['policy'])),
                         str(len(case.get('controls', [])))]
        for c in case.get('controls', []):
            request_lines.append(' '.join(map(str, [*c['point'], c['expected'], c['reason'], c['label']])))
        (root / 'zone-request.txt').write_text('\n'.join(request_lines) + '\n')
        (root / 'zone-case.json').write_text(json.dumps(case, ensure_ascii=False, indent=2) + '\n')
        zone_script = f'(arxload {quoted(zone_bundle)})\nGAZONEFILE\n{root / "zone-request.txt"}\n'
    script = root / 'query.scr'
    bundle, request = root / 'XrefLedger.dbx', root / 'request.txt'
    report_path = root / 'native.json'
    repath_script = ''
    if args.xref_map:
        mappings = json.loads(args.xref_map.read_text())
        lines = [str(root / 'repath.json'), str(len(mappings))]
        for row in mappings:
            path = (root / 'package' / row['file']).resolve(strict=True)
            path.relative_to(root / 'package')
            if path.suffix.lower() not in {'.dwg', '.dxf'}:
                raise ValueError('Explicit map must point to staged CAD')
            lines.extend([row['record'], row['name'], str(path)])
        (root / 'xref-map.json').write_text(json.dumps(mappings, ensure_ascii=False, indent=2) + '\n')
        (root / 'repath-request.txt').write_text('\n'.join(lines) + '\n')
        (root / 'before-request.txt').write_text(str(root / 'before.json') + '\n')
        repath_script = f'GAXREFLEDGER\n{root / "before-request.txt"}\nGAXREFPATHS\n{root / "repath-request.txt"}\n'
    script.write_text(f'(setvar "TRUSTEDPATHS" {quoted(str(root) + "/")})\n(setvar "FILEDIA" 0)\n'
                      f'(arxload {quoted(bundle)})\n' + repath_script + f'GAXREFLEDGER\n{request}\n'
                      + zone_script +
                      f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)\n'
                      '_QUIT\n_Y\n\n')
    try:
        receipt['engine'] = run_core(root, drawing, script, 180)
        report = json.loads(report_path.read_text())
        if args.xref_map:
            receipt['repath'] = json.loads((root / 'repath.json').read_text())
            if receipt['repath'].get('error') or receipt['repath'].get('reload_status') != 0:
                raise ValueError('Native reload did not finish successfully; see repath report')
        receipt['native_summary'] = {'graph_status': report['graph']['status'],
                                    'graph_nodes': len(report['graph']['nodes']),
                                    'xref_records': len(report['records']),
                                    'xref_instances': len(report['inventory']['xref_instances']),
                                    'visited_instances': report['inventory']['visited_instances'],
                                    'issues': report['inventory']['issues'],
                                    'elapsed_ms': report['elapsed_ms']}
        controls = report['inventory'].get('native_line_controls', [])
        receipt['native_summary']['line_controls'] = len(controls)
        receipt['native_summary']['line_controls_passed'] = sum(c.get('passed', False) for c in controls)
        receipt['native_summary']['line_control_queries'] = sum(c.get('queries', 0) for c in controls)
        receipt['native_summary']['line_control_failures'] = [c for c in controls if not c.get('passed')]
        if args.window_case:
            receipt['native_summary']['window_inventory'] = {
                k: v for k, v in report['inventory']['window_inventory'].items()
                if k not in {'near', 'unlocated', 'calculation'}}
            if 'calculation' in report['inventory']['window_inventory']:
                receipt['native_summary']['window_calculation'] = {
                    k: v for k, v in report['inventory']['window_inventory']['calculation'].items()
                    if k not in {'answers', 'selected', 'object_ledger', 'controls', 'unculled_checks'}}
        if args.areas:
            areas = report['inventory']['native_area_controls']
            receipt['native_summary']['area_cases'] = len(areas)
            receipt['native_summary']['area_cases_passed'] = sum(c.get('passed', False) for c in areas)
            receipt['native_summary']['area_query_points'] = sum(c.get('query_points', 0) for c in areas)
            receipt['native_summary']['area_failures'] = [{k: v for k, v in c.items() if k != 'answers'}
                                                         for c in areas if not c.get('passed')]
            affine = [c['affine_probe'] for c in areas if 'affine_probe' in c]
            if affine:
                receipt['native_summary']['affine_cases'] = len(affine)
                receipt['native_summary']['affine_passed'] = sum(c.get('passed', False) for c in affine)
                receipt['native_summary']['affine_query_points'] = sum(c.get('query_points', 0) for c in affine)
                receipt['native_summary']['affine_failures'] = [
                    {'route': c['route'], **{k: v for k, v in c['affine_probe'].items() if k != 'answers'}}
                    for c in areas if 'affine_probe' in c and not c['affine_probe'].get('passed')]
        if args.zone_bundle:
            zone = json.loads((root / 'zone.json').read_text())
            receipt['zone_summary'] = {k: v for k, v in zone.items() if k not in {'answers', 'controls', 'selected'}}
            receipt['zone_summary']['selected_count'] = len(zone['selected'])
            receipt['zone_checks_passed'] = (zone['controls_passed'] == len(case['controls']) > 0
                and zone['controls_failed'] == 0 and zone['repeat_mismatches'] == 0
                and zone['unavailable_observations'] == 0 and zone['manual_recheck_mismatches'] == 0
                and zone['height_query_mismatches'] == 0
                and zone['native_line_elevation_check']['status'] == 0
                and zone['native_line_elevation_check']['mismatches'] == 0
                and zone['source_sha256'] == drawing_sha and zone['sources_unchanged'])
    finally:
        receipt['drawing_unchanged'] = digest(drawing) == drawing_sha


if __name__ == '__main__':
    raise SystemExit(main())
