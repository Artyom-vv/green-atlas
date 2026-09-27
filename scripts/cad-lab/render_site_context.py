"""Bounded Kustanayskaya scene: source tree XY/height hypotheses + tagged context."""
import bpy,json,math,random,os
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2];out=ROOT/'.runtime/site-context-20260919'
p=json.loads((out/'scene-input.json').read_text());road=json.loads((ROOT/'.runtime/terrain-control-20260918/road-trial/terrain.json').read_text());origin=Vector(road['origin'])
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'.runtime/material-quality-trial-20260919/material-quality.blend'),use_scripts=False)
for ob in list(bpy.context.scene.objects):
 if ob.type not in {'CAMERA','LIGHT'}:bpy.data.objects.remove(ob,do_unlink=True)
vs=[Vector(v['xyz'])-origin for v in road['vertices']]
def z(x,y):
 for ids in road['triangles']:
  a,b,c=[vs[i] for i in ids];den=(b.y-c.y)*(a.x-c.x)+(c.x-b.x)*(a.y-c.y)
  u=((b.y-c.y)*(x-c.x)+(c.x-b.x)*(y-c.y))/den;v=((c.y-a.y)*(x-c.x)+(a.x-c.x)*(y-c.y))/den
  if min(u,v,1-u-v)>=-1e-6:return u*a.z+v*b.z+(1-u-v)*c.z
 weights=[1/max((x-a.x)**2+(y-a.y)**2,1) for a in vs]
 return sum(w*a.z for w,a in zip(weights,vs))/sum(weights)
def mat(name,color,rough=.85):
 m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True;s=m.node_tree.nodes.get('Principled BSDF');s.inputs['Base Color'].default_value=(*color,1);s.inputs['Roughness'].default_value=rough;return m
def mesh(name,vertices,faces,material):
 me=bpy.data.meshes.new(name);me.from_pydata(vertices,[],faces);me.update();ob=bpy.data.objects.new(name,me);bpy.context.collection.objects.link(ob);me.materials.append(material);return ob
materials={'road':bpy.data.materials['Scanned asphalt01'],'walk':bpy.data.materials['Scanned concrete'],'grass':bpy.data.materials['Ground beneath blades'],'ground':mat('Context soil',(.24,.23,.20))}
white=mat('White context building',(.69,.71,.70));curb=materials['walk']
for kind in materials:
 verts=[];faces=[]
 for tr in p['surfaces']:
  if tr['kind']!=kind:continue
  coords=[(x-origin.x,y-origin.y) for x,y in tr['xy']];offset=0 if kind=='road' else .18
  faces.append(tuple(range(len(verts),len(verts)+3)));verts.extend((x,y,z(x,y)+offset) for x,y in coords)
 ob=mesh('Context '+kind,verts,faces,materials[kind]);ob['geometry_status']='manual_trace_or_context_assumption';uv=ob.data.uv_layers.new()
 for face in ob.data.polygons:
  for i in face.loop_indices:
   v=ob.data.vertices[ob.data.loops[i].vertex_index].co;uv.data[i].uv=(v.x/2.1,v.y/2.1)
# Road perimeter follows the manually traced DXF context; curb height is assumed.
outline=p['context']['zones'][0]['xy'];verts=[];faces=[]
for a,b in zip(outline,outline[1:]+outline[:1]):
 a=Vector((a[0]-origin.x,a[1]-origin.y,0));b=Vector((b[0]-origin.x,b[1]-origin.y,0));steps=max(1,math.ceil((b-a).length/2))
 for i in range(steps):
  aa=a.lerp(b,i/steps);bb=a.lerp(b,(i+1)/steps);j=len(verts)
  verts.extend([(aa.x,aa.y,z(aa.x,aa.y)),(bb.x,bb.y,z(bb.x,bb.y)),(bb.x,bb.y,z(bb.x,bb.y)+.18),(aa.x,aa.y,z(aa.x,aa.y)+.18)]);faces.append((j,j+1,j+2,j+3))
mesh('Context curb — assumed 18cm',verts,faces,curb)
for b in p['context']['buildings']:
 xy=[(x-origin.x,y-origin.y) for x,y in b['xy'][:-1]];base=sum(z(x,y) for x,y in xy)/len(xy)+.18;count=len(xy)
 verts=[(x,y,h) for h in [base,base+b['height']] for x,y in xy];faces=[tuple(range(count-1,-1,-1)),tuple(range(count,count*2))]+[(i,(i+1)%count,(i+1)%count+count,i+count) for i in range(count)]
 ob=mesh('Source building '+b['handle'],verts,faces,white);ob['height_status']='assumed_3.5m';ob['source_handle']=b['handle'];bevel=ob.modifiers.new('Small edge bevel','BEVEL');bevel.width=.04;bevel.segments=2
