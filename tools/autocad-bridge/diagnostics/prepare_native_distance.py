"""Distance fixtures from prior native evidence, no sampled polygon or repair."""
import argparse
import json
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for street, handles in [('6', ['26259', '138AA6', '143E46']), ('16', ['14F316', '14F2D2'])]:
        report = args.input / street / 'native-report.json'
        data = json.loads(report.read_bytes())
        assert data['source_unchanged']
        cases = []
        for case in data['cases']:
            if case['name'] not in handles:
                continue
            region = case['regions'][0]
            t = region['topology_probe']
            points = []
            for fi, face in enumerate(t['faces']):
                for li, loop in enumerate(face['loops']):
                    # On-curve points must have near-zero edge-set distance.
                    edge = loop['edges'][0]
                    midpoint = edge['samples'][len(edge['samples'])//2]
                    points.append({'xyz': midpoint, 'kind': 'native_curve_point',
                                   'expected_distance': 0, 'tolerance': 1e-7,
                                   'face': fi, 'loop': li})
                    # Deliberately no expected exact distance for translated points:
                    # another edge may be closer, and we have no independent oracle.
                    for offset in (0.01, 0.1, 1.0):
                        points.append({'xyz': [midpoint[0]+offset, midpoint[1], midpoint[2]],
                                       'kind': 'distance_discovery', 'expected_distance': None})
            low, high = t['bounds']
            points.append({'xyz': [high[0]+1, high[1]+1, low[2]],
                           'kind': 'outside_bounds', 'distance_lower_bound': 2**0.5})
            assert len(points) <= 64
            cases.append({'name': case['name'], 'points': points})
        assert len(cases) == len(handles)
        directory = args.output / street
        directory.mkdir()
        rows = [data['source'], str((directory/'native-report.json').resolve()), str(len(cases))]
        for case in cases:
            rows += [case['name'], '1', case['name'], str(len(case['points']))]
            rows += [' '.join(format(v,'.17g') for v in point['xyz']) for point in case['points']]
        (directory/'request.txt').write_text('\n'.join(rows)+'\n')
        (directory/'cases.json').write_text(json.dumps({'source':data['source'],
            'source_sha256':data['source_sha256'], 'topology_report':str(report.resolve()),
            'scope':'edge-set distance, not obstacle clearance acceptance', 'cases':cases},
            indent=2,ensure_ascii=False))
        print(street,len(cases),sum(len(c['points']) for c in cases))

if __name__ == '__main__':
    main()
