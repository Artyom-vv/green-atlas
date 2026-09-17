"""Check the two measured Kustanayskaya placements, not a general ACIS adapter.

Run with the portable FreeCAD Python. Inputs come from unmodified SAB analytic
references and ezdxf's measured INSERT transforms. Reject other transforms.
"""
import json
from pathlib import Path
import sys

import FreeCAD
import Part

root = Path(sys.argv[1])
cases = {c['name']: c for c in json.loads((root/'cases.json').read_text(encoding='utf8'))}
placements = json.loads((root/'placements.json').read_text(encoding='utf8'))
results = []
for placement in placements:
    case = cases[placement['handle']]
    source = json.loads((root/case['name']/'result.json').read_text(encoding='utf8'))
    assert source['gate'] == 'passed'
    matrix = placement['matrix']
    assert matrix[:12] == [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]
    assert matrix[15] == 1
    scale = source['units_scale']
    shape = Part.read(source['step'])
    # The uniform scale API preserves circular edges. transformGeometry() can
    # replace them with splines and is not equivalent for the precision gate.
    shape.scale(1/scale)
    shape.translate(FreeCAD.Vector(*matrix[12:15]))
    box = shape.BoundBox
    actual = [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax]
    expected = [v/scale + matrix[12+i % 3] for i,v in enumerate(case['expected']['bounds'])]
    errors = dict(bounds=max(abs(a-b) for a,b in zip(actual,expected)),
                  area=abs(shape.Area-case['expected']['area']/scale**2),
                  perimeter=abs(shape.Length-case['expected']['perimeter']/scale))
    results.append(dict(handle=case['name'], valid=shape.isValid(), bounds=actual,
                        errors=errors, passed=shape.isValid() and max(errors.values()) < 1e-7))
report = dict(scope='two actual translated planar REGION; DXF meters, STEP millimeters',
              passed=len(results)==2 and all(r['passed'] for r in results), results=results)
(root/'world-placement.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
print(json.dumps(report))
sys.exit(0 if report['passed'] else 1)
