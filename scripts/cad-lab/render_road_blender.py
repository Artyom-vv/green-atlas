"""Blender geometry proof from the explicitly reviewed road hypothesis.
Run: Blender -b -t 6 --python SCRIPT -- ROAD_JSON OUTPUT_DIR
Upper green bands are presentation-only (3m width), NOT recovered lawn terrain.
"""
import bpy
import json
import math
import sys
from pathlib import Path
from mathutils import Vector

source,out=map(Path,sys.argv[sys.argv.index('--')+1:]);out.mkdir(parents=True,exist_ok=True)
data=json.loads(source.read_text());origin=data['origin']
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)

def material(name,color,rough=.8,noise=False):
 m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True
 n=m.node_tree.nodes;bs=n.get('Principled BSDF');bs.inputs['Base Color'].default_value=(*color,1);bs.inputs['Roughness'].default_value=rough
 if noise:
  tex=n.new('ShaderNodeTexNoise');tex.inputs['Scale'].default_value=95
  bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.16;bump.inputs['Distance'].default_value=.025
  m.node_tree.links.new(tex.outputs['Fac'],bump.inputs['Height']);m.node_tree.links.new(bump.outputs['Normal'],bs.inputs['Normal'])
 return m
asphalt=material('Asphalt',(.10,.12,.13),noise=True)
concrete=material('Curb concrete',(.58,.58,.52),noise=True)
grass=material('Upper reference strip — NOT measured lawn',(.22,.34,.12),noise=True)
base=material('Neutral studio',(.61,.64,.60))

def mesh(name,vertices,faces,mat):
 me=bpy.data.meshes.new(name);me.from_pydata(vertices,[],faces);me.update()
 ob=bpy.data.objects.new(name,me);bpy.context.collection.objects.link(ob);ob.data.materials.append(mat);return ob
verts=[[v['xyz'][i]-origin[i] for i in range(3)] for v in data['vertices']]
road=mesh('Road — manual elevation interpretation',verts,data['triangles'],asphalt)
road['status']=data['status']
byid={v['picket']:(i,v) for i,v in enumerate(data['vertices'])}
receipt={'source':str(source.resolve()),'source_sha256':data['source_sha256'],
 'status':'visual_geometry_trial_not_survey_confirmed','upper_band_width_m':3.0,
 'upper_band_status':'presentation_only_not_measured_lawn','curbs':[]}
# Follow both original source curb picket chains; outward normal fixed by road centroid.
centroid=sum((Vector(v) for v in verts),Vector())/len(verts)
for chain in [['1D03','1D0D'],['AB','BA','7B']]:
 for ha,hb in zip(chain,chain[1:]):
  ia,a=byid[ha];ib,b=byid[hb];pa,pb=Vector(verts[ia]),Vector(verts[ib])
  ua=Vector((pa.x,pa.y,a['alternative_value']-origin[2]));ub=Vector((pb.x,pb.y,b['alternative_value']-origin[2]))
  direction=(pb-pa);direction.z=0;direction.normalize();normal=Vector((-direction.y,direction.x,0))
  if normal.dot((pa+pb)/2-centroid)<0:normal=-normal
  mesh('Curb face '+ha+'-'+hb,[pa,pb,ub,ua],[(0,1,2,3)],concrete)
  # Narrow cap is a rendering parameter, not inferred curb width.
  cap=.18
  mesh('Curb top '+ha+'-'+hb,[ua,ub,ub+normal*cap,ua+normal*cap],[(0,1,2,3)],concrete)
  mesh('Upper illustrative band '+ha+'-'+hb,[ua+normal*cap,ub+normal*cap,ub+normal*3,ua+normal*3],[(0,1,2,3)],grass)
  receipt['curbs'].append({'pickets':[ha,hb],'delta_m':[round(a['alternative_value']-a['xyz'][2],3),round(b['alternative_value']-b['xyz'][2],3)],'cap_width_m':cap,'cap_width_status':'presentation_parameter'})
# Subtle ground below model, deliberately separated from unknown surveyed surfaces.
bpy.ops.mesh.primitive_plane_add(size=200,location=(20,25,-.65));bpy.context.object.name='Studio background — not site ground';bpy.context.object.data.materials.append(base)
world=bpy.context.scene.world;world.use_nodes=True;world.node_tree.nodes['Background'].inputs[0].default_value=(.72,.79,.86,1);world.node_tree.nodes['Background'].inputs[1].default_value=.4
bpy.ops.object.light_add(type='AREA',location=(5,8,35));bpy.context.object.data.energy=6500;bpy.context.object.data.shape='DISK';bpy.context.object.data.size=20
bpy.ops.object.light_add(type='SUN',location=(0,0,20));bpy.context.object.rotation_euler=(.4,-.5,-.4);bpy.context.object.data.energy=2;bpy.context.object.data.angle=.15
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=32;scene.cycles.use_denoising=True
scene.render.resolution_x=1440;scene.render.resolution_y=1000;scene.render.resolution_percentage=100
scene.world.color=(.8,.8,.8);scene.view_settings.view_transform='AgX'

def camera(name,position,target,ortho=None):
 bpy.ops.object.camera_add(location=position);c=bpy.context.object;c.name=name
 c.rotation_euler=(Vector(target)-c.location).to_track_quat('-Z','Y').to_euler()
 if ortho:c.data.type='ORTHO';c.data.ortho_scale=ortho
 else:c.data.lens=40
 c.data.clip_end=500;return c
center=tuple(centroid)
full=camera('Overview',(centroid.x-40,centroid.y-35,42),center,54)
ia,a=byid['BA'];p=Vector(verts[ia]);close=camera('Curb inspection',p+Vector((-8,-7,3.2)),p+Vector((1,1,.1)))
scene.camera=full
bpy.ops.wm.save_as_mainfile(filepath=str((out/'road-curb-trial.blend').resolve()))
(out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
for cam,name in [(full,'overview'),(close,'curb-close')]:
 scene.camera=cam;scene.render.filepath=str((out/(name+'.png')).resolve());bpy.ops.render.render(write_still=True)
