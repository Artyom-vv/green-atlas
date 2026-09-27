"""Preserve compact native evidence and a per-object ledger for the 54-line audit."""
import argparse
import gzip
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run', action='append', required=True, help='label=/absolute/run/path')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    review = json.loads(args.review.read_bytes())
    targets = {item['route'] for item in review['items']}
    ledger = {item['route']: {'route': item['route'], 'endpoint_distance_m': item['endpoint_distance_m'], 'passes': {}}
              for item in review['items']}
    summary = {'scope': 'native area candidates, not semantic acceptance or product integration',
               'target_count': len(targets), 'runs': {}}
    for specification in args.run:
        label, path = specification.split('=', 1)
        root = Path(path)
        raw = (root / 'native.json').read_bytes()
        data = json.loads(raw)
        receipt = json.loads((root / 'receipt.json').read_bytes())
        evidence = args.output / label
        evidence.mkdir(exist_ok=True)
        with gzip.open(evidence / 'native.json.gz', 'wb') as stream:
            stream.write(raw)
        compact_receipt = {**receipt, 'result': {k:v for k,v in receipt.get('result', {}).items()
                                               if k not in {'curves', 'pieces', 'faces', 'errors'}}}
        (evidence / 'receipt.json').write_text(json.dumps(compact_receipt, ensure_ascii=False, indent=2))
        if (root / 'core.log').is_file():
            shutil.copy2(root / 'core.log', evidence / 'core.log')
        entry = {'seconds': receipt['engine']['wall_seconds'],
                 'process_exit': receipt['engine']['exit_code'],
                 'source_unchanged': receipt['source_unchanged'],
                 'references_unchanged': receipt.get('references_unchanged')}
        if 'faces' in data:
            covered = set()
            for index, face in enumerate(data['faces']):
                routes = {r for i in face['pieces'] for r in data['pieces'][i].get('source_routes', [data['pieces'][i]['route']])}
                for target in routes & targets:
                    ledger[target]['passes'].setdefault(label, []).append({
                        'face': index, 'area_m2': face.get('area'), 'error': face.get('error'),
                        'members': sorted(routes),
                    })
                if 'area' in face:
                    covered |= routes & targets
            entry.update({'valid_faces': sum('area' in f for f in data['faces']),
                          'rejected_faces': [{'face':i, 'error':f['error'], 'pieces':len(f['pieces'])}
                                             for i,f in enumerate(data['faces']) if 'error' in f],
                          'target_lines_in_valid_faces': len(covered),
                          'remaining': sorted(targets-covered), 'connectors': data.get('connectors', []),
                          'outside_control_count': sum(c['label'] == 'outside_control'
                              for f in data['faces'] for c in f.get('controls', [])),
                          'outside_control_failures': sum(c['status'] != 0 or c['membership'] != 'outside'
                              for f in data['faces'] for c in f.get('controls', []) if c['label'] == 'outside_control')})
        if 'targets' in data:
            for target in data['targets']:
                ledger[target['route']]['native_endpoint_contacts'] = target['endpoints']
        summary['runs'][label] = entry
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (args.output / 'objects-54.json').write_text(json.dumps(list(ledger.values()), ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
