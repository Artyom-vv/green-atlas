"""Bounded IP/perechet pairing; nearest annotation is a recorded hypothesis."""
import json,re,math
from pathlib import Path
import ezdxf
ROOT=Path(__file__).resolve().parents[2];folder=ROOT/'.runtime/kustanayskaya-mac-ready-20260917/dxf'
source=next(p for p in folder.rglob('*.dxf') if p.name=='ИП_Кустанайская улица.dxf');doc=ezdxf.readfile(source)
rows={}
for line in (ROOT/'.runtime/terrain-control-20260918/pdf-6.txt').read_text().splitlines():
 m=re.match(r'^\s*(\d+)\s+(Липа|Клен|Береза|Ель|Сосна)\s+1\s+[\d.,-]+\s+(\d+(?:[.,]\d+)?)\s+',line)
 if m:rows[int(m[1])]={'species':m[2],'height_m':float(m[3].replace(',','.')),'source_row':line.strip()}
points=[e for e in doc.modelspace().query('INSERT') if e.dxf.layer=='!!!_1. Дендра_сохранить' and 15810<e.dxf.insert.x<15970 and -5000<e.dxf.insert.y<-4880]
texts=[e for e in doc.modelspace().query('MTEXT') if 'номера_сохранить' in e.dxf.layer and e.plain_text().strip().isdigit()]
matched=[];rejected=[]
for p in points:
 rank=sorted([(math.dist(list(p.dxf.insert)[:2],list(t.dxf.insert)[:2]),t) for t in texts],key=lambda r:r[0])
 dist,t=rank[0];number=int(t.plain_text().strip())
 if dist>1.2 or rank[1][0]-dist<.8 or number not in rows:
  rejected.append({'handle':p.dxf.handle,'nearest_number':number,'distance':dist,'reason':'ambiguous_or_unsupported_inventory_row'});continue
 matched.append({'handle':p.dxf.handle,'number_handle':t.dxf.handle,'number':number,'xy':list(p.dxf.insert)[:2],'distance_to_number':dist,'link_status':'nearest_annotation_hypothesis',**rows[number]})
assert len(set(m['number'] for m in matched))==len(matched)
out=ROOT/'.runtime/cad-vegetation-20260919/trees.json';out.write_text(json.dumps({'source':str(source),'matched':matched,'rejected':rejected,'base_elevation_status':'not_reconstructed'},ensure_ascii=False,indent=2))
print('matched',len(matched),'rejected',len(rejected));print([(m['number'],m['species'],m['height_m']) for m in matched])
