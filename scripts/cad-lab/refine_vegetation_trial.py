"""Second visual trial: metric materials, stratified grass, street-level camera.
All planting remains illustrative, not an approved design from DXF.
"""
import bpy,json,math,random
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2];out=ROOT/'.runtime/vegetation-refined-20260919';out.mkdir(exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'.runtime/vegetation-trial-20260919/vegetation-trial.blend'),use_scripts=False)
def surface(name,lo,hi,scale,rough,bump):
 m=bpy.data.materials.new(name);m.use_nodes=True;n=m.node_tree.nodes;l=m.node_tree.links;n.clear()
 output=n.new('ShaderNodeOutputMaterial');bs=n.new('ShaderNodeBsdfPrincipled');bs.inputs['Roughness'].default_value=rough
 geo=n.new('ShaderNodeNewGeometry');noise=n.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=scale;noise.inputs['Detail'].default_value=2
 ramp=n.new('ShaderNodeValToRGB');ramp.color_ramp.elements[0].color=(*lo,1);ramp.color_ramp.elements[1].color=(*hi,1)
 b=n.new('ShaderNodeBump');b.inputs['Strength'].default_value=.3;b.inputs['Distance'].default_value=bump
 l.new(geo.outputs['Position'],noise.inputs['Vector']);l.new(noise.outputs['Fac'],ramp.inputs[0]);l.new(ramp.outputs[0],bs.inputs['Base Color']);l.new(noise.outputs['Fac'],b.inputs['Height']);l.new(b.outputs['Normal'],bs.inputs['Normal']);l.new(bs.outputs[0],output.inputs[0]);return m
asphalt=surface('Asphalt metric aggregate',(.032,.038,.041),(.12,.13,.135),180,.93,.002)
concrete=surface('Concrete metric pores',(.31,.32,.29),(.51,.52,.48),85,.86,.001)
turf=surface('Ground beneath blades',(.032,.055,.013),(.065,.105,.024),25,.95,.005)
for ob in bpy.data.objects:
 mat=asphalt if ob.name.startswith('Road —') else concrete if ob.name.startswith('Curb ') else turf if ob.name.startswith('Upper illustrative') else None
 if mat and ob.type=='MESH':ob.data.materials.clear();ob.data.materials.append(mat)
for ob in list(bpy.data.objects):
 if ob.name.startswith('grass instance'):bpy.data.objects.remove(ob,do_unlink=True)
road=json.loads((ROOT/'.runtime/terrain-control-20260918/road-trial/terrain.json').read_text());v={a['picket']:a for a in road['vertices']};origin=Vector(road['origin']);center=sum((Vector(a['xyz']) for a in road['vertices']),Vector())/len(v)
rng=random.Random(43);count=0
for chain in [['1D03','1D0D'],['AB','BA','7B']]:
 for ha,hb in zip(chain,chain[1:]):
  a,b=v[ha],v[hb];pa=Vector((*a['xyz'][:2],a['alternative_value']));pb=Vector((*b['xyz'][:2],b['alternative_value']));delta=pb-pa;length=Vector((delta.x,delta.y,0)).length
  normal=Vector((-delta.y,delta.x,0)).normalized()
  if normal.dot((pa+pb)/2-center)<0:normal=-normal
  steps=math.ceil(length/.19)
  for i in range(steps):
   for j in range(13):
    t=(i+.15+.7*rng.random())/steps;offset=.36+(j+.2+.6*rng.random())*.19
    ob=bpy.data.objects.new('Grass blade patch',None);ob.instance_type='COLLECTION';ob.instance_collection=bpy.data.collections['Asset grass'];bpy.context.collection.objects.link(ob)
    ob.location=pa.lerp(pb,t)+normal*offset-origin;scale=.49+.09*rng.random();ob.scale=(scale,scale,scale*.8);ob.rotation_euler.z=rng.random()*math.tau;count+=1
scene=bpy.context.scene
# Physical sky provides foliage fill rather than only a studio background.
w=scene.world;w.use_nodes=True;n=w.node_tree.nodes;l=w.node_tree.links;n.clear();sky=n.new('ShaderNodeTexSky');sky.sky_type='NISHITA';sky.sun_elevation=math.radians(35);sky.sun_rotation=math.radians(135);sky.sun_disc=False
bg=n.new('ShaderNodeBackground');bg.inputs['Strength'].default_value=.35;wo=n.new('ShaderNodeOutputWorld');l.new(sky.outputs['Color'],bg.inputs[0]);l.new(bg.outputs[0],wo.inputs[0])
scene.view_settings.exposure=.3;scene.cycles.samples=64;scene.render.resolution_x=1440;scene.render.resolution_y=1000
cam=bpy.data.objects['Overview'];scene.camera=cam
# CPU/GPU preferences are process-local; select Metal again.
prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='METAL';prefs.get_devices()
for device in prefs.devices:device.use=device.type=='METAL'
scene.cycles.device='GPU' if any(d.type=='METAL' for d in prefs.devices) else 'CPU'
(out/'receipt.json').write_text(json.dumps({'status':'illustrative_visual_trial','planting_from_DXF':False,'grass_instances':count,'grass_distribution':'stratified jittered grid','asphalt_noise_scale_per_m':180,'device':scene.cycles.device,'samples':64},indent=2))
for name,pos,target,ortho in [('overview',(-14,-23,28),(22,24,5),49),('street',(33,16,3.3),(24,38,4.4),None)]:
 cam.location=pos;cam.rotation_euler=(Vector(target)-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO' if ortho else 'PERSP'
 if ortho:cam.data.ortho_scale=ortho
 else:cam.data.lens=32
 scene.render.filepath=str(out/(name+'.png'))
 if name=='street':bpy.ops.wm.save_as_mainfile(filepath=str(out/'vegetation-refined.blend'))
 bpy.ops.render.render(write_still=True)
