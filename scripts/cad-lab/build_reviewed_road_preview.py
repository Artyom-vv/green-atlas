"""Kustanayskaya-specific visual interpretation trial. NOT survey admission.
Pairs explicitly selected for a road-side hypothesis, never globally min-reduced.
"""
import json
from pathlib import Path
import sys
from shapely.geometry import Polygon,MultiPoint
from shapely.ops import triangulate
from build_terrain_preview import write_preview

# Visual source review: lower paired label tentatively assigned to road side.
# Alternative remains recorded. This is a manual hypothesis requiring confirmation.
SELECT=[('1D03','1440A','14409'),('1D0D','1440E','1440D'),
        ('7B','14228','14226'),('BA','14234','14233'),('AB','14230','1422F'),
        ('3A9','142A3',None),('3A4','142A2',None),('39F','142A1',None)]


def build(packet):
 ps={p['handle']:p for p in packet['pickets']};ts={t['handle']:t for t in packet['labels']}
 vertices=[]
 for ph,th,other in SELECT:
  p,t=ps[ph],ts[th]
  if not any(v['handle']==th for v in p['nearby_labels']):raise ValueError('selected label lacks proximity evidence')
  vertices.append({'xyz':[*p['xyz'][:2],t['value']],'picket':ph,'label':th,
    'status':'manual_road_side_hypothesis' if other else 'estimated_unconfirmed_ground',
    'alternative_label':other,'alternative_value':ts[other]['value'] if other else None,
    'interpretation':'road-side lower paired mark, visually selected; not confirmed' if other else 'single road interior mark'})
 polygon=Polygon([v['xyz'][:2] for v in vertices[:5]])
 if not polygon.is_valid:raise ValueError('invalid road trial footprint')
 lookup={tuple(v['xyz'][:2]):i for i,v in enumerate(vertices)};faces=[]
 for tri in triangulate(MultiPoint(list(lookup))):
  if polygon.covers(tri):faces.append([lookup[tuple(p)] for p in list(tri.exterior.coords)[:3]])
 area=sum(Polygon([vertices[i]['xyz'][:2] for i in f]).area for f in faces)
 return {'schema':'terrain-estimated-preview-v1','status':'experimental_manual_road_hypothesis',
  'source_sha256':packet['sha256'],'origin':[15840,-4980,150], 'vertices':vertices,'triangles':faces,
  'surface_area_xy':area,'surface_kind':'road','boundary':list(polygon.exterior.coords),
  'limitations':['Five boundary point associations are manual road-side hypotheses, not survey-confirmed.',
                 'Boundary connects source curb pickets by straight chords; intervening curb curvature is approximate.',
                 'No lawn/walkway heights are inferred.','Does not supersede the unknown status of source terrain.']}

if __name__=='__main__':
 mesh=build(json.loads(Path(sys.argv[1]).read_text()));write_preview(mesh,Path(sys.argv[2]))
 print(json.dumps({'vertices':len(mesh['vertices']),'triangles':len(mesh['triangles']),'area':mesh['surface_area_xy']}))
