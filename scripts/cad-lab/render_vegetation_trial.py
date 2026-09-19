"""Render selected botaniq geometry in the manual road trial; planting is illustrative."""
import bpy,json,random,math
from pathlib import Path
from mathutils import Vector
ROOT=Path('/Users/artem/Documents/ЛЦТ');out=ROOT/'.runtime/vegetation-trial-20260919';out.mkdir(exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'.runtime/terrain-blender-20260918/road-curb-trial.blend'),use_scripts=False)
assets=ROOT/'.runtime/botaniq-68-inspection/selected/botaniq_full'
paths={'tree':'blends_280/deciduous/Tree_Tilia-europaea_A_summer.blend','shrub':'blends_280/shrubs/Shrub_Syringa-vulgaris_A_spring-summer-autumn.blend','grass':'blends_280/grass/Grass_Basic_A_spring-summer.blend'}
source={}
required=assets/'blends_280/Library_Botaniq_Materials.blend'
if not required.exists():raise RuntimeError('Missing shared botaniq material library')
for role,p in paths.items():
 with bpy.data.libraries.load(str(assets/p),link=False) as (a,b):b.objects=a.objects
 source[role]=next(o for o in b.objects if o and o.type=='MESH')
textures={p.name:p for p in (assets/'textures').rglob('*') if p.is_file()};missing=[]
for im in bpy.data.images:
 if im.source!='FILE' or im.packed_file:continue
 name=Path(im.filepath.replace('\\','/')).name
 if name in textures:im.filepath=str(textures[name]);im.reload()
 elif im.filepath:missing.append(im.filepath)
if missing:raise RuntimeError('Missing image textures: '+str(missing))
collections={}
for role,ob in source.items():
 col=bpy.data.collections.new('Asset '+role);col.objects.link(ob);collections[role]=col
rng=random.Random(42);counts={'tree':0,'shrub':0,'grass':0}
def spawn(role,position,scale):
 ob=bpy.data.objects.new(role+' instance',None);ob.instance_type='COLLECTION';ob.instance_collection=collections[role];bpy.context.collection.objects.link(ob)
 ob.location=position;ob.scale=(scale,)*3;ob.rotation_euler.z=rng.random()*math.tau
 ob['placement_status']='illustrative_not_DXF_planting';counts[role]+=1
 return ob
# Place only in existing upper demonstration bands, interpolate their elevations.
road=json.loads((ROOT/'.runtime/terrain-control-20260918/road-trial/terrain.json').read_text());origin=road['origin'];v={a['picket']:a for a in road['vertices']}
center=sum((Vector(a['xyz']) for a in road['vertices']),Vector())/len(v)
for chain in [['1D03','1D0D'],['AB','BA','7B']]:
 for ha,hb in zip(chain,chain[1:]):
  a,b=v[ha],v[hb];pa=Vector((*a['xyz'][:2],a['alternative_value']));pb=Vector((*b['xyz'][:2],b['alternative_value']))
  d=pb-pa;length=Vector((d.x,d.y,0)).length;normal=Vector((-d.y,d.x,0)).normalized()
  if normal.dot((pa+pb)/2-center)<0:normal=-normal
  def at(t,offset):return pa.lerp(pb,t)+normal*offset-Vector(origin)
  for t in [.22,.75]:spawn('tree',at(t,1.5),.78+rng.random()*.13)
  for t in [.4,.5,.6]:spawn('shrub',at(t,1.7),.34+rng.random()*.06)
  for k in range(int(length*96)):
   spawn('grass',at(rng.random(),.4+rng.random()*2.15),.42+rng.random()*.2)
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=24;scene.cycles.use_denoising=True
prefs=bpy.context.preferences.addons['cycles'].preferences
try:
 prefs.compute_device_type='METAL';prefs.get_devices()
 for d in prefs.devices:d.use=d.type=='METAL'
 if any(d.type=='METAL' for d in prefs.devices):scene.cycles.device='GPU'
except Exception as ex:print('GPU setup',ex)
scene.render.resolution_x=1200;scene.render.resolution_y=900;scene.render.resolution_percentage=100
cam=bpy.data.objects['Overview'];scene.camera=cam
cam.location=( -14,-23,28);target=Vector((22,24,5));cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=49
scene.view_settings.look="AgX - Medium High Contrast"
scene.view_settings.exposure=.5
# Render geometry and natural foliage without wire overlays.
scene.render.filepath=str(out/'vegetation.png')
receipt={'status':'illustrative_asset_quality_trial','planting_from_DXF':False,'counts':counts,'missing_textures':missing,'engine':scene.render.engine,'device':scene.cycles.device,'models':paths,'upper_band_width_m':3,'road_geometry':'manual_road_hypothesis'}
(out/'receipt.json').write_text(json.dumps(receipt,indent=2))
bpy.ops.wm.save_as_mainfile(filepath=str(out/'vegetation-trial.blend'))
bpy.ops.render.render(write_still=True)
