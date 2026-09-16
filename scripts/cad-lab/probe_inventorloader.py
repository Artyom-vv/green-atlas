"""Isolated unpatched InventorLoader + real FreeCAD test, not an app importer.

Run with FreeCAD's Python, setting CAD_ACIS_LAB and CAD_ACIS_RESULT to absolute
directories. Result must not exist. No parser monkeypatches or GUI stubs.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
import traceback

LAB = Path(os.environ['CAD_ACIS_LAB'])
OUT = Path(os.environ['CAD_ACIS_RESULT'])
OUT.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(LAB / 'InventorLoader'))
if os.environ.get('CAD_ACIS_DEPS'):
    sys.path.insert(0, os.environ['CAD_ACIS_DEPS'])


def vector(v):
    return [v.x, v.y, v.z]


def describe(shape):
    bounds = shape.BoundBox
    edges = []
    for edge in shape.Edges:
        curve = edge.Curve
        info = dict(type=type(curve).__name__, length=edge.Length,
                    parameters=[edge.FirstParameter, edge.LastParameter],
                    vertices=[vector(v.Point) for v in edge.Vertexes],
                    samples=[vector(p) for p in edge.discretize(Number=65)])
        for attr in ['Radius', 'MajorRadius', 'MinorRadius']:
            if hasattr(curve, attr):
                info[attr] = getattr(curve, attr)
        if hasattr(curve, 'Center'):
            info['center'] = vector(curve.Center)
        edges.append(info)
    return dict(valid=shape.isValid(), null=shape.isNull(), area=shape.Area,
                length=shape.Length, faces=len(shape.Faces), edges=edges,
                wires=[dict(closed=w.isClosed(), valid=w.isValid(),
                            edges=len(w.Edges)) for w in shape.Wires],
                bounds=[bounds.XMin, bounds.YMin, bounds.ZMin,
                        bounds.XMax, bounds.YMax, bounds.ZMax])


result = {'scope': 'four original SAB bodies; not complete drawing fidelity',
          'cases': [], 'status': 'failed',
          'mode': os.environ.get('CAD_ACIS_MODE', 'native')}
try:
    import FreeCAD
    import Part
    if os.environ.get('CAD_ACIS_GUI') == 'offscreen':
        import FreeCADGui
        FreeCADGui.showMainWindow()
    import Acis
    import importerUtils
    if result['mode'] == 'step':
        import Acis2Step
    else:
        import importerSAT

    result['freecad_version'] = FreeCAD.Version()
    result['python'] = sys.version
    result['occ_version'] = getattr(Part, 'OCC_VERSION', None)
    inputs = sorted(LAB.glob('acadrust.dxf.*.sab'))
    assert len(inputs) == 4, inputs
    for source in inputs:
        handle = format(int(source.name.split('.')[-2]), 'X')
        item = dict(handle=handle, input=str(source),
                    sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        result['cases'].append(item)
        doc = FreeCAD.newDocument('Probe_' + handle)
        started = time.monotonic()
        try:
            # Upstream setDumpFolder deletes existing contents: use only a fresh
            # per-body directory inside this new, exclusive result directory.
            importerUtils.setDumpFolder(str(OUT / (handle + '.sab')))
            reader = Acis.AcisReader(io.BytesIO(source.read_bytes()))
            reader.name = handle
            item['read_binary'] = reader.readBinary()
            item['records'] = [r.name for r in reader.getRecords()]
            item['acis_scale'] = reader.scale
            if result['mode'] == 'step':
                # Same public reader/entity/export API used by convertModel;
                # no ImportGui needed when Part reads the exported STEP.
                Acis.init()
                Acis.setReader(reader)
                bodies = []
                for record in reader.getRecords():
                    if record.name in ('Begin-of-ACIS-History-Data',
                                       'End-of-ACIS-History-Section', 'End-of-ACIS-data'):
                        break
                    entity = Acis.createEntity(record)
                    if record.name == 'body':
                        bodies.append(entity)
                step = Acis2Step.export(reader.name, reader.header, bodies)
                item['step'] = step
                obj = doc.addObject('Part::Feature', 'StepShape')
                obj.Shape = Part.read(step)
            else:
                importerSAT.importModel(None)
            doc.recompute()
            item['objects'] = []
            for obj in doc.Objects:
                if not hasattr(obj, 'Shape'):
                    continue
                info = describe(obj.Shape)
                info['name'] = obj.Name
                info['placement'] = list(obj.Placement.toMatrix().A)
                brep = OUT / (handle + '-' + obj.Name + '.brep')
                obj.Shape.exportBrep(str(brep))
                reread = Part.Shape()
                reread.read(str(brep))
                info['brep_roundtrip'] = describe(reread)
                item['objects'].append(info)
            item['status'] = 'built' if item['objects'] else 'no_geometry'
        except Exception:
            item['status'] = 'failed'
            item['error'] = traceback.format_exc()
        finally:
            item['elapsed_seconds'] = time.monotonic() - started
            Acis.setReader(None)
            FreeCAD.closeDocument(doc.Name)
        print(json.dumps({k: v for k, v in item.items() if k != 'objects'}), flush=True)
    result['status'] = 'built' if all(c['status'] == 'built' for c in result['cases']) else 'failed'
except Exception:
    result['error'] = traceback.format_exc()
finally:
    with (OUT / 'results.json').open('x', encoding='utf8') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'error': result.get('error')}), flush=True)

sys.exit(0 if result['status'] == 'built' else 1)
