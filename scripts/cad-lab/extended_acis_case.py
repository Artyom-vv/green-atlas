"""One bounded, disposable FreeCAD process per corpus case; no repairs."""
import io
import json
import os
from pathlib import Path
import sys
import traceback

root = Path(os.environ['CAD_ACIS_LAB'])
sys.path.insert(0, str(root/'InventorLoader'))
case = json.loads(Path(sys.argv[1]).read_text())[int(sys.argv[2])]
out = Path(sys.argv[3])
out.mkdir(exist_ok=False, parents=True)
result = dict(name=case['name'], status='failed', source=case)
try:
    import FreeCAD, Part, Acis, Acis2Step, importerUtils
    source = Path(case['path'])
    importerUtils.setDumpFolder(str(out/'body.sab'))
    reader = Acis.AcisReader(io.BytesIO(source.read_bytes()) if source.suffix == '.sab' else io.StringIO(source.read_text()))
    reader.name = case['name']
    assert reader.readBinary() if source.suffix == '.sab' else reader.readText()
    Acis.init()
    Acis.setReader(reader)
    bodies = []
    for record in reader.getRecords():
        if record.name in ('Begin-of-ACIS-History-Data', 'End-of-ACIS-History-Section', 'End-of-ACIS-data'):
            break
        entity = Acis.createEntity(record)
        if record.name == 'body':
            bodies.append(entity)
    step = Acis2Step.export(reader.name, reader.header, bodies)
    shape = Part.read(step)
    edge_types = []
    for e in shape.Edges:
        try:
            edge_types.append(type(e.Curve).__name__)
        except TypeError:
            # Some valid poles have degenerate edges without a curve. Record
            # this measurement limitation rather than calling it import loss.
            edge_types.append('undefined/degenerate')
    box = shape.BoundBox
    bounds = [box.XMin,box.YMin,box.ZMin,box.XMax,box.YMax,box.ZMax]
    result.update(status='built', valid=shape.isValid(), null=shape.isNull(),
                  area=shape.Area, perimeter=shape.Length, bounds=bounds,
                  faces=len(shape.Faces), wires=len(shape.Wires),
                  wires_per_face=[len(f.Wires) for f in shape.Faces],
                  closed_wires=all(w.isClosed() for w in shape.Wires),
                  edge_types=sorted(set(edge_types)),
                  units_scale=reader.scale, step=step)
    checks = dict(valid=result['valid'], nonempty=not result['null'],
                  faces=len(shape.Faces)==case['source_faces'],
                  wires=len(shape.Wires)==case['source_loops'], closed=result['closed_wires'])
    if case['expected']:
        expected = case['expected']
        result['errors'] = dict(area=abs(shape.Area-expected['area']),
                               perimeter=abs(shape.Length-expected['perimeter']),
                               bounds=max(abs(a-b) for a,b in zip(bounds,expected['bounds'])))
        checks.update(area=result['errors']['area']<1e-7,
                      perimeter=result['errors']['perimeter']<1e-7,
                      bounds=result['errors']['bounds']<1e-6)
    result['checks'] = checks
    result['gate'] = 'passed' if all(checks.values()) else 'failed'
    result['gate_scope'] = 'analytic transform/units control' if case['expected'] else 'structural validity only, not full fidelity'
    shape.exportBrep(str(out/'shape.brep'))
except Exception:
    result['error'] = traceback.format_exc()
finally:
    (out/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))
sys.exit(0 if result.get('gate') == 'passed' else 1)
