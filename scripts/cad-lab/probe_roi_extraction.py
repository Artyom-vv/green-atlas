"""Conservative ROI experiment on the real large block; never a production importer.

Only reject provably disjoint LINE bounds. Retain every other entity unchanged,
including unknown ACIS. Independent GEOS intersections audit all source LINEs.
This isolates the large-block bottleneck, NOT the complete project/XREF assembly.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

import ezdxf
from ezdxf import xref
from ezdxf.math import Matrix44
import numpy as np
import shapely

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.runtime/cad-extended-20260916/full-root.dxf'
OUT = ROOT / '.runtime/cad-roi-20260916'
APPID = 'GREEN_ATLAS_LAB_SOURCE'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def signature(entity):
    attrs = entity.dxf.all_existing_dxf_attribs()
    if entity.dxftype() == 'LWPOLYLINE':
        attrs['elevation'] = entity.dxf.elevation
    keys = ('layer', 'start', 'end', 'center', 'radius', 'extrusion', 'insert',
            'location', 'elevation', 'rotation', 'xscale', 'yscale', 'zscale',
            'start_angle', 'end_angle', 'major_axis', 'ratio')
    result = dict(type=entity.dxftype(), attrs={k: str(attrs[k]) for k in keys if k in attrs})
    if hasattr(entity, 'sab'):
        result['sab_sha256'] = sha(entity.sab)
        result['sab_bytes'] = len(entity.sab)
    return result


def main():
    OUT.mkdir(exist_ok=False)
    started = time.monotonic()
    source_sha = sha(SOURCE.read_bytes())
    doc = ezdxf.readfile(SOURCE)
    read_seconds = time.monotonic() - started
    block = doc.blocks.get('3_ДЖКХ-25_02294')
    ref = doc.entitydb['13D3E7E']
    assert list(ref.matrix44()) == list(Matrix44()), 'This probe only accepts the verified identity instance'
    assert tuple(block.base_point) == (0, 0, 0)
    assert doc.units == 6
    baseline = json.loads((ROOT / 'docs/implementation/2026-09-16-acis-reader-comparison/results.json').read_text())
    checked = next(r for r in baseline['runs'] if r['name'] == 'acis-step-1')['geometry_check']['cases']
    anchor = next(c for c in checked if c['handle'] == '13E6884')['reference']['bounds']
    center = [(anchor[i]+anchor[i+3])/2 for i in (0, 1)]
    entities = list(block)
    lines = [e for e in entities if e.dxftype() == 'LINE']
    unknown = [e for e in entities if e.dxftype() != 'LINE']
    # LINE start/end are WCS in this identity block instance. Include thickness
    # extrusion endpoints in broad-phase bounds; no curve flattening is involved.
    xy = np.array([[(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)] for e in lines])
    thickness = np.array([(e.dxf.extrusion.x*e.dxf.thickness,
                           e.dxf.extrusion.y*e.dxf.thickness) for e in lines])
    low = np.minimum(xy.min(axis=1), xy.min(axis=1)+thickness)
    high = np.maximum(xy.max(axis=1), xy.max(axis=1)+thickness)
    geometries = shapely.linestrings(xy)
    source_types = dict(Counter(e.dxftype() for e in entities))
    cases = []
    selected_mask = None
    scan_started = time.monotonic()
    for width in (50, 100, 250):
        # 20 m is an experimental envelope, NOT a validated normative radius.
        radius = width/2 + 20
        bounds = [center[0]-radius, center[1]-radius, center[0]+radius, center[1]+radius]
        overlap = ((low[:, 0] <= bounds[2]) & (high[:, 0] >= bounds[0]) &
                   (low[:, 1] <= bounds[3]) & (high[:, 1] >= bounds[1]))
        exact = shapely.intersects(geometries, shapely.box(*bounds))
        missing = exact & ~overlap
        assert not missing.any(), 'Independent line intersection audit failed'
        cases.append(dict(work_width_m=width, experimental_margin_m=20,
                          extraction_bounds=bounds, candidate_lines=int(overlap.sum()),
                          exact_intersecting_lines=int(exact.sum()), lost_intersecting_lines=int(missing.sum()),
                          retained_unbounded_entities=len(unknown), selected_total=int(overlap.sum())+len(unknown)))
        if width == 100:
            selected_mask = overlap
    selected = [e for e, keep in zip(lines, selected_mask) if keep] + unknown
    scan_seconds = time.monotonic()-scan_started
    expected = {e.dxf.handle: signature(e) for e in selected}
    assert APPID not in doc.appids
    doc.appids.new(APPID)
    for entity in selected:
        entity.set_xdata(APPID, [(1000, entity.dxf.handle)])

    exported = ezdxf.new(doc.dxfversion)
    exported.units = doc.units
    loader = xref.Loader(doc, exported)
    # Library's normal resource-aware clone; no vendor patches or custom ACIS parser.
    loader.add_command(xref.LoadEntities(selected, exported.modelspace()))
    export_started = time.monotonic()
    loader.execute()
    fragment = OUT / 'large-block-roi-100m.dxf'
    exported.saveas(fragment)
    export_seconds = time.monotonic()-export_started
    restored = ezdxf.readfile(fragment)
    actual = {}
    untracked = []
    for e in restored.modelspace():
        if not e.has_xdata(APPID):
            untracked.append(e.dxf.handle)
            continue
        source_handle = e.get_xdata(APPID)[0].value
        assert source_handle not in actual
        actual[source_handle] = signature(e)
    missing_handles = sorted(set(expected)-set(actual))
    changed = [dict(handle=h, before=expected[h], after=actual[h]) for h in expected.keys() & actual.keys()
               if expected[h] != actual[h]]
    retained_lines = [e for e in selected if e.dxftype() == 'LINE']
    per_layer = Counter(e.dxf.layer for e in selected)
    known_regions = [dict(handle=e.dxf.handle, **signature(e)) for e in unknown if e.dxftype() == 'REGION']
    report = dict(scope='one real embedded block at identity placement, not complete site',
                  source=str(SOURCE), source_sha256=source_sha, ezdxf=ezdxf.__version__,
                  block=block.name, instance_handle=ref.dxf.handle, center=center,
                  source_entities=len(entities), source_types=source_types,
                  source_region_anchor='13E6884', cases=cases,
                  fragment=str(fragment), fragment_bytes=fragment.stat().st_size,
                  fragment_sha256=sha(fragment.read_bytes()),
                  selected_entities=len(selected), source_layers=dict(per_layer),
                  selected_types=dict(Counter(e.dxftype() for e in selected)),
                  region_payloads=known_regions, output_entities=len(restored.modelspace()),
                  missing_source_handles=missing_handles, untracked_output_handles=untracked,
                  changed_checked_attributes=changed, output_units=restored.units,
                  source_bytes_unchanged=sha(SOURCE.read_bytes())==source_sha,
                  read_seconds=read_seconds, scan_three_windows_seconds=scan_seconds,
                  clone_and_save_seconds=export_seconds, seconds=time.monotonic()-started,
                  caveats=['20 m envelope is not a validated regulatory maximum',
                           'All non-LINE entities retained even if outside ROI',
                           'Other root layers and four external XREFs not included in this isolated block probe',
                           'Attribute comparison covers types, layers, common geometry attributes and exact SAB, not all DXF semantics',
                           'Full source is still parsed once; no native DWG viewer or lazy disk reader tested'])
    (OUT/'expected-entities.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2))
    (OUT/'extraction.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_layers','region_payloads','changed_checked_attributes')},ensure_ascii=False),flush=True)
    assert not missing_handles and not untracked and not changed
    assert restored.units == doc.units and report['source_bytes_unchanged']


if __name__ == '__main__':
    main()
