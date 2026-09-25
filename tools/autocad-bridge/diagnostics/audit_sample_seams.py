"""Read existing diagnostic JSON only; locate seams without changing geometry."""
import argparse
import json
import math
from pathlib import Path
from shapely.geometry import LineString, Polygon
from shapely.validation import explain_validity


def audit(root):
    result = []
    for street, handles in [('6', ['138AA6', '143E46']), ('16', ['14F2D2'])]:
        report = root / street / 'native-report.json'
        data = json.loads(report.read_bytes())
        for case in data['cases']:
            if case['name'] not in handles:
                continue
            item = {'street': street, 'handle': case['name'],
                    'source_sha256': data['source_sha256'], 'report': str(report), 'faces': []}
            for face in case['regions'][0]['topology_probe']['faces']:
                loops = []
                for loop in face['loops']:
                    edges = loop['edges']
                    seams, coords, crossings = [], [], []
                    for i, edge in enumerate(edges):
                        previous = edges[i - 1]['samples'][-1]
                        current = edge['samples'][0]
                        seams.append({'from_edge': (i - 1) % len(edges), 'to_edge': i,
                                      'gap_units': math.dist(previous, current),
                                      'from': previous, 'to': current})
                        coords.extend(edge['samples'] if i == 0 else edge['samples'][1:])
                    lines = [LineString(e['samples']) for e in edges]
                    for i, line in enumerate(lines):
                        for j in range(i):
                            if i - j == 1 or (i == len(lines) - 1 and j == 0):
                                continue
                            intersection = line.intersection(lines[j])
                            if not intersection.is_empty:
                                crossings.append({'edges': [j, i], 'intersection': intersection.wkt})
                    loops.append({'role': loop['role'], 'seams': seams,
                                  'nonadjacent_sampled_edge_intersections': crossings,
                                  'individual_sampled_edges_simple': [e.is_simple for e in lines],
                                  'stitched_fixture_validity': explain_validity(Polygon(coords))})
                item['faces'].append({'loops': loops})
            result.append(item)
    return {'scope': 'sampled evidence only; no geometry modified or repaired', 'cases': result}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = audit(args.input)
    with args.output.open('x') as output:
        json.dump(result, output, indent=2, ensure_ascii=False)
    for case in result['cases']:
        loops = [loop for face in case['faces'] for loop in face['loops']]
        print(case['handle'], 'maximum seam gap', max(s['gap_units'] for l in loops for s in l['seams']),
              'nonadjacent intersections', sum(len(l['nonadjacent_sampled_edge_intersections']) for l in loops))
