"""Extract a bounded DXF terrain review packet, never an admitted terrain mesh.

API venv: extract_terrain_control.py SOURCE OUTPUT --bbox XMIN YMIN XMAX YMAX
Optional --svg needs ezdxf drawing dependencies (Pillow). Source is read-only.
Text coordinates remain annotation anchors, not surveyed ground coordinates.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import ezdxf
from ezdxf import bbox
from ezdxf.disassemble import recursive_decompose

NUMBER = re.compile(r"\s*[+-]?\d{2,3}[.,]\d{1,3}\s*")


def extract(source, bounds):
    doc = ezdxf.readfile(source)
    x0, y0, x1, y1 = bounds
    if x0 >= x1 or y0 >= y1:
        raise ValueError("bbox must have positive width and height")
    selected, labels, points, lines = [], [], [], []
    # PIKET identity belongs to INSERT; decomposition loses it. This packet
    # intentionally supports standalone topography with top-level PIKET only.
    for e in doc.modelspace().query('INSERT'):
        if e.dxf.layer == 'Горизонтали' and e.dxf.name.startswith('PIKET'):
            x, y, z = e.dxf.insert
            if x0 <= x <= x1 and y0 <= y <= y1:
                points.append({'handle': e.dxf.handle, 'block': e.dxf.name,
                               'xyz': [x, y, z], 'accepted_elevation': None})
    for e in recursive_decompose(doc.modelspace()):
        if e.dxftype() not in {'TEXT', 'MTEXT', 'LINE', 'LWPOLYLINE', 'ARC', 'CIRCLE', 'ELLIPSE', 'POINT'}:
            continue
        b = bbox.extents([e], fast=True)
        if not b.has_data or b.extmax.x < x0 or b.extmin.x > x1 or b.extmax.y < y0 or b.extmin.y > y1:
            continue
        selected.append(e)
        if e.dxftype() == 'TEXT' and e.dxf.layer == 'Горизонтали' and NUMBER.fullmatch(e.dxf.text):
            labels.append({'handle': e.dxf.handle, 'text': e.dxf.text,
                           'value': float(e.dxf.text.replace(',', '.')),
                           'insert': list(e.dxf.insert),
                           'align_point': list(e.dxf.align_point) if e.dxf.align_point is not None else None,
                           'rotation': e.dxf.rotation, 'halign': e.dxf.halign,
                           'valign': e.dxf.valign, 'height': e.dxf.height,
                           'style': e.dxf.style, 'status': 'unreviewed_annotation'})
        if e.dxf.layer in {'Бортовой камень', 'Откосы'}:
            r = {'handle': e.dxf.handle, 'layer': e.dxf.layer, 'type': e.dxftype(),
                 'status': 'candidate_breakline_not_admitted'}
            if e.dxftype() == 'LINE':
                r.update(start=list(e.dxf.start), end=list(e.dxf.end))
            elif e.dxftype() == 'LWPOLYLINE':
                r.update(vertices=[list(v) for v in e.get_points('xyb')],
                         elevation=e.dxf.elevation, closed=e.closed)
            else:
                continue
            lines.append(r)
    for p in points:
        ranked = sorted(((sum((p['xyz'][i]-t['insert'][i])**2 for i in (0, 1))**.5, t)
                         for t in labels), key=lambda item: item[0])
        p['nearby_labels'] = [{'handle': t['handle'], 'value': t['value'],
                               'insertion_distance': d} for d, t in ranked if d <= 3]
    packet = {'schema': 'terrain-control-review-v1', 'status': 'review_only_not_terrain',
              'source': str(source.resolve()), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'bbox': bounds, 'dxf_units_code': doc.units,
              'scope': 'Standalone topography; top-level PIKET only; nested linework flattened. Selection intersects bbox, not clipped.',
              'limitations': ['3-unit annotation proximity is diagnostic only.',
                             'No ground heights admitted; no coordinate datum inferred.',
                             'Drawing font substitution may differ from AutoCAD.'],
              'entity_count': len(selected), 'layers': dict(Counter(e.dxf.layer for e in selected)),
              'pickets': points, 'labels': labels, 'breakline_candidates': lines,
              'accepted_points': [], 'triangles': []}
    return doc, selected, packet


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--bbox', type=float, nargs=4, required=True)
    p.add_argument('--svg', action='store_true')
    args = p.parse_args()
    doc, entities, packet = extract(args.source, args.bbox)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'control.json').write_text(json.dumps(packet, ensure_ascii=False, indent=2))
    if args.svg:
        from ezdxf.addons.drawing import Frontend, RenderContext, svg, layout, config
        from ezdxf.math import BoundingBox2d
        backend = svg.SVGBackend()
        cfg = config.Configuration(color_policy=config.ColorPolicy.BLACK, min_lineweight=.03)
        front = Frontend(RenderContext(doc), backend, config=cfg)
        front.set_background('#ffffff')
        front.draw_entities(entities)
        backend.finalize()
        x0, y0, x1, y1 = args.bbox
        picture = backend.get_string(layout.Page(250, 250*(y1-y0)/(x1-x0)),
                                     settings=layout.Settings(crop_at_margins=True),
                                     render_box=BoundingBox2d([(x0, y0), (x1, y1)]))
        (args.output / 'control.svg').write_text(picture)
    print(json.dumps({k: len(packet[k]) for k in ('pickets', 'labels', 'breakline_candidates', 'accepted_points')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
