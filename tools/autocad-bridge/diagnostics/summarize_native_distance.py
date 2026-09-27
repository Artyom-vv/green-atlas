"""Verify distance receipts; discovery queries are not geometry acceptance."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def summarize(directory):
    fixture = json.loads((directory / 'cases.json').read_bytes())
    report = json.loads((directory / 'native-report.json').read_bytes())
    require(report['source'] == fixture['source'], 'Source path mismatch')
    with Path(fixture['source']).open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    require(report['source_unchanged'] is True, 'Source changed during probe')
    require(digest == report['source_sha256'] == fixture['source_sha256'],
            'Source SHA mismatch')
    expected = fixture['cases']
    actual = report['cases']
    require(len(actual) == len(expected) > 0, 'Missing/extra cases')
    require(len({c['name'] for c in actual}) == len(actual), 'Duplicate cases')
    result = []
    for case, control in zip(actual, expected):
        require(case['name'] == control['name'], 'Case identity mismatch')
        require(len(case['regions']) == 1, 'Expected one region')
        region = case['regions'][0]
        require(region['area_status'] == region['brep_set_status'] == 0,
                'Native region unavailable')
        probe = region['distance_probe']
        require(probe['meaning'] == 'native_edge_set_not_occupied_area',
                'Unexpected distance meaning')
        edges = probe['edges']
        edge_checks = bool(edges) and all(
            e['open_status'] == e['curve_status'] == 0 and e['finite_interval'] is True
            for e in edges)
        if probe['edge_set_complete'] is True:
            require(edge_checks and probe['traversal_start_status'] ==
                    probe['traversal_end_status'] == 0, 'False complete edge set')
        points = control['points']
        queries = probe['queries']
        require(len(queries) == len(points) == len(region['queries']),
                'Missing/extra queries')
        outcomes = []
        for query, point, containment in zip(queries, points, region['queries']):
            xyz = point['xyz']
            require(query['point'] == containment['point'] == xyz, 'Point mismatch')
            require(len(xyz) == 3 and all(number(v) for v in xyz), 'Invalid point')
            distance = query['distance_units']
            partial = query['observed_partial_minimum']
            if partial is not None:
                require(number(partial) and partial >= 0, 'Invalid partial minimum')
                index = query['nearest_edge_index']
                require(type(index) is int and 0 <= index < len(edges), 'Invalid edge index')
                nearest = query['nearest_point']
                require(len(nearest) == 3 and all(number(v) for v in nearest),
                        'Invalid nearest point')
                require(math.isclose(math.dist(xyz, nearest), partial,
                                     abs_tol=1e-7, rel_tol=1e-9), 'Distance/witness mismatch')
            outcome = 'unavailable'
            if query['complete'] is True:
                require(probe['edge_set_complete'] is True and
                        query['failed_edge_queries'] == 0 and number(distance) and
                        distance >= 0 and distance == partial, 'False complete query')
                outcome = 'discovery'
                if point['kind'] == 'native_curve_point':
                    outcome = 'pass' if abs(distance - point['expected_distance']) <= point['tolerance'] else 'fail'
                elif point['kind'] == 'outside_bounds':
                    outcome = 'pass' if distance + 1e-7 >= point['distance_lower_bound'] else 'fail'
            else:
                require(distance is None, 'Partial distance exposed as accepted')
            outcomes.append({'kind': point['kind'], 'outcome': outcome,
                             'distance_units': distance, 'containment': containment})
        result.append({'handle': case['name'], 'source_sha256': digest,
                       'report': str(directory / 'native-report.json'), 'queries': outcomes})
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    fixtures = sorted(args.input.glob('*/cases.json'))
    require(bool(fixtures), 'No fixtures: refusing empty success report')
    cases = [case for fixture in fixtures for case in summarize(fixture.parent)]
    with args.output.open('x') as output:
        json.dump({'scope': 'edge-set distance controls, NOT planting acceptance',
                   'cases': cases}, output, indent=2, ensure_ascii=False)


if __name__ == '__main__':
    main()
