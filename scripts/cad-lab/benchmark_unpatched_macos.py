"""Sequential converter comparison after all builds; no installation or project writes."""
import json
from pathlib import Path
import statistics
import subprocess
import sys

repo = Path(__file__).resolve().parents[2]
lab = repo / '.runtime/cad-comparison-20260916'
source = repo / 'fixtures/cad/parkovaya/parkovaya-apot-original.dwg'
oda = Path('/private/tmp/green-atlas-oda-20260916/ODAFileConverter.app/Contents/MacOS/ODAFileConverter')
results = {}
for name in ('baseline', 'experimental', 'acadrust', 'oda'):
    runs = []
    for index in range(1, 4):
        dest = lab / 'benchmarks' / f'{name}-{index}'
        dest.mkdir(parents=True, exist_ok=False)
        output = dest / 'parkovaya-apot-original.dxf'
        if name in ('baseline', 'experimental'):
            cmd = [str(lab / name / 'dwg2dxf'), '-v1', '-o', str(output), str(source)]
        elif name == 'acadrust':
            cmd = [str(lab / 'cargo-target/release/cad-probe'), str(source), str(output)]
        else:
            cmd = [str(oda), str(source.parent), str(dest), 'ACAD2018', 'DXF', '0', '0', source.name]
        report = dest / 'run.json'
        completed = subprocess.run([sys.executable, str(repo / 'scripts/cad-lab/run_bounded.py'), str(report), '--', *cmd])
        data = json.loads(report.read_text())
        data['output_exists'] = output.is_file()
        data['output_bytes'] = output.stat().st_size if output.is_file() else 0
        runs.append(data)
        if completed.returncode:
            break
    results[name] = dict(runs=runs, median_seconds=statistics.median(r['elapsed_seconds'] for r in runs),
                         max_rss_bytes=max(r['peak_tree_rss_bytes'] for r in runs))
with (lab / 'benchmark-summary.json').open('x') as stream:
    json.dump(results, stream, indent=2)
print(json.dumps({name: {k: v for k, v in data.items() if k != 'runs'} for name, data in results.items()}, indent=2))
