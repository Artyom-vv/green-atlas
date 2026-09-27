"""Experimental dash-gap recovery within original curb/street-boundary INSERTs.

Never join across block instances. Only mutual-nearest endpoints <=0.55
source units; no original primitive is moved. Output remains review-only.
Usage: API Python reconstruct_terrain_breaklines.py PACKET_JSON OUTPUT_JSON
"""
import hashlib
import json
import math
from pathlib import Path
import sys

import ezdxf
from ezdxf.path import make_path
from shapely.geometry import LineString, box


def connect(parts, max_gap=.55):
    endpoints=[(i,side,tuple(part[side])) for i,part in enumerate(parts) for side in (0,-1)]
    nearest={}
    for i,(owner,side,p) in enumerate(endpoints):
        choices=[(math.dist(p,q),j) for j,(other,_,q) in enumerate(endpoints) if other!=owner]
        if choices:
            distance,j=min(choices)
            if distance<=max_gap:nearest[i]=(distance,j)
    result=[]
    for i,(distance,j) in nearest.items():
        if i<j and distance>1e-6 and nearest.get(j,(None,None))[1]==i:
            result.append({'start':list(endpoints[i][2]),'end':list(endpoints[j][2]),
                           'gap':distance,'part_indices':[endpoints[i][0],endpoints[j][0]]})
    return result


def reconstruct(packet):
    source=Path(packet['source'])
    if hashlib.sha256(source.read_bytes()).hexdigest()!=packet['sha256']:
        raise ValueError('source SHA differs from control packet')
    doc=ezdxf.readfile(source);extent=box(*packet['bbox']);joins=[]; additional=[]
    for insert in doc.modelspace().query('INSERT'):
        if insert.dxf.layer not in {'Бортовой камень','Граница улицы'}:continue
        parts=[]
        for e in insert.virtual_entities():
            if e.dxftype()=='LINE':coords=[list(e.dxf.start)[:2],list(e.dxf.end)[:2]]
            elif e.dxftype()=='LWPOLYLINE':coords=[list(p)[:2] for p in make_path(e).flattening(.02)]
            else:continue
            if len(coords)>=2:parts.append(coords)
        if not any(LineString(part).intersects(extent) for part in parts):continue
        if insert.dxf.layer=='Граница улицы':
            for part in parts:
                if LineString(part).intersects(extent):
                    for a,b in zip(part,part[1:]):
                        additional.append({'handle':None,'type':'LINE','layer':insert.dxf.layer,
                            'start':list(a)+[0],'end':list(b)+[0],'source_insert':insert.dxf.handle,
                            'status':'source_boundary_surface_role_unconfirmed'})
        for join in connect(parts):
            if not LineString([join['start'],join['end']]).intersects(extent):continue
            joins.append({**join,'source_insert':insert.dxf.handle,'block':insert.dxf.name,'layer':insert.dxf.layer})
    augmented=json.loads(json.dumps(packet))
    augmented['breakline_candidates'].extend(additional)
    for i,j in enumerate(joins):
        augmented['breakline_candidates'].append({'handle':None,'type':'LINE','layer':j['layer'],
            'start':j['start']+[0],'end':j['end']+[0],'status':'inferred_dash_gap_not_admitted',
            'source_insert':j['source_insert'],'reconstruction_index':i})
    augmented['dash_gap_reconstruction']={'status':'experimental','max_gap':.55,'joins':joins,
        'rule':'mutual nearest primitive endpoints within same original INSERT; no primitive moved',
        'limitations':['Source block identity does not prove surface-side semantics.',
                       'Unmatched endpoints remain open; no forced closure or cross-block snapping.']}
    return augmented

if __name__=='__main__':
    packet=reconstruct(json.loads(Path(sys.argv[1]).read_text()))
    Path(sys.argv[2]).write_text(json.dumps(packet,ensure_ascii=False,indent=2))
    print(json.dumps({'joins':len(packet['dash_gap_reconstruction']['joins']),
                      'source_instances':len(set(j['source_insert'] for j in packet['dash_gap_reconstruction']['joins']))}))
