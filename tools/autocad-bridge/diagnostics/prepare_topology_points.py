"""Select experimental witnesses from native topology samples, never parse DWG.

These are consistency checks against sampled fixtures, not an independent oracle
or a production polygonizer. Invalid/unclosed samples stop preparation explicitly.
"""
import argparse
import json
import math
from pathlib import Path
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for report in sorted(args.input.glob('*/native-report.json')):
        data = json.loads(report.read_bytes())
        assert data['source_unchanged']
        cases, unqualified = [], []
        for case in data['cases']:
            if not case.get('regions'):
                continue
            assert len(case['regions']) == 1
            topology = case['regions'][0]['topology_probe']
            assert topology['faces_traversed']
            shapes, holes, boxes = [], [], []
            z = None
            invalid_fixture = False
            for face in topology['faces']:
                assert face['face_status'] == 0 and face['loops_traversed']
                outer, inner = [], []
                for loop in face['loops']:
                    assert loop['type_status'] == 0 and loop['samples_complete']
                    coords = []
                    for edge in loop['edges']:
                        samples = edge['samples']
                        assert edge['sampled'] and all(p is not None for p in samples)
                        if coords and math.dist(coords[-1], samples[0]) >= 1e-7:
                            unqualified.append({'name': case['name'],
                                'reason': 'sampled_edge_endpoints_disagree',
                                'gap_units': math.dist(coords[-1], samples[0]),
                                'stage': 'sampled_fixture_not_native_geometry'})
                            invalid_fixture = True
                            break
                        coords.extend(samples if not coords else samples[1:])
                    if invalid_fixture:
                        break
                    assert Point(coords[0]).distance(Point(coords[-1])) < 1e-7
                    if z is None:
                        z = coords[0][2]
                    assert all(abs(p[2] - z) < 1e-7 for p in coords)
                    assert loop['role'] in ('outer', 'hole')
                    (outer if loop['role'] == 'outer' else inner).append(coords)
                if invalid_fixture:
                    break
                assert len(outer) == 1
                shape = Polygon(outer[0], inner)
                if not shape.is_valid or shape.is_empty:
                    from shapely.validation import explain_validity
                    unqualified.append({'name': case['name'], 'reason': explain_validity(shape),
                                        'stage': 'sampled_fixture_not_native_geometry'})
                    invalid_fixture = True
                    break
                shapes.append(shape)
                holes.extend(Polygon(h) for h in inner)
                low, high = face['bounds']
                boxes.append(box(low[0], low[1], high[0], high[1]))
            if invalid_fixture:
                continue
            union = unary_union(shapes)
            points = []
            def add(label, p, expected):
                points.append({'label': label, 'xyz': [p.x, p.y, z], 'expected': expected})
            for i, shape in enumerate(shapes):
                add(f'face_{i}', shape.representative_point(), 'face')
            for i, hole in enumerate(holes):
                p = hole.representative_point()
                assert not union.covers(p), 'hole occupied by another face'
                add(f'hole_{i}', p, 'outside')
            # A gap witness outside ALL native face bounding boxes is stronger
            # than one inferred solely from the sampled polygon interior.
            if len(shapes) > 1:
                gap = union.convex_hull.difference(unary_union(boxes))
                if not gap.is_empty and gap.area > 0:
                    add('gap_outside_native_face_bounds', gap.representative_point(), 'outside')
            low, high = topology['bounds']
            add('outside_native_bounds', Point(high[0] + 1, high[1] + 1), 'outside')
            assert len(points) <= 64
            cases.append({'name': case['name'], 'points': points, 'faces': len(shapes),
                          'holes': len(holes), 'oracle': 'sampled_fixture_consistency_only'})
        directory = args.output / report.parent.name
        directory.mkdir()
        rows = [data['source'], str((directory / 'native-report.json').resolve()), str(len(cases))]
        for case in cases:
            rows += [case['name'], '1', case['name'], str(len(case['points']))]
            rows += [' '.join(format(v, '.17g') for v in p['xyz']) for p in case['points']]
        (directory / 'request.txt').write_text('\n'.join(rows) + '\n')
        (directory / 'cases.json').write_text(json.dumps({
            'source': data['source'], 'source_sha256': data['source_sha256'],
            'topology_report': str(report.resolve()), 'cases': cases,
            'unqualified': unqualified}, indent=2, ensure_ascii=False))
        print(directory, len(cases), sum(len(c['points']) for c in cases), unqualified)


if __name__ == '__main__':
    main()
