"""Build an explicitly estimated, disconnected terrain preview from a review packet.

No product admission. Conservative annotation matching; triangles crossing known
curbs/slopes are omitted rather than smoothing across unassigned elevation pairs.
Usage: API Python build_terrain_preview.py CONTROL_JSON OUTPUT_DIR
"""
import collections
import json
import math
from pathlib import Path
import sys

from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.ops import triangulate, unary_union
from ezdxf.math import bulge_to_arc


def blockers(rows):
    lines = []
    for row in rows:
        if row['type'] == 'LINE':
            lines.append(LineString([row['start'][:2], row['end'][:2]]))
        else:
            vertices = row['vertices']
            for i in range(len(vertices) if row['closed'] else len(vertices)-1):
                a, b = vertices[i], vertices[(i+1) % len(vertices)]
                if a[:2] == b[:2]:
                    continue
                if not a[2]:
                    lines.append(LineString([a[:2], b[:2]]))
                    continue
                center, start, end, radius = bulge_to_arc(a[:2], b[:2], a[2])
                sweep = (end-start) % (2*math.pi)
                # Sample with at most 0.02 drawing-unit sagitta, not a straight chord.
                step = 2*math.acos(max(-1, 1-min(.02/radius, 1)))
                n = max(2, math.ceil(sweep/step))
                lines.append(LineString([(center.x+radius*math.cos(start+sweep*j/n),
                                          center.y+radius*math.sin(start+sweep*j/n)) for j in range(n+1)]))
    return unary_union(lines)


def build(packet):
    ps = packet['pickets']
    groups = collections.defaultdict(list)
    rejected = []
    for t in packet['labels']:
        anchor = t['align_point'] if (t['valign'] or t['halign']) and t['align_point'] else t['insert']
        ranks = sorted((math.dist(anchor[:2], p['xyz'][:2]), i) for i,p in enumerate(ps))
        if len(ranks)<2 or ranks[0][0]>1.6 or ranks[1][0]-ranks[0][0]<1.5:
            rejected.append({'label':t['handle'],'reason':'distance_or_competing_picket'})
        else:
            groups[ranks[0][1]].append(t)
    barrier = blockers(packet['breakline_candidates'])
    vertices = []
    for i, ts in groups.items():
        p=ps[i]
        # Also veto any alternative within the original 3-unit diagnostic radius.
        if len(ts)!=1 or len(p['nearby_labels'])!=1:
            rejected.append({'picket':p['handle'],'reason':'multiple_annotations'})
            continue
        if MultiPoint([p['xyz'][:2]]).distance(barrier)<2:
            rejected.append({'picket':p['handle'],'reason':'near_curb_or_slope'})
            continue
        vertices.append({'xyz':[*p['xyz'][:2], ts[0]['value']], 'picket':p['handle'],
                         'label':ts[0]['handle'], 'status':'estimated_unconfirmed_ground'})
    lookup={tuple(v['xyz'][:2]):i for i,v in enumerate(vertices)}
    faces=[]; excluded=collections.Counter()
    for triangle in triangulate(MultiPoint(list(lookup))):
        xy=list(triangle.exterior.coords)[:3]
        if max(math.dist(xy[i],xy[(i+1)%3]) for i in range(3))>25:
            excluded['long_edge']+=1;continue
        if triangle.intersects(barrier):
            excluded['crosses_curb_or_slope']+=1;continue
        if triangle.area<1:
            excluded['small_area']+=1;continue
        faces.append([lookup[tuple(p)] for p in xy])
    return {'schema':'terrain-estimated-preview-v1','status':'experimental_not_admitted',
            'source_sha256':packet['sha256'], 'origin':[packet['bbox'][0],packet['bbox'][1],150],
            'threshold_units':'source drawing units, assumed metres for this experiment',
            'rules':{'max_anchor_distance':1.6,'second_picket_margin':1.5,'min_breakline_distance':2,'max_edge':25},
            'limitations':['Annotation association is heuristic, not survey confirmation.',
                           'No interpolation outside retained triangles; gaps are intentional.',
                           'Paired curb elevations are excluded, not averaged.',
                           'Source vertical datum not transformed.'],
            'vertices':vertices,'triangles':faces,'rejected':rejected,'excluded_triangles':dict(excluded),
            'surface_area_xy':sum(Polygon([vertices[i]['xyz'][:2] for i in f]).area for f in faces)}



def sample_height(mesh, x, y):
    """Return source-datum height inside coverage, None in gaps/outside."""
    for face in mesh['triangles']:
        a,b,c=[mesh['vertices'][i]['xyz'] for i in face]
        denominator=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(denominator)<1e-12:
            continue
        u=((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/denominator
        v=((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/denominator
        w=1-u-v
        if min(u,v,w)>=-1e-10:
            return u*a[2]+v*b[2]+w*c[2]
    return None


def write_preview(mesh, out):
    out.mkdir(parents=True,exist_ok=True)
    (out/'terrain.json').write_text(json.dumps(mesh,ensure_ascii=False,indent=2))
    ox,oy,oz=mesh['origin']
    obj=['# Experimental terrain, not admitted; local origin '+str(mesh['origin'])]
    obj += ['v %.6f %.6f %.6f' % (v['xyz'][0]-ox,v['xyz'][1]-oy,v['xyz'][2]-oz) for v in mesh['vertices']]
    obj += ['f '+' '.join(str(i+1) for i in f) for f in mesh['triangles']]
    (out/'terrain.obj').write_text('\n'.join(obj)+'\n')
    def project(v):
        x,y,z=v['xyz'];x-=ox;y-=oy;z-=oz
        return (600+(x-y)*7, 720-(x+y)*4-z*8)
    points=[project(v) for v in mesh['vertices']]
    svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="900" viewBox="0 0 1200 900">',
         '<rect width="1200" height="900" fill="#f5f7f4"/>',
         '<g font-family="Arial" fill="#243831"><text x="45" y="55" font-size="27">Кустанайская · пробная высотная поверхность</text>',
         '<text x="45" y="85" font-size="17">Гипотеза по одиночным отметкам. Пробелы не заполнены. Без вертикального преувеличения.</text>']
    for f in sorted(mesh['triangles'],key=lambda f:sum(points[i][1] for i in f)):
        z=sum(mesh['vertices'][i]['xyz'][2] for i in f)/3
        light=75-(z-149)*9
        coords=' '.join('%.2f,%.2f'%points[i] for i in f)
        svg.append(f'<polygon points="{coords}" fill="hsl(155,25%,{light}%)" stroke="#547566" stroke-width="1.3"/>')
    for i,v in enumerate(mesh['vertices']):
        x,y=points[i]
        svg.append(f'<circle cx="{x}" cy="{y}" r="4" fill="#243831"/><text x="{x+7}" y="{y-7}" font-size="14">{v["xyz"][2]:.2f}</text>')
    svg.append('<text x="45" y="850" font-size="17">Экспериментальная геометрия для проверки; не финальный рендер и не подтверждённый рельеф.</text></g></svg>')
    (out/'preview.svg').write_text('\n'.join(svg))


if __name__=='__main__':
    mesh=build(json.loads(Path(sys.argv[1]).read_text()))
    write_preview(mesh,Path(sys.argv[2]))
    print(json.dumps({'vertices':len(mesh['vertices']),'triangles':len(mesh['triangles']),
                      'surface_area_xy':mesh['surface_area_xy'],'excluded':mesh['excluded_triangles']}))