assets=ROOT/'.runtime/botaniq-68-inspection/selected/botaniq_full';types={'Липа':('deciduous','Tilia-europaea_A_summer'),'Клен':('deciduous','Acer-pseudoplatanus_A_summer'),'Береза':('deciduous','Betula-pendula_A_summer'),'Ель':('coniferous','Picea-abies_A_spring-summer-autumn')};cols={}
for species,(folder,name) in types.items():
 with bpy.data.libraries.load(str(assets/'blends_280'/folder/('Tree_'+name+'.blend')),link=False) as (a,b):b.objects=a.objects
 col=bpy.data.collections.new('Site asset '+species)
 for ob in b.objects:
  if ob:col.objects.link(ob)
 cols[species]=(col,max(o.dimensions.z for o in b.objects if o and o.type=='MESH'))
textures={x.name:x for x in (assets/'textures').rglob('*') if x.is_file()}
for im in bpy.data.images:
 if im.source=='FILE' and not im.packed_file:
  name=Path(im.filepath.replace('\\','/')).name
  if name in textures:im.filepath=str(textures[name]);im.reload()
for m in bpy.data.materials:
 if m.use_nodes and m.name.startswith('bq_Leaf_'):
  for n in m.node_tree.nodes:
   if n.type=='GROUP':
    for key,val in [('Normal Strength',.22),('Bump Strength',.1),('Roughness',.64),('Specular',.28)]:
     if key in n.inputs:n.inputs[key].default_value=val
rng=random.Random(1919)
def instance(name,col,xyz,scale):
 ob=bpy.data.objects.new(name,None);ob.instance_type='COLLECTION';ob.instance_collection=col;bpy.context.collection.objects.link(ob);ob.location=xyz;ob.scale=(scale,)*3;ob.rotation_euler.z=rng.random()*math.tau;return ob
for t in p['trees']:
 col,height=cols[t['species']];x,y=t['xy'][0]-origin.x,t['xy'][1]-origin.y
 ob=instance('Inventory '+str(t['number'])+' '+t['species'],col,(x,y,z(x,y)+.18),t['height_m']/height)
 ob['source_handle']=t['handle'];ob['inventory_number']=t['number'];ob['association_status']=t['link_status'];ob['base_elevation_status']='visual_interpolation_not_surveyed'
# Sparse lawn instances over the context polygons, deterministic and explicitly illustrative.
grass=bpy.data.collections['Asset grass'];grass_count=0
for tr in p['surfaces']:
 if tr['kind']!='grass':continue
 a,b,c=[Vector((x-origin.x,y-origin.y,0)) for x,y in tr['xy']];area=(b-a).cross(c-a).length/2
 for _ in range(int(area*5)):
  u,v=rng.random(),rng.random()
  if u+v>1:u,v=1-u,1-v
  q=a+(b-a)*u+(c-a)*v;instance('Context grass',grass,(q.x,q.y,z(q.x,q.y)+.18),1.15+rng.random()*.2);grass_count+=1
# Keep the accepted shrub asset as illustrative context, separate from inventory trees.
shrubs=bpy.data.collections['Asset shrub']
for x,y in [(32,47),(35,44),(37,41)]:instance('Illustrative shrub',shrubs,(x,y,z(x,y)+.18),.37)
scene=bpy.context.scene;prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='METAL';prefs.get_devices()
for d in prefs.devices:d.use=d.type=='METAL'
scene.cycles.device='GPU';scene.cycles.samples=int(os.environ.get('SITE_SAMPLES','192'));scene.cycles.adaptive_threshold=.006
scene.render.resolution_x=1920;scene.render.resolution_y=1280
cam=scene.camera
receipt={'surface_tree_conflicts':p['surface_tree_conflicts'],'coverage_error_m2':p['coverage_error_m2'],'tree_count':len(p['trees']),'grass_instances':grass_count,'assumptions':p['assumptions'],'building_count':len(p['context']['buildings']),'origin':list(origin),'cameras':{}}
for name,pos,target,lens in [('street',(23,-5,z(23,-5)+1.7),(68,33,4.0),26),('overview',(-85,-105,145),(50,40,0),45)]:
 cam.location=pos;cam.data.type='PERSP';cam.data.lens=lens;cam.rotation_euler=(Vector(target)-cam.location).to_track_quat('-Z','Y').to_euler();
 if name=='overview':cam.data.type='ORTHO';cam.data.ortho_scale=225
 receipt['cameras'][name]={'position':list(pos),'target':list(target),'lens':lens}
 scene.render.filepath=str(out/(name+'.png'))
 if name=='street':bpy.ops.wm.save_as_mainfile(filepath=str(out/'site-context.blend'))
 bpy.ops.render.render(write_still=True)
(out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
