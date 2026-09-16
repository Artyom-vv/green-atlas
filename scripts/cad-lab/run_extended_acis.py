"""Sequential corpus runner; process budgets include real CAD dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
old = root/'.runtime/cad-comparison-20260916'
lab = root/'.runtime/cad-extended-20260916'
env = {**os.environ, 'CAD_ACIS_LAB':str(old),
       'PYTHONPATH':str(old/'FreeCAD.app/Contents/Resources/lib'),
       'QT_QPA_PLATFORM':'offscreen'}
rows = []
manifest=lab/(sys.argv[1] if len(sys.argv)>1 else 'cases.json')
for i, case in enumerate(json.loads(manifest.read_text())):
    name=case['name']
    process=lab/(name+'-process.json')
    command=[sys.executable,str(root/'scripts/cad-lab/run_bounded.py'),
             '--seconds','90','--memory-mib','2048',str(process),'--',
             str(old/'FreeCAD.app/Contents/Resources/bin/python'),'-u',
             str(root/'scripts/cad-lab/extended_acis_case.py'),str(manifest),str(i),str(lab/name)]
    subprocess.run(command,env=env,check=False,stdout=subprocess.DEVNULL)
    measured=json.loads(process.read_text())
    result_path=lab/name/'result.json'
    result=json.loads(result_path.read_text()) if result_path.exists() else {'gate':'failed','error':'no result'}
    rows.append(dict(name=name,process=measured,result=result))
    print(json.dumps(dict(name=name,process=measured['status'],gate=result.get('gate'),
                          checks=result.get('checks'),error=result.get('error'))),flush=True)
with (lab/(manifest.stem+'-results.json')).open('x') as f:
    json.dump(rows,f,indent=2)
