"""Contextual CAD evidence figure, with explicitly hand-traced presentation zones.

API venv: render_terrain_context.py SOURCE_DXF MESH_JSON OUTPUT_DIR
No project edits. Buildings use source footprints and assumed 3.5m height.
Presentation surfaces lie on a flat reference plane, not an invented DTM.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
import html

import ezdxf
from ezdxf.disassemble import recursive_decompose
from ezdxf.path import make_path
from shapely.geometry import LineString, box, Polygon

BOUNDS=(15810,-5000,15970,-4880)
# Manually traced on the inspected 1600x1200 DXF overview, not automatic admission.
# Pixel-to-WCS mapping is exact for that overview extent.
ZONES=[
 ('road',[(0,450),(460,278),(777,124),(857,270),(570,405),(550,449),(520,480),(507,542),(554,610),(979,1200),(712,1200),(257,592),(215,563),(0,652)]),
 ('grass',[(577,461),(751,377),(794,404),(811,468),(789,534),(737,623),(674,664),(610,604),(566,544),(559,514)]),
 ('grass',[(805,704),(972,760),(983,812),(920,885),(893,967),(834,904),(755,810),(715,747)]),
 ('grass',[(64,291),(231,224),(363,306),(400,300),(453,264),(580,208),(753,106),(727,16),(0,308)]),
 ('grass',[(1,715),(223,626),(278,659),(479,871),(604,1100),(638,1200),(0,1200)]),
 ('grass',[(1200,1020),(1476,872),(1530,789),(1511,968),(1497,1200),(1334,1200)]),
]

def world(pixel):return (BOUNDS[0]+pixel[0]/10,BOUNDS[3]-pixel[1]/10)
def esc(s):return html.escape(str(s))


def main():
 source,meshpath,out=map(Path,sys.argv[1:]);out.mkdir(parents=True,exist_ok=True)
 doc=ezdxf.readfile(source);mesh=json.loads(meshpath.read_text());extent=box(*BOUNDS)
 area=mesh['surface_area_xy']; face_count=len(mesh['triangles'])
 heights=[mesh['vertices'][i]['xyz'][2] for f in mesh['triangles'] for i in f]
 zmin,zmax=min(heights),max(heights)
 target=tuple(sum(v['xyz'][i] for v in mesh['vertices'])/len(mesh['vertices']) for i in range(3))
 lines=[];buildings=[]
 layers={'Бортовой камень','Откосы','Граница растительности и грунта','Здания','Крыльца','Ограды','Полоса деревьев','Леса и газоны','Отдельно стоящее дерево'}
 for e in recursive_decompose(doc.modelspace()):
  if e.dxf.layer not in layers or e.dxftype() not in {'LINE','LWPOLYLINE','ARC','CIRCLE','ELLIPSE'}:continue
  try:
   if e.dxftype()=='LINE':coords=[list(e.dxf.start)[:2],list(e.dxf.end)[:2]]
   else:coords=[list(p)[:2] for p in make_path(e).flattening(.08)]
   if len(coords)<2:continue
   line=LineString(coords)
   if not line.intersects(extent):continue
   clipped=line.intersection(extent)
   for part in ([clipped] if clipped.geom_type=='LineString' else getattr(clipped,'geoms',[])):
    if part.geom_type=='LineString':lines.append({'xy':list(part.coords),'layer':e.dxf.layer})
   if e.dxf.layer=='Здания' and e.dxftype()=='LWPOLYLINE' and math.dist(coords[0],coords[-1])<.01:
    poly=Polygon(coords)
    if poly.is_valid and poly.area>50 and extent.covers(poly):
     buildings.append({'handle':e.dxf.handle,'xy':coords,'height':3.5,'height_status':'assumed_for_context'})
  except (ValueError,TypeError):continue
 zones=[{'kind':k,'xy':[world(v) for v in vs],'status':'manual_visual_trace_not_product_geometry'} for k,vs in ZONES]
 receipt={'source':str(source.resolve()),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'bbox':BOUNDS,
          'zones':zones,'buildings':buildings,'mesh_source':str(meshpath.resolve()),
          'context_elevation':150,'context_elevation_status':'flat_reference_plane_not_terrain',
          'line_count':len(lines),'limitations':['Road/grass fills manually traced from DXF overview.',
          'Building height assumed 3.5m; source footprint preserved.','Only turquoise triangles carry reconstructed elevation.',
          'All reconstruction remains experimental; unknown terrain is not inferred.']}
 (out/'context.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
 svg=['<svg xmlns="http://www.w3.org/2000/svg" width="2000" height="1400" viewBox="0 0 2000 1400">',
 '<defs><pattern id="unknown" width="12" height="12" patternUnits="userSpaceOnUse"><path d="M0 12L12 0" stroke="#bcb7a9" stroke-width=".55" opacity=".4"/></pattern><filter id="shadow"><feDropShadow dx="0" dy="5" stdDeviation="7" flood-opacity=".12"/></filter></defs>',
 '<rect width="2000" height="1400" fill="#eef0ed"/>',
 '<g font-family="Arial, sans-serif" fill="#22352e">',
 '<text x="60" y="64" font-size="35" font-weight="bold">Кустанайская × Шипиловская</text>',
 '<text x="60" y="102" font-size="21">Исходная улица и место первого фрагмента рельефа · участок 160 × 120 м</text>']
 def text(x,y,s,size=18,color='#22352e',weight='normal'):
  svg.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}">{esc(s)}</text>')
 def path(xyz,project,fill='none',stroke='none',width=1,close=False,extra=''):
  if not xyz:return
  points=[project(*v) for v in xyz]
  d='M'+' L'.join(f'{x:.2f},{y:.2f}' for x,y in points)+(' Z' if close else '')
  svg.append(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{width}" {extra}/>')
 for mode,xoff in [('plan',50),('oblique',1030)]:
  svg.append(f'<rect x="{xoff}" y="145" width="920" height="1000" rx="16" fill="#fff"/>')
  text(xoff+28,190,'01  Вид сверху' if mode=='plan' else '02  Наклонный вид',25,weight='bold')
  text(xoff+28,221,'Совмещение с линиями исходного DXF' if mode=='plan' else 'Тот же участок; контекст на условной плоскости',17)
  if mode=='plan':
   def project(x,y,z=150):return (xoff+28+(x-BOUNDS[0])*5.4,285+(BOUNDS[3]-y)*5.4)
  else:
   def project(x,y,z=150):
    a=x-BOUNDS[0];b=y-BOUNDS[1]
    return (xoff+440+(a-b)*3.05, 905-(a+b)*1.9-(z-150)*4.31)
  rect=[(BOUNDS[0],BOUNDS[1],150),(BOUNDS[2],BOUNDS[1],150),(BOUNDS[2],BOUNDS[3],150),(BOUNDS[0],BOUNDS[3],150)]
  path(rect,project,'#e8e3d7','#c8c3b5',1,True)
  for zone in zones:
   path([(*p,150) for p in zone['xy']],project,'#949b9c' if zone['kind']=='road' else '#b8cfa7',close=True)
  path(rect,project,'url(#unknown)',close=True)
  for l in lines:
   path([(*p,150) for p in l['xy']],project,stroke='#526256' if l['layer']=='Бортовой камень' else '#899084',width=.65 if l['layer']=='Бортовой камень' else .45)
  # Sparse measured fragment is intentionally distinguishable from presentation fills.
  for face in mesh['triangles']:
   path([mesh['vertices'][i]['xyz'] for i in face],project,'#36b8ac','#087a75',1.8,True, 'opacity=".9"')
  for b in buildings:
   pts=b['xy']
   if mode=='oblique':
    walls=[]
    for a,c in zip(pts,pts[1:]):
     walls.append((sum(project(*p)[1] for p in [(*a,150),(*c,150)]),[(*a,150),(*c,150),(*c,153.5),(*a,153.5)]))
    for _,v in sorted(walls):path(v,project,'#d6dcda','#889a92',.8,True)
   path([(*p,153.5 if mode=='oblique' else 150) for p in pts],project,'#fff','#75867f',1.5,True)
  # Label known street names, with direct evidence from the drawing.
  for p,name in [((15870,-4910),'ШИПИЛОВСКАЯ'),((15867,-4968),'КУСТАНАЙСКАЯ')]:
   x,y=project(*p,150);svg.append(f'<text x="{x}" y="{y}" text-anchor="middle" font-size="15" font-weight="bold" fill="#fff" stroke="#707b7a" stroke-width="3" paint-order="stroke">{name}</text>')
  def callout(target,label,tx,ty):
   px,py=project(*target)
   svg.append(f'<path d="M{px},{py} L{tx-12},{ty-6}" fill="none" stroke="#284b40" stroke-width="1.5"/><circle cx="{px}" cy="{py}" r="4" fill="#284b40"/>')
   text(tx,ty,label,17,weight='bold')
  if mode=='plan':
   callout(target,f'Пробная поверхность · {area:.0f} м²',xoff+460,265)
   callout((15935,-4960,150),'Здание из DXF',xoff+580,1010)
   text(xoff+28,1080,f'Бирюзовый — {face_count} граней; выбранные высоты требуют проверки.',16)
   text(xoff+28,1110,'Штриховка — высота поверхности ещё не восстановлена.',16)
   # Scale: 20m.
   svg.append(f'<path d="M{xoff+40},970 h108" stroke="#22352e" stroke-width="3"/>');text(xoff+40,995,'20 м',15)
  else:
   callout((15933,-4960,153.5),'Контур точный; высота условно 3.5 м',xoff+315,300)
   callout(target,f'Высоты: {zmin:.2f}–{zmax:.2f} м',xoff+40,1010)
   text(xoff+28,1080,'Дорога и газоны показаны для ориентации на плоскости Z = 150.',16)
   text(xoff+28,1110,'Это сборка сцены, а не готовая поверхность всей улицы.',16)
 text(60,1200,'Что здесь взято из данных',23,weight='bold')
 text(60,1235,'Линии бордюров, откосов и объектов; контур здания; подписи высот и положение пикетов.',20)
 text(60,1285,'Что пока условно',23,weight='bold')
 text(60,1320,'Зоны и выбор дорожной стороны парных отметок — ручная гипотеза. Высота здания условная. Пробелы не заполнены.',20)
 svg.append('</g></svg>');(out/'context.svg').write_text('\n'.join(svg))
 print(json.dumps({'lines':len(lines),'buildings':[(b['handle'],b['height']) for b in buildings],'zones':len(zones)}))

if __name__=='__main__':main()
