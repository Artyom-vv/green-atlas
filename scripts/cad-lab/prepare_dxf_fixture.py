"""Offline fixture preparation with an existing CAD probe, outside the service.

Preserve the manifest's directory structure; never overwrite existing evidence.
Each converter runs separately under the existing process-tree resource guard.
Successful conversion is not a claim of CAD completeness.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--converter', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    target = args.destination.resolve()
    if target.exists():
        raise FileExistsError('Choose a fresh destination for reproducible evidence')
    entries = json.loads(args.manifest.read_text(encoding='utf8'))['files']
    inputs = []
    for entry in entries:
        relative = Path(entry['path']).relative_to('source')
        path = (source / relative).resolve()
        if not path.is_relative_to(source):
            raise ValueError('Source path escapes the manifest root')
        raw = path.read_bytes()
        if len(raw) != entry['bytes'] or hashlib.sha256(raw).hexdigest() != entry['sha256']:
            raise ValueError(f'Source differs from manifest: {relative}')
        inputs.append((entry, relative, path))
    target.mkdir(parents=True)
    converter = args.converter.resolve()
    evidence = {'source_manifest': str(args.manifest), 'converter': str(converter),
                'converter_sha256': hashlib.sha256(converter.read_bytes()).hexdigest(),
                'cad_completeness_verified': False, 'files': []}
    for index, (entry, relative, path) in enumerate(inputs):
        output = target / 'dxf' / relative.with_suffix('.dxf')
        output.parent.mkdir(parents=True, exist_ok=True)
        report = target / 'processes' / f'{index:02d}.json'
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('run_bounded.py')),
                                 str(report), '--', str(converter), str(path), str(output)],
                                capture_output=True, text=True, encoding='utf8', errors='replace')
        item = {'source': entry, 'dxf_path': str(output.relative_to(target)),
                'process_report': str(report.relative_to(target)), 'exit_code': result.returncode}
        if output.exists():
            raw = output.read_bytes()
            item.update(dxf_bytes=len(raw), dxf_sha256=hashlib.sha256(raw).hexdigest())
        evidence['files'].append(item)
        (target / 'preparation.json').write_text(json.dumps(evidence, indent=2, ensure_ascii=False)+'\n', encoding='utf8')
        print(f'{index + 1}/{len(inputs)}: exit={result.returncode}: {relative}', flush=True)
    return int(any(item['exit_code'] for item in evidence['files']))


if __name__ == '__main__':
    raise SystemExit(main())
