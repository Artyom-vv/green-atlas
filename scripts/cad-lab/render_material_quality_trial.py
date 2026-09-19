"""Texture/foliage quality trial with explicit close-up and supersampled delivery.
Geometry/planting remains the illustrative road test, not an approved DXF design.
"""
import bpy, math, json
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2]
out=ROOT/'.runtime/material-quality-trial-20260919';out.mkdir(exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'.runtime/pbr-surface-trial-20260919/pbr-trial.blend'),use_scripts=False)
# Keep original photographic detail, temper scan coloration and normal amplitude.
for name,base,strength in [('Scanned asphalt01',(.08,.085,.09,1),.32),('Scanned concrete',(.4,.41,.4,1),.18)]:
 m=bpy.data.materials[name];n=m.node_tree.nodes;l=m.node_tree.links
 bs=next(x for x in n if x.type=='BSDF_PRINCIPLED')
 tex=next(x for x in n if x.type=='TEX_IMAGE' and 'Diffuse' in x.image.name)
 hsv=n.new('ShaderNodeHueSaturation');hsv.inputs['Saturation'].default_value=.12;l.new(tex.outputs['Color'],hsv.inputs['Color'])
 mix=n.new('ShaderNodeMixRGB');mix.blend_type='MIX';mix.inputs[0].default_value=.35;mix.inputs[2].default_value=base
 l.new(hsv.outputs[0],mix.inputs[1]);l.new(mix.outputs[0],bs.inputs['Base Color'])
 for x in n:
  if x.type=='NORMAL_MAP':x.inputs['Strength'].default_value=strength
 # Smooth spatial UV distortion breaks the regular repetition without sharp seams.
 uv=n.new('ShaderNodeTexCoord');noise=n.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=.45;noise.inputs['Detail'].default_value=2
 l.new(uv.outputs['UV'],noise.inputs['Vector'])
 scale=n.new('ShaderNodeVectorMath');scale.operation='SCALE';scale.inputs[3].default_value=.32;l.new(noise.outputs['Color'],scale.inputs[0])
 add=n.new('ShaderNodeVectorMath');add.operation='ADD';l.new(uv.outputs['UV'],add.inputs[0]);l.new(scale.outputs[0],add.inputs[1])
 for x in list(n):
  if x.type=='TEX_IMAGE':l.new(add.outputs[0],x.inputs['Vector'])
# Leaf detail must not become a field of tiny specular flashes.
for m in bpy.data.materials:
 if not m.use_nodes:continue
 for n in m.node_tree.nodes:
  if n.type=='GROUP' and m.name.startswith('bq_Leaf_'):
   for key,value in [('Normal Strength',.22),('Bump Strength',.1),('Roughness',.64),('Specular',.28)]:
    if key in n.inputs:n.inputs[key].default_value=value
scene=bpy.context.scene
for ob in bpy.data.objects:
 if ob.type=='LIGHT' and ob.data.type=='SUN':
  ob.rotation_euler=(math.radians(35),math.radians(-25),math.radians(-150));ob.data.energy=2;ob.data.angle=math.radians(3);ob.data.color=(1,.96,.9)
n=scene.world.node_tree.nodes;l=scene.world.node_tree.links;n.clear()
bg=n.new('ShaderNodeBackground');bg.inputs[0].default_value=(.74,.82,1,1);bg.inputs[1].default_value=.85
wo=n.new('ShaderNodeOutputWorld');l.new(bg.outputs[0],wo.inputs[0])
scene.view_settings.look='AgX - Medium High Contrast';scene.view_settings.exposure=.8
scene.cycles.samples=512;scene.cycles.adaptive_threshold=.003;scene.cycles.adaptive_min_samples=64
scene.cycles.use_denoising=True;scene.cycles.denoising_input_passes='RGB_ALBEDO_NORMAL'
scene.cycles.transparent_max_bounces=32;scene.cycles.max_bounces=12
scene.cycles.pixel_filter_type='BLACKMAN_HARRIS';scene.cycles.filter_width=1.5
prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='METAL';prefs.get_devices()
for d in prefs.devices:d.use=d.type=='METAL'
scene.cycles.device='GPU';scene.render.resolution_percentage=100
scene.render.resolution_x=2880;scene.render.resolution_y=2000
scene.render.filepath=str(out/'street-2880.png')
bpy.ops.wm.save_as_mainfile(filepath=str(out/'material-quality.blend'))
bpy.ops.render.render(write_still=True)
# Standalone close-up: actual shrub, retaining its original geometry and placement.
shrubs=[o for o in scene.objects if o.instance_type=='COLLECTION' and o.instance_collection and o.instance_collection.name=='Asset shrub']
cam=scene.camera;shrub=min(shrubs,key=lambda o:(o.location-cam.location).length)
target=shrub.location+Vector((0,0,.85));direction=(cam.location-shrub.location);direction.z=0;direction.normalize()
cam.location=target+direction*3.3+Vector((0,0,.1));cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.lens=50
scene.render.resolution_x=1600;scene.render.resolution_y=1600;scene.render.filepath=str(out/'shrub-detail.png');bpy.ops.render.render(write_still=True)
(out/'receipt.json').write_text(json.dumps({'samples':512,'adaptive_threshold':.003,'street_resolution':[2880,2000],'leaf_normal_strength':.22,'leaf_roughness':.64,'planting_from_DXF':False,'shrub':shrub.name,'shrub_source_contains_bark_and_leaves':True,'materials':'existing Poly Haven maps with restrained color and continuous UV distortion','limitations':['illustrative planting','incomplete urban context','no added shrub branch geometry']},indent=2))
