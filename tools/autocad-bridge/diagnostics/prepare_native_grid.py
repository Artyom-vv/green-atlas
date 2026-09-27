"""Ask native containment on a fixed grid of native face bounds, no polygons.

Interior results are native witnesses, not independently validated expectations.
Only points outside the native whole-region box have an a-priori outside claim.
"""
import argparse
import json
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for street, wanted in [('6', ['138AA6', '143E46']), ('16', ['14F2D2'])]:
        report = args.input / street / 'native-report.json'
        data = json.loads(report.read_bytes())
        assert data['source_unchanged']
        cases = []
        for case in data['cases']:
            if case['name'] not in wanted:
                continue
            topology = case['regions'][0]['topology_probe']
            points = []
            for fi, face in enumerate(topology['faces']):
                assert face['bounds_status'] == 0
                low, high = face['bounds']
                assert abs(high[2] - low[2]) < 1e-7
                for i in range(5):
                    for j in range(5):
                        xyz = [low[0] + (high[0]-low[0])*(i+0.5)/5,
                               low[1] + (high[1]-low[1])*(j+0.5)/5, low[2]]
                        points.append({'xyz': xyz, 'face_bounds_index': fi,
                                       'expected': None, 'kind': 'discovery'})
            assert topology['bounds_status'] == 0
            low, high = topology['bounds']
            points.append({'xyz': [high[0]+1, high[1]+1, low[2]],
                           'kind': 'outside_control', 'expected': 'outside'})
            assert len(points) <= 64
            cases.append({'name': case['name'], 'points': points})
        assert len(cases) == len(wanted)
        directory = args.output / street
        directory.mkdir()
        rows = [data['source'], str((directory / 'native-report.json').resolve()), str(len(cases))]
        for case in cases:
            rows += [case['name'], '1', case['name'], str(len(case['points']))]
            rows += [' '.join(format(v, '.17g') for v in p['xyz']) for p in case['points']]
        (directory/'request.txt').write_text('\n'.join(rows)+'\n')
        (directory/'cases.json').write_text(json.dumps({'source': data['source'],
            'source_sha256': data['source_sha256'], 'topology_report': str(report.resolve()),
            'scope': 'native query availability, not independent spatial acceptance',
            'cases': cases}, indent=2, ensure_ascii=False))
        print(directory, sum(len(c['points']) for c in cases))

if __name__ == '__main__':
    main()
