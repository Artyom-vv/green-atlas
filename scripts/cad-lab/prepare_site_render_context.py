"""Prepare source-backed trees and explicitly assumed context surfaces for Blender."""
import json,math,collections
from pathlib import Path
import ezdxf
from shapely import constrained_delaunay_triangles
from shapely.geometry import Polygon,Point,box
from shapely.ops import triangulate,unary_union
ROOT=Path(__file__).resolve().parents[2];out=ROOT/'.runtime/site-context-20260919';out.mkdir(exist_ok=True)
c=json.loads((ROOT/'.runtime/terrain-context-road-20260918/context.json').read_text())
t=json.loads((ROOT/'.runtime/cad-vegetation-20260919/trees.json').read_text());doc=ezdxf.readfile(t['source'])
points=[e for e in doc.modelspace().query('INSERT') if e.dxf.layer=='!!!_1. Дендра_сохранить']
leaders=[e for e in doc.modelspace().query('LWPOLYLINE') if e.dxf.layer=='!!!_1. Дендра_выноски_сохранить']
accepted=[];rejected=[]
for row in t['matched']:
 text=doc.entitydb[row['number_handle']];anchor=list(text.dxf.insert)[:2]
 nearest=min(points,key=lambda p:math.dist(anchor,list(p.dxf.insert)[:2]))
 ends=[list(e.get_points('xy')) for e in leaders];near=[e.dxf.handle for e,xy in zip(leaders,ends) if xy and min(math.dist(row['xy'],xy[0]),math.dist(row['xy'],xy[-1]))<.25]
 if nearest.dxf.handle!=row['handle'] or near:
  rejected.append({**row,'reason':'nonreciprocal_or_leader_nearby','leaders':near});continue
 accepted.append({**row,'link_status':'reciprocal_nearest_no_nearby_leader_still_hypothesis'})
assert len({x['number'] for x in accepted})==len(accepted)
bounds=box(*c['bbox']);road=Polygon(c['zones'][0]['xy']);grass=unary_union([Polygon(z['xy']) for z in c['zones'] if z['kind']=='grass'])
buildings=unary_union([Polygon(b['xy']) for b in c['buildings']])
road=road.difference(buildings)
grass=grass.difference(road).difference(buildings)
# A two-metre walkway is a visual assumption, NOT a recovered DXF pavement boundary.
walk=road.buffer(2,join_style=2).difference(road).difference(grass).difference(buildings).intersection(bounds)
base=bounds.difference(unary_union([road,grass,walk,buildings]))
surfaces=[]
for kind,g in [('road',road),('grass',grass),('walk',walk),('ground',base)]:
 parts=[g] if g.geom_type=='Polygon' else list(g.geoms)
 for p in parts:
  # Clip into 5m cells to let preview elevation vary without long flat triangles.
  for x in range(15810,15970,5):
   for y in range(-5000,-4880,5):
    cut=p.intersection(box(x,y,x+5,y+5))
    if cut.is_empty:continue
    for tr in constrained_delaunay_triangles(cut).geoms:
     if tr.area>1e-10:surfaces.append({'kind':kind,'xy':list(tr.exterior.coords)[:3]})
coverage=unary_union([Polygon(s['xy']) for s in surfaces]+[buildings])
assert bounds.symmetric_difference(coverage).area<1e-5
assert abs(sum(Polygon(s['xy']).area for s in surfaces)+buildings.area-bounds.area)<1e-5
conflicts=[x['number'] for x in accepted if road.contains(Point(x['xy']))]
packet={'surface_tree_conflicts':conflicts,'coverage_error_m2':bounds.symmetric_difference(coverage).area,'context':c,'trees':accepted,'rejected_pairs':rejected,'unmatched_tree_count':len(t['rejected']),'surfaces':surfaces,'assumptions':{'surfaces':'prior manual DXF trace; walkway 2m buffer visual assumption','elevation':'manual road triangles, inverse-distance visual interpolation outside; not surveyed DTM','building_height':'3.5m assumption','species_models':'representative species assets; exact cultivar/crown unknown'}}
(out/'scene-input.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2))
print(json.dumps({'trees':len(accepted),'species':dict(collections.Counter(x['species'] for x in accepted)),'rejected':len(rejected),'triangles':len(surfaces),'buildings':len(c['buildings'])},ensure_ascii=False))
