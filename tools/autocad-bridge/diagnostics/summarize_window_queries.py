"""Compare actual native observations: full-window predicate vs old three objects.

No CAD reader, geometry reconstruction, role remapping or planted result changes.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from run_direct_queries import digest


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--prior-zone', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output must be new')
    native = json.loads(args.native.read_text())
    window = native['inventory']['window_inventory']
    calc = window['calculation']
    prior = json.loads(args.prior_zone.read_text())
    now = {tuple(a['point']): a for a in calc['answers']}
    old = {tuple(a['point']): a for a in prior['answers']}
    changed = Counter()
    newly_blocked = []
    lost_known_blocks = []
    for point in sorted(now.keys() & old.keys()):
        before, after = old[point], now[point]
        changed[f"{before['result']} -> {after['result']}"] += 1
        if before['result'] == 'clear_of_selected_objects_only' and after['result'] == 'blocked':
            newly_blocked.append(after)
        if before['result'] == 'blocked' and after['result'] != 'blocked':
            lost_known_blocks.append(after)
    selected_before = [now.get(tuple(p), {'point': p, 'result': 'not_in_grid'}) for p in prior['selected']]
    result = {
        'scope': 'Same measured grid, expanded explicit experimental roles. NOT a product or normative safety check.',
        'native_sha256': digest(args.native), 'prior_zone_sha256': digest(args.prior_zone),
        'window': window['window'],
        'grid_points_compared': len(now.keys() & old.keys()),
        'old_points_missing_in_new': len(old.keys() - now.keys()),
        'transitions': dict(changed), 'lost_known_blocks': lost_known_blocks,
        'prior_selected_count': len(selected_before),
        'prior_selected_now': dict(Counter(row['result'] for row in selected_before)),
        'prior_selected_rechecks': selected_before,
        'newly_blocked_by_layer': dict(Counter(
            next(o['layer'] for o in calc['object_ledger'] if o['route'] == row['blocker'])
            for row in newly_blocked)),
        'newly_blocked_examples': newly_blocked[:20],
        'summary': {k: v for k, v in calc.items()
                    if k not in {'answers', 'selected', 'object_ledger', 'controls', 'unculled_checks'}},
        'unknown_role_layers': dict(Counter(o['layer'] for o in calc['object_ledger'] if o['role'] == 'unknown')),
        'area_unavailable_but_curve_available': [o for o in calc['object_ledger'] if o['error'] and o['curve_ready']],
        'unlocated_native_types': dict(Counter(o['type'] for o in window['unlocated'])),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('grid_points_compared', 'old_points_missing_in_new',
                                           'transitions', 'prior_selected_now', 'lost_known_blocks')}, ensure_ascii=False))
    return int(bool(lost_known_blocks))


if __name__ == '__main__':
    raise SystemExit(main())
