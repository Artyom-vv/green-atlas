"""Generated integration controls of existing DXF reader; not survey substitutes."""
import io
import json
from pathlib import Path
import sys
import ezdxf
from shapely.geometry import shape

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root/'apps/api'))
from app.dxf_import.adapters import EzdxfReader

rows=[]
for name,model_units,block_units,size,scale,rotation,expected,area,nested in [
    ('mm-block-in-m',6,4,1000,0.001,0,[10,20,11,20.5],0.5,False),
    ('m-block-in-mm',4,6,1,1000,0,[0.01,0.02,1.01,0.52],0.5,False),
    ('rotate90',6,4,1000,0.001,90,[9.5,20,10,21],0.5,False),
    ('mirror',6,4,1000,-0.001,0,[9,20,10,20.5],0.5,False),
    ('nested-rotate',6,4,1000,0.001,90,[9.5,21,10,22],0.5,True),
]:
    doc=ezdxf.new('R2018'); doc.units=model_units
    block=doc.blocks.new('inner'); block.block_record.dxf.units=block_units
    block.add_lwpolyline([(0,0),(size,0),(size,size/2),(0,size/2)],close=True)
    name_to_insert='inner'
    if nested:
        parent=doc.blocks.new('outer'); parent.block_record.dxf.units=block_units
        parent.add_blockref('inner',(size,0)); name_to_insert='outer'
    doc.modelspace().add_blockref(name_to_insert,(10,20),dxfattribs=dict(xscale=scale,yscale=abs(scale),zscale=abs(scale),rotation=rotation))
    stream=io.StringIO(); doc.write(stream)
    imported=EzdxfReader().read(name+'.dxf',stream.getvalue().encode())
    features=imported.geometry.feature_collection['features']
    actual_area=sum(shape(f['geometry']).area for f in features)
    bounds_error=max(abs(a-b) for a,b in zip(imported.bounds,expected))
    rows.append(dict(name=name,bounds=imported.bounds,expected_bounds=expected,area=actual_area,
                     passed=bounds_error<1e-8 and abs(actual_area-area)<1e-8))
out=root/'.runtime/cad-extended-20260916/insert-unit-controls.json'
with out.open('x') as f: json.dump(rows,f,indent=2)
print(json.dumps(rows))
sys.exit(0 if all(r['passed'] for r in rows) else 1)
