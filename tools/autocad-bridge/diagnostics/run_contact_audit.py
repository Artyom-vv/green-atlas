"""Native topology research on an isolated copy, never on the live project."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

from run_direct_queries import digest, quoted, run_core


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ('source', 'inventory', 'review', 'bundle', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--boundary-control', action='store_true')
    parser.add_argument('--boundary-real', action='store_true')
    parser.add_argument('--faces', action='store_true')
    parser.add_argument('--connector-cases', type=Path)
    parser.add_argument('--explode', action='store_true')
    parser.add_argument('--connector-limit', type=float, default=.02)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        parser.error('Output must be empty')
    source = args.source.resolve()
    drawing = root / ('Drawing' + source.suffix)
    source_sha = digest(source)
    shutil.copy2(source, drawing)
    references = []
    for original in sorted((source.parent / '00.3_Ссылки').glob('*.dwg')):
        staged = root / '00.3_Ссылки' / original.name
        staged.parent.mkdir(exist_ok=True)
        subprocess.run(['/bin/cp', '-c', '-p', str(original), str(staged)], check=True)
        references.append({'source': str(original), 'staged': str(staged), 'sha256': digest(original)})
    bundle = root / 'ContactAudit.dbx'
    shutil.copytree(args.bundle, bundle)
    inventory = json.loads(args.inventory.read_bytes())['objects']
    review = json.loads(args.review.read_bytes())
    targets = {item['route'] for item in review['items']}
    bounds = [o['bounds'] for o in inventory if o['route'] in targets]
    candidates = []
    for obj in inventory:
        box = obj['bounds']
        if not obj['curve'] or not box or not obj['route'].startswith('6E16/'):
            continue
        if any(box[0] <= b[2]+2 and box[2] >= b[0]-2
               and box[1] <= b[3]+2 and box[3] >= b[1]-2 for b in bounds):
            candidates.append(obj)
    request = root / 'request.txt'
    request.write_text('\n'.join([
        str(root / 'native.json'), str(len(targets)), *sorted(targets),
        str(len(candidates)), *[v for o in candidates for v in (o['route'], o['layer'])], '',
    ]))
    commands = f'GACONTACTAUDIT\n{request}\n'
    if args.faces:
        candidates = [o for o in inventory if o['curve'] and o['route'].startswith('6E16/')
            and o['route'].count('/') == 1 and o['layer'].split('|')[-1] in {'Здания', 'Граница заказа'}]
        connectors = json.loads(args.connector_cases.read_bytes()) if args.connector_cases else []
        request.write_text('\n'.join([
            str(root / 'native.json'), str(len(candidates)),
            *[v for o in candidates for v in (o['route'], o['layer'])],
            str(int(args.explode)), str(args.connector_limit), str(len(connectors)),
            *[value for row in connectors for value in row], '',
        ]))
        commands = f'GAFACEAUDIT\n{request}\n'
    if args.boundary_control:
        # All edits below affect only the disposable drawing copy and are not saved.
        commands = (
            '(command "_.UCS" "_World")\n'
            '(command "_.RECTANG" "0,0" "10,10")\n'
            '(command "_.ZOOM" "_Window" "-1,-1" "11,11")\n'
            f'GABOUNDARYCONTROL\n{root / "native.json"}\n'
        )
    if args.boundary_real:
        commands = f'GABOUNDARYREAL\n{root / "native.json"}\n'
    script = root / 'query.scr'
    script.write_text(
        f'(setvar "TRUSTEDPATHS" {quoted(str(root)+"/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload {quoted(bundle)})\n{commands}'
        f'(setq gaDone (open {quoted(root / "completed")} "w")) (close gaDone)\n'
        '_QUIT\n_Y\n\n'
    )
    receipt = {'scope': 'isolated native research; no source or project writes',
               'source': str(source), 'source_sha256': source_sha,
               'inventory_sha256': digest(args.inventory),
               'binary_sha256': digest(bundle / 'Contents/MacOS/GreenAtlasBridge'),
               'candidate_count': len(candidates), 'target_count': len(targets),
               'references': references}
    try:
        receipt['engine'] = run_core(root, drawing, script, 120)
        native = json.loads((root / 'native.json').read_bytes())
        receipt['result'] = {k: v for k, v in native.items()
                             if k not in {'targets', 'curves', 'errors', 'pieces', 'faces'}}
        for field in ('pieces', 'faces'):
            if field in native:
                receipt['result'][field + '_count'] = len(native[field])
        receipt['error_count'] = len(native.get('errors', []))
    except (OSError, ValueError) as error:
        receipt['error'] = str(error)
    finally:
        receipt['source_unchanged'] = source_sha == digest(source) == digest(drawing)
        receipt['references_unchanged'] = all(digest(Path(r['source'])) == r['sha256']
            == digest(Path(r['staged'])) for r in references)
        (root / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in receipt.items() if k != 'references'}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
