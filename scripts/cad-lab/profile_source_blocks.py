"""Identify the existing importer's large-block guard without expanding geometry."""
import json
from pathlib import Path
import sys
import ezdxf

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'apps/api'))
from app.dxf_import.adapters import _estimated_insert_component_count, MAX_EXPANDED_MINSERT_INSTANCES

doc=ezdxf.readfile(sys.argv[1])
rows=[]
for ref in doc.modelspace().query('INSERT'):
    count=_estimated_insert_component_count(ref)
    if count>MAX_EXPANDED_MINSERT_INSTANCES:
        block=doc.blocks.get(ref.dxf.name)
        rows.append(dict(handle=ref.dxf.handle,name=ref.dxf.name,layer=ref.dxf.layer,
                         estimate=count,guard=MAX_EXPANDED_MINSERT_INSTANCES,
                         direct_entities=len(block),
                         acis_handles=[e.dxf.handle for e in block if hasattr(e,'sab')]))
out=Path(sys.argv[2])
with out.open('x') as f: json.dump(rows,f,indent=2,ensure_ascii=False)
print(json.dumps(rows,ensure_ascii=False))
