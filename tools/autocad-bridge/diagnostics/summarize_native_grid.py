"""Summarize native discovery queries, without claiming an independent oracle."""
import argparse
import json
from collections import Counter
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    result = {'scope': 'native discovery; not independent geometric acceptance', 'cases': []}
    for path in sorted(args.input.glob('*/native-report.json')):
        data = json.loads(path.read_bytes())
        fixture = json.loads((path.parent/'cases.json').read_bytes())
        assert data['source_unchanged'] and data['source_sha256'] == fixture['source_sha256']
        assert len(data['cases']) == len(fixture['cases'])
        for case, expected in zip(data['cases'], fixture['cases']):
            assert case['name'] == expected['name'] and len(case['regions']) == 1
            queries = case['regions'][0]['queries']
            assert len(queries) == len(expected['points'])
            counts = Counter()
            controls = []
            for query, point in zip(queries, expected['points']):
                assert query['point'] == point['xyz']
                counts[(query['status'], query['containment'], query['container_class'])] += 1
                if point['expected'] is not None:
                    controls.append(query['status'] == 0 and query['containment'] == point['expected'])
            result['cases'].append({'handle': case['name'], 'report': str(path),
                'source_sha256': data['source_sha256'], 'query_count': len(queries),
                'outcomes': [{'status': k[0], 'containment': k[1], 'container_class': k[2], 'count': v}
                             for k,v in counts.items()], 'outside_controls_passed': controls})
    with args.output.open('x') as out:
        json.dump(result, out, indent=2, ensure_ascii=False)

if __name__ == '__main__':
    main()
