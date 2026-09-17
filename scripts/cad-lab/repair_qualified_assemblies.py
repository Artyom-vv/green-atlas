"""Re-run only qualified independent graphs with the corrected SDK section setup.

Each child has its own 300 s / 4 GiB budget, one at a time. Original drawings,
previous evidence and application projects are never changed.
"""
import json
from pathlib import Path
import subprocess
import sys


def main(census_path: Path, output: Path):
    output.mkdir(parents=True, exist_ok=False)
    rows = json.loads(census_path.read_text(encoding='utf8'))
    records = []
    for item in rows:
        if not item['suspect_missing_binary_records']:
            continue
        group = Path(item['source']).parent
        profile = json.loads((group / 'profile.json').read_text(encoding='utf8'))
        destination = output / group.name
        destination.mkdir()
        command = [sys.executable, 'scripts/cad-lab/run_bounded.py', str(destination/'process.json'), '--',
                   sys.executable, 'scripts/cad-lab/assemble_dxf_fixture.py', profile['package'], profile['root'],
                   str(destination/'full.dxf'), str(destination/'assembly.json')]
        process = subprocess.run(command, capture_output=True, text=True)
        record = {'street':item['street'],'root':item['root'],'group':group.name,
                  'exit_code':process.returncode,'directory':str(destination),
                  'stdout':process.stdout,'stderr':process.stderr}
        records.append(record)
        (output/'summary.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps({'street':item['street'],'exit_code':process.returncode}),flush=True)


if __name__ == '__main__':
    main(*map(Path, sys.argv[1:]))
