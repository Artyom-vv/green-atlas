"""Read converted CAD with ezdxf and inspect actual ACIS payload availability."""
from collections import Counter
import json
from pathlib import Path
import sys
import ezdxf
from ezdxf.acis import api as acis

doc = ezdxf.readfile(sys.argv[1])
regions = []
for entity in doc.entitydb.values():
    if entity.is_alive and entity.dxftype() == 'REGION':
        item = dict(handle=entity.dxf.handle, layer=entity.dxf.layer,
            sab_bytes=len(entity.sab), sat_lines=len(entity.sat))
        try:
            bodies = acis.load(entity.sab or entity.sat)
            meshes = [mesh for body in bodies for mesh in acis.mesh_from_body(body)]
            item['acis_bodies'] = len(bodies)
            item['flat_meshes'] = [{'vertices':[list(v) for v in mesh.vertices],
                                    'faces':[list(f) for f in mesh.faces]} for mesh in meshes]
            item['scope'] = 'existing ezdxf flat-face extraction; not proof of full curved topology or placement constraints'
        except Exception as error:
            item['acis_error'] = f'{type(error).__name__}: {error}'
        regions.append(item)
report = dict(dxf_version=doc.dxfversion,
    entities=dict(Counter(e.dxftype() for e in doc.entitydb.values() if e.is_alive)),
    regions=regions,
    materials=[dict(name=n,handle=m.dxf.handle) for n,m in doc.materials],
    acdsdata_records=sum(1 for _ in doc.acdsdata.acdsrecords))
Path(sys.argv[2]).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(report,ensure_ascii=True))
