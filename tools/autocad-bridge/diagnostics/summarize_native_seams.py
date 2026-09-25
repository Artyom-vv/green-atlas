"""Compare native identities and curve endpoints, without changing geometry."""
import argparse
import json
import math
from pathlib import Path

def summarize(root):
    cases = []
    for street in ('6', '16'):
        path = root / street / 'native-report.json'
        data = json.loads(path.read_bytes())
        assert data['source_unchanged']
        for case in data['cases']:
            if case['name'] not in ('138AA6', '143E46', '14F2D2'):
                continue
            result = {'handle': case['name'], 'report': str(path),
                      'source_sha256': data['source_sha256'], 'loops': []}
            for face in case['regions'][0]['topology_probe']['faces']:
                for loop in face['loops']:
                    chain, errors = [], []
                    for edge in loop['edges']:
                        p = edge['native_edge_probe']
                        assert p['edge_status'] == 0
                        assert p['orient_to_loop']['status'] == p['orient_to_curve']['status'] == 0
                        a, b = p['vertex1'], p['vertex2']
                        assert a['status'] == b['status'] == a['point_status'] == b['point_status'] == 0
                        if not p['orient_to_loop']['value']:
                            a, b = b, a
                        chain.append([a['loop_local_identity'], b['loop_local_identity']])
                        errors.extend([math.dist(a['point'], p['evaluated_start']),
                                       math.dist(b['point'], p['evaluated_end'])])
                    result['loops'].append({'oriented_vertex_chain': chain,
                        'nonconnecting_successors': [i for i, pair in enumerate(chain)
                                                     if chain[i-1][1] != pair[0]],
                        'maximum_curve_vertex_difference_units': max(errors)})
            cases.append(result)
    return {'scope': 'native adjacency diagnostic, not source repair or product acceptance', 'cases': cases}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with args.output.open('x') as out:
        json.dump(summarize(args.input), out, indent=2, ensure_ascii=False)
