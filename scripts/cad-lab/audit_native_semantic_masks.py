from pathlib import Path
import json,hashlib,argparse
import numpy as np
from PIL import Image
parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True);args=parser.parse_args();ROOT=args.root
palette={'road':(90,95,105),'sidewalk':(40,175,225),'lawn':(85,200,75),'curb':(245,225,140),'building':(90,120,225),'vegetation':(15,105,40),'furniture':(190,100,220),'unknown':(240,35,170),'sky':(170,220,255),'marking':(255,255,255),'context_lawn':(180,225,65)}
reports=[]
for d in sorted(ROOT.glob('0*-*')):
 if not d.is_dir() or not (d/'native-v2.png').exists():continue
 masks={name:np.asarray(Image.open(d/f'mask-{name}-0001.png').convert('L')) for name in palette}
 unique={name:np.unique(a).tolist() for name,a in masks.items()}
 binary={name:a>127 for name,a in masks.items()};coverage=sum(a.astype(np.uint8) for a in binary.values())
 diagnostic=np.zeros((*coverage.shape,3),dtype=np.uint8)
 for name,b in binary.items():diagnostic[b]=palette[name]
 Image.fromarray(diagnostic).save(d/'semantic-diagnostic-v1.png')
 image=np.asarray(Image.open(d/'native-v2.png').convert('RGB'))/255
 luminance=image@np.array([.2126,.7152,.0722])
 reports.append({'view':d.name,'binary_masks':all(set(v)<=set([0,255]) for v in unique.values()),'unassigned_pixels':int((coverage==0).sum()),'overlap_pixels':int((coverage>1).sum()),'classes':{name:{'pixels':int(b.sum()),'share':float(b.mean()),'median_luminance':float(np.median(luminance[b])) if b.any() else None} for name,b in binary.items()},'mask_sha256':{name:hashlib.sha256((d/f'mask-{name}-0001.png').read_bytes()).hexdigest() for name in palette}})
(ROOT/'mask-audit.json').write_text(json.dumps({'views':reports,'palette':palette},indent=2))
print(json.dumps([{k:r[k] for k in ('view','binary_masks','unassigned_pixels','overlap_pixels')} for r in reports],indent=2))
