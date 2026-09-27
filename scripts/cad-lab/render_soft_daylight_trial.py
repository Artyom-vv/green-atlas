"""Neutral daylight/material study; same geometry and camera as the prior trial."""
import bpy, math, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
out=ROOT/'.runtime/soft-daylight-trial-20260919';out.mkdir(exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'.runtime/pbr-surface-trial-20260919/pbr-trial.blend'),use_scripts=False)
def surface(name, low, high, grain, depth, rough):
 m=bpy.data.materials.new(name);m.use_nodes=True
 n=m.node_tree.nodes;l=m.node_tree.links;n.clear()
 bs=n.new('ShaderNodeBsdfPrincipled');output=n.new('ShaderNodeOutputMaterial');l.new(bs.outputs[0],output.inputs[0]);bs.inputs['Roughness'].default_value=rough
 coord=n.new('ShaderNodeTexCoord')
 fine=n.new('ShaderNodeTexNoise');fine.inputs['Scale'].default_value=grain;fine.inputs['Detail'].default_value=2
 l.new(coord.outputs['Object'],fine.inputs['Vector'])
 ramp=n.new('ShaderNodeValToRGB');ramp.color_ramp.elements[0].color=(*low,1);ramp.color_ramp.elements[1].color=(*high,1)
 l.new(fine.outputs['Fac'],ramp.inputs[0]);l.new(ramp.outputs[0],bs.inputs['Base Color'])
 bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.22;bump.inputs['Distance'].default_value=depth
 l.new(fine.outputs['Fac'],bump.inputs['Height']);l.new(bump.outputs[0],bs.inputs['Normal'])
 return m
asphalt=surface('Fine asphalt — metre coordinates',(.065,.07,.074),(.095,.10,.105),210,.00065,.86)
concrete=surface('Quiet cast concrete — metre coordinates',(.36,.37,.37),(.43,.44,.44),330,.00025,.8)
for ob in bpy.data.objects:
 if ob.type!='MESH':continue
 if ob.name.startswith('Road —') or ob.name.startswith('Curb '):
  ob.data.materials.clear();ob.data.materials.append(asphalt if ob.name.startswith('Road —') else concrete)
for ob in bpy.data.objects:
 if ob.type=='LIGHT' and ob.data.type=='SUN':
  ob.data.energy=2;ob.data.angle=math.radians(12);ob.data.color=(1,.98,.94)
scene=bpy.context.scene
# Broad neutral sky fill reveals leaf surfaces and retains soft contact shadows.
n=scene.world.node_tree.nodes;l=scene.world.node_tree.links;n.clear()
bg=n.new('ShaderNodeBackground');bg.inputs['Color'].default_value=(.78,.84,.92,1);bg.inputs['Strength'].default_value=2
wo=n.new('ShaderNodeOutputWorld');l.new(bg.outputs[0],wo.inputs[0])
scene.view_settings.view_transform='AgX';scene.view_settings.look='AgX - Base Contrast';scene.view_settings.exposure=1.0
scene.cycles.samples=128;scene.cycles.use_denoising=True
scene.cycles.max_bounces=12;scene.cycles.diffuse_bounces=6;scene.cycles.transparent_max_bounces=16
prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='METAL';prefs.get_devices()
for d in prefs.devices:d.use=d.type=='METAL'
scene.cycles.device='GPU';scene.render.resolution_x=1440;scene.render.resolution_y=1000
scene.render.filepath=str(out/'street.png');bpy.ops.wm.save_as_mainfile(filepath=str(out/'soft-daylight.blend'));bpy.ops.render.render(write_still=True)
(out/'receipt.json').write_text(json.dumps({'purpose':'same-camera material/light correction','materials':'procedural fine grain in object metres; no tiled scan albedo','asphalt_bump_distance_m':.00065,'concrete_bump_distance_m':.00025,'look':scene.view_settings.look,'samples':128,'planting_from_DXF':False,'urban_context_complete':False},indent=2))
