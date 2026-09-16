"""Collect portable evidence from the isolated macOS comparison."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess

from ezdxf.acis import api

repo = Path(__file__).resolve().parents[2]
lab = repo / '.runtime/cad-comparison-20260916'
dest = repo / 'docs/implementation/2026-09-16-unpatched-cad-comparison'
dest.mkdir(parents=True, exist_ok=True)

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

result = dict(platform=platform.platform(), machine=platform.machine(),
    source_sha256=digest(repo / 'fixtures/cad/parkovaya/parkovaya-apot-original.dwg'),
    source_bytes=26293408, xrefs_included=False, runtime_changed=False,
    benchmark=json.loads((lab / 'benchmark-summary.json').read_text()),
    inspections={}, sources={}, binaries={}, ezdxf_acis_load={})
for name, source in [('libredwg', lab / 'libredwg'), ('acadrust', repo / '.runtime/cad-patch-lab/acadrust')]:
    result['sources'][name] = dict(revision=subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip(),
        status=subprocess.check_output(['git', '-C', str(source), 'status', '--porcelain'], text=True))
for name in ('baseline', 'experimental', 'acadrust', 'oda'):
    info = json.loads((lab / f'{name}-inspection.json').read_text())
    info['output_sha256'] = digest(repo / info['source'])
    result['inspections'][name] = info
    # Bind each timed output to the output inspected for correctness. ODA may
    # update metadata, so unequal hashes do not alone mean geometry differs.
    for run in result['benchmark'][name]['runs']:
        command = run['command']
        if name == 'oda':
            output = Path(command[2]) / 'parkovaya-apot-original.dxf'
        elif name == 'acadrust':
            output = Path(command[2])
        else:
            output = Path(command[3])
        run['output_sha256'] = digest(output)
        run['byte_identical_to_inspected'] = run['output_sha256'] == info['output_sha256']
for name, binary in [('baseline', lab / 'baseline/dwg2dxf'), ('experimental', lab / 'experimental/dwg2dxf'),
                     ('acadrust', lab / 'cargo-target/release/cad-probe'),
                     ('oda', Path('/private/tmp/green-atlas-oda-20260916/ODAFileConverter.app/Contents/MacOS/ODAFileConverter'))]:
    result['binaries'][name] = dict(sha256=digest(binary))
for path in sorted(lab.glob('acadrust.dxf.*.sab')):
    try:
        bodies = api.load(path.read_bytes())
        result['ezdxf_acis_load'][path.name] = dict(loaded=True, bodies=len(bodies))
    except Exception as error:
        result['ezdxf_acis_load'][path.name] = dict(loaded=False, error=f'{type(error).__name__}: {error}')
with (dest / 'results.json').open('x', encoding='utf8') as stream:
    json.dump(result, stream, indent=2, ensure_ascii=False)
    stream.write('\n')
print(json.dumps({name: [r['byte_identical_to_inspected'] for r in data['runs']] for name, data in result['benchmark'].items()}))
