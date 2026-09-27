"""Sequential corpus runner; process budgets include real CAD dependencies."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
old = root/'.runtime/cad-comparison-20260916'
lab = root/'.runtime/cad-extended-20260916'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('manifest', nargs='?', default='cases.json', type=Path)
parser.add_argument('--output-dir', type=Path, default=lab)
parser.add_argument('--dependency-root', type=Path, default=old)
parser.add_argument('--cad-python', type=Path, default=old/'FreeCAD.app/Contents/Resources/bin/python')
args = parser.parse_args()
env = {**os.environ, 'CAD_ACIS_LAB':str(args.dependency_root.resolve()),
       'QT_QPA_PLATFORM':'offscreen'}
mac_lib = args.dependency_root/'FreeCAD.app/Contents/Resources/lib'
if mac_lib.is_dir():
    env['PYTHONPATH'] = str(mac_lib)
rows = []
manifest = args.manifest if args.manifest.is_absolute() else lab/args.manifest
lab = args.output_dir
lab.mkdir(parents=True, exist_ok=True)
cases = json.loads(manifest.read_text(encoding='utf8'))
report_path = lab/(manifest.stem+'-results.json')
if report_path.exists():
    raise FileExistsError(report_path)
for case in cases:
    name = case['name']
    if Path(name).name != name or name in {'.', '..'}:
        raise ValueError('Case name must be a single path component')
    for target in (lab/name, lab/(name+'-process.json')):
        if target.exists():
            raise FileExistsError(target)
for i, case in enumerate(cases):
    name=case['name']
    process=lab/(name+'-process.json')
    command=[sys.executable,str(root/'scripts/cad-lab/run_bounded.py'),
             '--seconds','90','--memory-mib','2048',str(process),'--',
             str(args.cad_python),'-u',
             str(root/'scripts/cad-lab/extended_acis_case.py'),str(manifest),str(i),str(lab/name)]
    subprocess.run(command,env=env,check=False,stdout=subprocess.DEVNULL)
    measured=json.loads(process.read_text(encoding='utf8'))
    result_path=lab/name/'result.json'
    result=json.loads(result_path.read_text(encoding='utf8')) if result_path.exists() else {'gate':'failed','error':'no result'}
    rows.append(dict(name=name,process=measured,result=result))
    print(json.dumps(dict(name=name,process=measured['status'],gate=result.get('gate'),
                          checks=result.get('checks'),error=result.get('error'))),flush=True)
with report_path.open('x', encoding='utf8') as f:
    json.dump(rows,f,indent=2)
sys.exit(0 if rows and all(row['process']['status'] == 'completed' and
                         row['result'].get('gate') == 'passed' for row in rows) else 1)
