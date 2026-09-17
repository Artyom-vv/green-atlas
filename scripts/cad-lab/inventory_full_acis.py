"""All ACIS owners and references in a full converted drawing, read only."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import ezdxf

source, out = map(Path, sys.argv[1:])
out.mkdir(exist_ok=False)
doc=ezdxf.readfile(source)
owners={b.block_record_handle:b.name for b in doc.blocks}
acis=[]
for e in doc.entitydb.values():
    if not e.is_alive or not hasattr(e,'sab'):
        continue
    data=e.sab
    text=e.sat
    row=dict(handle=e.dxf.handle,type=e.dxftype(),layer=e.dxf.layer,
             owner=e.dxf.get('owner'),layout=owners.get(e.dxf.get('owner')),
             sab_bytes=len(data),sat_lines=len(text))
    if data or text:
        path=out/(e.dxf.handle+('.sab' if data else '.sat'))
        path.write_bytes(data or '\n'.join(text).encode())
        row.update(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    acis.append(row)
xrefs=[]
for b in doc.blocks:
    if b.block.is_xref or b.block.is_xref_overlay:
        xrefs.append(dict(name=b.name,path=b.block.dxf.get('xref_path'),flags=b.block.dxf.flags))
inserts=list(doc.modelspace().query('INSERT'))
from ezdxf.math import Matrix44
targets={r['layout'] for r in acis}
block_refs={b.name:list(b.query('INSERT')) for b in doc.blocks}
reachable=set(targets)
while True:
    expanded=reachable | {name for name,refs in block_refs.items() if any(e.dxf.name in reachable for e in refs)}
    if expanded==reachable:
        break
    reachable=expanded
instances=[]
def visit(refs, parent, chain, ancestry):
    for ref in refs:
        name=ref.dxf.name
        if name not in reachable:
            continue
        if name in ancestry:
            raise ValueError('cyclic ACIS ancestry')
        for instance in (ref.multi_insert() if ref.mcount>1 else [ref]):
            matrix=instance.matrix44() @ parent
            path=chain+[dict(handle=ref.dxf.handle,block=name,rotation=instance.dxf.rotation,
                             scale=[instance.dxf.xscale,instance.dxf.yscale,instance.dxf.zscale])]
            if name in targets:
                instances.append(dict(block=name,chain=path,matrix=list(matrix)))
            visit(block_refs.get(name,[]),matrix,path,ancestry|{name})
visit(inserts,Matrix44(),[],set())
result=dict(source=str(source),dxf_units=doc.units,acis=acis,xrefs=xrefs,
            acis_instances=instances,
            modelspace_types=dict(Counter(e.dxftype() for e in doc.modelspace())),
            all_database_types=dict(Counter(e.dxftype() for e in doc.entitydb.values() if e.is_alive)),
            inserts=len(inserts),nonuniform_inserts=sum(not e.has_uniform_scaling for e in inserts),
            rotated_inserts=sum(e.dxf.rotation!=0 for e in inserts),
            mirrored_inserts=sum(e.dxf.xscale*e.dxf.yscale*e.dxf.zscale<0 for e in inserts),
            block_units=dict(Counter(b.block_record.dxf.get('units',0) for b in doc.blocks)))
(out/'inventory.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n', encoding='utf8')
print(json.dumps(result,ensure_ascii=False))
