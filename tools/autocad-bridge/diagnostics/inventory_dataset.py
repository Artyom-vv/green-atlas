"""Inventory CAD inputs and link evidence; never infer acceptance from files."""
import argparse
import hashlib
import json
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    dataset = root / 'fixtures/cad/lct-dataset/Датасет/Пилотный проект 20 улиц'
    audit_path = root / 'artifacts/geometry-audit-20260922/report.json'
    audit = json.loads(audit_path.read_bytes())
    probes = root / 'artifacts/native-containment-20260922'
    results = list(probes.glob('**/native-report.json'))
    streets = []
    for directory in sorted(dataset.iterdir()):
        if not directory.is_dir():
            continue
        files = []
        for file in sorted(directory.rglob('*')):
            if any(part in ('PaxHeader', 'PaxHeaders', '__MACOSX') for part in file.parts) or file.name.startswith('._'):
                continue  # Archive metadata is not a drawing, even with .dwg suffix.
            if file.is_file() and file.suffix.lower() in ('.dwg', '.dxf'):
                files.append({'path': str(file.relative_to(root)), 'bytes': file.stat().st_size,
                              'format': file.suffix.lower()[1:]})
        evidence, hash_cache = [], {}
        for report in results:
            data = json.loads(report.read_bytes())
            source = Path(data['source'])
            if not source.is_relative_to(directory):
                continue
            if source not in hash_cache:
                hash_cache[source] = sha256(source) if source.is_file() else None
            evidence.append({'report': str(report.relative_to(root)),
                             'source': str(source.relative_to(root)),
                             'recorded_sha256': data['source_sha256'],
                             'current_sha256': hash_cache[source],
                             'source_matches': hash_cache[source] == data['source_sha256'],
                             'case_count': len(data['cases']),
                             'scope': 'selected definitions only, not entire street acceptance'})
        baseline = [d for d in audit['drawings'] if Path(d['source']).is_relative_to(directory)]
        streets.append({'street': directory.name, 'inputs': files, 'native_probes': evidence,
                        'baseline_reports': [{'path': str(audit_path.relative_to(root)),
                                              'source': d['source'], 'plugin_version': d['plugin_version'],
                                              'status': d['status']} for d in baseline],
                        'whole_street_acceptance': 'not_verified',
                        'instance_transforms': 'not_verified_in_current_experiment',
                        'native_clearances': 'not_verified_in_current_experiment',
                        'end_to_end_placement': 'not_verified_in_current_experiment'})
    payload = {'scope': 'filesystem inventory and evidence index, not DWG parsing',
               'streets': streets}
    with args.output.open('x') as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    for s in streets:
        print(s['street'], 'DWG', sum(f['format'] == 'dwg' for f in s['inputs']),
              'DXF', sum(f['format'] == 'dxf' for f in s['inputs']),
              'probe reports', len(s['native_probes']))
    print('street directories:', len(streets))


if __name__ == '__main__':
    main()
