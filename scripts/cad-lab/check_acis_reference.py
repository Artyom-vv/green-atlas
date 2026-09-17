"""Independent analytic check for these four circle-segment fixtures only.

Uses ezdxf's *token* reader, not its unsupported ACIS geometry loader.
Not a general ACIS parser and never used to generate production geometry.
"""
import hashlib
import json
import math
from pathlib import Path
import sys

from ezdxf.acis import sab


def reference(path, *, units_scale=1):
    raw = sab.parse_sab(path.read_bytes())
    groups = {}
    for entity in raw.entities:
        groups.setdefault(entity.name, []).append(entity)
    assert len(groups['ellipse-curve']) == len(groups['straight-curve']) == 1
    assert len(groups['edge']) == 2 and len(groups['point']) == 2
    curve = groups['ellipse-curve'][0]
    center, normal, axis, ratio = [t.value for t in curve.data[1:5]]
    assert ratio == 1 and tuple(normal) == (0, 0, 1)
    transforms = groups.get('transform', [])
    assert len(transforms) <= 1
    offset = (0, 0, 0)
    if transforms:
        transform = [t.value for t in transforms[0].data]
        assert transform[:3] == [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
        assert transform[4] == 1 and not any(transform[5:])
        offset = transform[3]
    assert raw.header.units_in_mm == units_scale
    edge = next(e for e in groups['edge'] if e.data[6].value is curve)
    t0, t1 = edge.data[2].value, edge.data[4].value
    delta = t1 - t0
    assert 0 < delta < 2 * math.pi
    radius = math.sqrt(sum(v * v for v in axis))
    assert axis[2] == 0
    tangent = (-axis[1], axis[0], 0)

    def point(t):
        return [units_scale * (center[i] + offset[i] + axis[i] * math.cos(t) +
                tangent[i] * math.sin(t)) for i in range(3)]

    source_vertices = [[units_scale * (e.data[1].value[i] + offset[i]) for i in range(3)]
                       for e in groups['point']]
    endpoint_error = max(min(math.dist(point(t), v) for v in source_vertices)
                         for t in (t0, t1))
    assert endpoint_error < 1e-8
    extrema = [t0, t1]
    for i in (0, 1):
        extremum = math.atan2(tangent[i], axis[i])
        extrema += [extremum + k * math.pi for k in range(-4, 5)
                    if t0 <= extremum + k * math.pi <= t1]
    extrema_points = [point(t) for t in extrema]
    bounds = [min(p[i] for p in extrema_points) for i in range(3)] + [
        max(p[i] for p in extrema_points) for i in range(3)]
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                area=(radius * units_scale)**2 / 2 * (delta - math.sin(delta)),
                perimeter=radius * units_scale * delta + math.dist(*source_vertices),
                radius=radius, angle=delta, bounds=bounds,
                vertices=source_vertices, endpoint_error=endpoint_error,
                samples=[point(t0 + delta * n / 128) for n in range(129)])


def main():
    source_dir, probe_path, output_path = map(Path, sys.argv[1:])
    probe = json.loads(probe_path.read_text())
    cases = []
    for case in probe['cases']:
        path = source_dir / ('acadrust.dxf.' + str(int(case['handle'], 16)) + '.sab')
        expected = reference(path)
        assert expected['sha256'] == case['sha256']
        comparisons = []
        for obj in case.get('objects', []):
            bbox_error = max(abs(a-b) for a, b in zip(obj['bounds'], expected['bounds']))
            area_error = abs(obj['area'] - expected['area'])
            length_error = abs(obj['length'] - expected['perimeter'])
            checks = dict(valid=obj['valid'], one_face=obj['faces'] == 1,
                          two_edges=len(obj['edges']) == 2,
                          one_closed_wire=len(obj['wires']) == 1 and obj['wires'][0]['closed'],
                          bounds=bbox_error < 1e-6, area=area_error < 1e-8,
                          perimeter=length_error < 1e-8,
                          brep_valid=obj['brep_roundtrip']['valid'],
                          brep_area=abs(obj['brep_roundtrip']['area'] - expected['area']) < 1e-8)
            comparisons.append(dict(name=obj['name'], checks=checks,
                                    bounds_max_error=bbox_error, area_error=area_error,
                                    perimeter_error=length_error, passed=all(checks.values())))
        cases.append(dict(handle=case['handle'], reference=expected,
                          comparisons=comparisons,
                          passed=len(comparisons) == 1 and comparisons[0]['passed']))
    result = dict(scope='four known planar circle segments, raw CAD units',
                  status='passed' if len(cases) == 4 and all(c['passed'] for c in cases) else 'failed',
                  cases=cases)
    with output_path.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({**result, 'cases': [{k:v for k,v in c.items() if k != 'reference'} for c in cases]}))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
