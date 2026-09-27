"""Prepare upstream examples and explicitly labelled metamorphic controls."""
import hashlib
import json
import math
from pathlib import Path
import sys

import ezdxf
from ezdxf.acis import sat, sab

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / '.runtime/cad-comparison-20260916'
LAB = ROOT / '.runtime/cad-extended-20260916'
OUT = LAB / 'inputs'
OUT.mkdir(exist_ok=False)
sys.path.insert(0, str(Path(__file__).parent))
from check_acis_reference import reference

cases = []


def add(name, payload, origin, expected=None):
    binary = isinstance(payload, bytes)
    path = OUT / (name + ('.sab' if binary else '.sat'))
    path.write_bytes(payload if binary else payload.encode())
    raw = sab.parse_sab(payload) if binary else sat.parse_sat(payload.splitlines())
    types = [e.name for e in raw.entities]
    cases.append(dict(name=name, path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                      origin=origin, source_faces=types.count('face'), source_loops=types.count('loop'),
                      source_types=sorted(set(types)), expected=expected))


base = (OLD / 'acadrust.dxf.20859695.sat').read_text()
ref = reference(OLD / 'acadrust.dxf.20859695.sab')
raw = sab.parse_sab((OLD / 'acadrust.dxf.20859695.sab').read_bytes())
curve = next(e for e in raw.entities if e.name == 'ellipse-curve')
center, normal, major, ratio = [t.value for t in curve.data[1:5]]
edge = next(e for e in raw.entities if e.name == 'edge' and e.data[6].value is curve)
t0, t1 = edge.data[2].value, edge.data[4].value
local_points = [[center[i] + major[i]*math.cos(t) + (-major[1], major[0], 0)[i]*math.sin(t)
                 for i in range(3)] for t in [t0+(t1-t0)*n/16384 for n in range(16385)]]
identity = [[1,0,0],[0,1,0],[0,0,1]]
c = math.sqrt(0.5)
controls = [
    ('identity', identity, 1, 1),
    ('rotate-z45', [[c,-c,0],[c,c,0],[0,0,1]], 1, 1),
    ('rotate-x90', [[1,0,0],[0,0,-1],[0,1,0]], 1, 1),
    ('reflect-x', [[-1,0,0],[0,1,0],[0,0,1]], 1, 1),
    ('uniform-scale2', [[2,0,0],[0,2,0],[0,0,2]], 1, 2),
    ('header-inch', identity, 25.4, 1),
]
for name, matrix, units, scale in controls:
    offset = [10,20,30]
    values = [matrix[i][j] for j in range(3) for i in range(3)] + offset + [1]
    flags = ('rotate' if name.startswith('rotate') else 'no_rotate') + ' ' + (
        'reflect' if name.startswith('reflect') else 'no_reflect') + ' no_shear'
    lines = base.splitlines()
    lines[2] = str(units) + ' 1e-006 1e-010'
    lines = [('transform $-1 -1 ' + ' '.join(map(str, values)) + ' ' + flags + ' #')
             if l.startswith('transform ') else l for l in lines]
    points = [[units*(sum(matrix[i][j]*p[j] for j in range(3))+offset[i]) for i in range(3)]
              for p in local_points]
    expected = dict(area=ref['area']*(units*scale)**2,
                    perimeter=ref['perimeter']*units*scale,
                    bounds=[min(p[i] for p in points) for i in range(3)] +
                           [max(p[i] for p in points) for i in range(3)],
                    source_transform=values, header_units=units)
    add('control-'+name, '\n'.join(lines)+'\n', 'generated metamorphic control from original SAT; NOT another real survey', expected)

for source in [LAB/'ezdxf/examples/acistools/3dsolids.dxf', LAB/'ezdxf/exploration/acis/torus_r2010.dxf']:
    doc = ezdxf.readfile(source)
    for e in doc.modelspace().query('3DSOLID REGION BODY'):
        add(source.stem+'-'+e.dxf.handle, e.sab or '\n'.join(e.sat), str(source.relative_to(ROOT)))
add('upstream-region', (ROOT/'.runtime/cad-patch-lab/acadrust/examples/entity_atlas_assets/region.sat').read_text(),
    'acadrust examples/entity_atlas_assets/region.sat')
add('upstream-dice', (OLD/'InventorLoader/Demo-Status/Dice.sat').read_text(), 'InventorLoader Demo-Status/Dice.sat')
for p in sorted(OLD.glob('acadrust.dxf.*.sab')):
    add('real-'+format(int(p.name.split('.')[-2]), 'X'), p.read_bytes(), 'original Parkovaya DWG, unchanged SAB')
(LAB/'cases.json').write_text(json.dumps(cases, indent=2)+'\n')
print(json.dumps([dict(name=c['name'], faces=c['source_faces'], loops=c['source_loops']) for c in cases]))
