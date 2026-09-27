"""Extract upgraded upstream payloads, keeping original topology expectations."""
import hashlib
import json
from pathlib import Path
import ezdxf

root=Path(__file__).resolve().parents[2]
lab=root/'.runtime/cad-extended-20260916'
original={r['name']:r for r in json.loads((lab/'cases.json').read_text())}
rows=[]
out=lab/'oda-inputs'
out.mkdir(exist_ok=False)
for filename in ['3dsolids.dxf','torus_r2010.dxf']:
    source=lab/'oda-upgraded'/filename
    for e in ezdxf.readfile(source).modelspace().query('3DSOLID REGION BODY'):
        name=source.stem+'-'+e.dxf.handle
        before=original[name]
        payload=e.sab or '\n'.join(e.sat).encode()
        path=out/(name+('.sab' if e.sab else '.sat'))
        path.write_bytes(payload)
        rows.append({**before,'name':'oda-'+name,'path':str(path),
                     'sha256':hashlib.sha256(payload).hexdigest(),
                     'origin':'ODA ACAD2018 upgrade of '+before['origin'],
                     'original_payload_sha256':before['sha256']})
rows.append({**original['upstream-dice'],'name':'upstream-dice-metrics-retry'})
(lab/'oda-cases.json').write_text(json.dumps(rows,indent=2)+'\n')
print(json.dumps([dict(name=r['name'],path=r['path']) for r in rows]))
