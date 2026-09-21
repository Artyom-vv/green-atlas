"""PBR correction trial; replaces flat procedural surfacing, preserves camera."""
import bpy,math,json
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2];out=ROOT/'.runtime/pbr-surface-trial-20260919';out.mkdir(exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'.runtime/vegetation-refined-20260919/vegetation-refined.blend'),use_scripts=False)
texroot=ROOT/'.runtime/cad-vegetation-20260919/pbr'
def pbr(name):
 m=bpy.data.materials.new('Scanned '+name);m.use_nodes=True;n=m.node_tree.nodes;l=m.node_tree.links;n.clear()
 bs=n.new('ShaderNodeBsdfPrincipled');o=n.new('ShaderNodeOutputMaterial');l.new(bs.outputs[0],o.inputs[0])
 for role,socket in [('Diffuse','Base Color'),('Rough','Roughness'),('nor_gl','Normal')]:
  tex=n.new('ShaderNodeTexImage');tex.image=bpy.data.images.load(str(texroot/(name+'_'+role+'.jpg')),check_existing=True)
  if role!='Diffuse':tex.image.colorspace_settings.name='Non-Color'
  if role=='nor_gl':
   normal=n.new('ShaderNodeNormalMap');normal.inputs['Strength'].default_value=.7;l.new(tex.outputs['Color'],normal.inputs['Color']);l.new(normal.outputs[0],bs.inputs[socket])
  else:l.new(tex.outputs['Color'],bs.inputs[socket])
 return m
asphalt=pbr('asphalt01');concrete=pbr('concrete')
for ob in bpy.data.objects:
 if ob.type!='MESH':continue
 isroad=ob.name.startswith('Road —');iscurb=ob.name.startswith('Curb ')
 if not(isroad or iscurb):continue
 ob.data.materials.clear();ob.data.materials.append(asphalt if isroad else concrete)
 uv=ob.data.uv_layers.new(name='MetricUV')
 # Road tile spans 2m; curb uses tangent/height projection on vertical face.
 tangent=(ob.data.vertices[1].co-ob.data.vertices[0].co).normalized() if iscurb else Vector((1,0,0))
 normal=Vector((-tangent.y,tangent.x,0))
 for face in ob.data.polygons:
  for li in face.loop_indices:
   p=ob.data.vertices[ob.data.loops[li].vertex_index].co
   if isroad:coord=(p.x/2.1,p.y/2.1)
   elif ob.name.startswith('Curb face'):coord=(p.dot(tangent)/.5,p.z/.5)
   else:coord=(p.dot(tangent)/.5,p.dot(normal)/.5)
   uv.data[li].uv=coord
# Directional daylight, remove studio softbox that washed out the material.
for ob in list(bpy.data.objects):
 if ob.type=='LIGHT' and ob.data.type=='AREA':bpy.data.objects.remove(ob,do_unlink=True)
for ob in bpy.data.objects:
 if ob.type=='LIGHT' and ob.data.type=='SUN':
  ob.rotation_euler=(math.radians(48),math.radians(-22),math.radians(-55));ob.data.energy=3;ob.data.angle=math.radians(1.2)
for n in bpy.context.scene.world.node_tree.nodes:
 if n.type=='BACKGROUND':n.inputs['Strength'].default_value=.28
scene=bpy.context.scene;scene.view_settings.exposure=.25;scene.cycles.samples=96;scene.cycles.transparent_max_bounces=12
prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='METAL';prefs.get_devices()
for device in prefs.devices:device.use=device.type=='METAL'
scene.cycles.device='GPU';scene.render.resolution_x=1440;scene.render.resolution_y=1000
cam=scene.camera
for name in ['street','detail']:
 if name=='detail':
  cam.location=(34,23,2.35);cam.rotation_euler=(Vector((30,34,1.6))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.lens=42
 scene.render.filepath=str(out/(name+'.png'))
 if name=='street':bpy.ops.wm.save_as_mainfile(filepath=str(out/'pbr-trial.blend'))
 bpy.ops.render.render(write_still=True)
(out/'receipt.json').write_text(json.dumps({'materials':['https://polyhaven.com/a/asphalt_01','https://polyhaven.com/a/concrete_floor_02'],'maps':['Diffuse','Rough','nor_gl'],'resolution':'2k','tile_width_m':{'asphalt':2.1,'concrete':.5},'tile_scale_status':'visual_trial_assumption','planting_from_DXF':False,'samples':96,'device':'Metal'},indent=2))
