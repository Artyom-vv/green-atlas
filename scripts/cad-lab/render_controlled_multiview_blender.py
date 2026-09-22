import bpy,json,hashlib,sys,math,argparse
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description='Render a frozen Blender scene with exclusive visible semantic masks.')
parser.add_argument('--source-blend',type=Path,required=True)
parser.add_argument('--context-lawn',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--samples',type=int,default=96)
parser.add_argument('--cameras',type=Path,required=True)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
OUT=args.output.resolve();OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'scripts/cad-lab'))
import render_nspd_scene_blender as renderer
source=args.source_blend.resolve()
bpy.ops.wm.open_mainfile(filepath=str(source));s=bpy.context.scene
for o in list(bpy.data.objects):
 if o.get('semantic') in {'road_marking','sky_preset'}:bpy.data.objects.remove(o,do_unlink=True)
for row in json.loads(args.context_lawn.read_text()):
 lawn=bpy.data.objects['project_lawn'].data.materials[0]
 renderer.mesh_from_triangles(row['id'].replace(':','_'),row['triangles'],lawn,{'semantic':'context_lawn','source_id':row['id'],'geometry_status':'osm_cartographic_grass_outside_project_and_buildings'})
# Neutral unknown appearance must not imply a vegetation classification.
unknown=bpy.data.objects.get('Context_grade')
unknown['semantic']='unknown'
unknown.data.materials.clear();unknown.data.materials.append(renderer.simple_material('Unknown ground neutral proxy',(.22,.22,.21),.95))
wn=s.world.node_tree.nodes;wl=s.world.node_tree.links;wn.clear()
bg=wn.new('ShaderNodeBackground');out=wn.new('ShaderNodeOutputWorld')
bg.inputs['Color'].default_value=(.83,.90,1,1);bg.inputs['Strength'].default_value=1.0
wl.new(bg.outputs['Background'],out.inputs['Surface'])
for o in s.objects:
 if o.type=='LIGHT' and o.data.type=='SUN':
  o.data.energy=2.0;o.data.angle=math.radians(2.5);o.rotation_euler=(math.radians(38),0,math.radians(215))
leaf_tuning=[]
for mat in bpy.data.materials:
 if not mat.use_nodes or not mat.name.startswith('bq_Leaf_'):continue
 for node in mat.node_tree.nodes:
  if node.type!='GROUP':continue
  changes={}
  for key,value in {'Value':1.22,'Roughness':.62,'Normal Strength':.25,'Bump Strength':.18,'Translucency Factor':.6}.items():
   socket=node.inputs.get(key)
   if socket is not None and not socket.is_linked:
    changes[key]={'before':float(socket.default_value),'after':value};socket.default_value=value
  if changes:leaf_tuning.append({'material':mat.name,'changes':changes})
s.view_settings.look='AgX - Medium High Contrast';s.view_settings.exposure=.45
s.render.resolution_x=1280;s.render.resolution_y=720;s.render.resolution_percentage=100
s.render.image_settings.file_format='PNG';s.cycles.samples=args.samples;s.cycles.adaptive_threshold=.012;s.cycles.seed=271828;s.cycles.use_denoising=True
classes={'road':1,'sidewalk':2,'lawn':3,'curb':4,'building':5,'vegetation':6,'furniture':7,'unknown':8,'sky':9,'marking':10,'context_lawn':11}
alias={'lawn_detail_proxy':'lawn','building_detail_proxy':'building','street_furniture':'furniture','barrier':'furniture','sky_preset':'sky'}
for o in bpy.data.objects:
 if o.type=='MESH':
  semantic=alias.get(o.get('semantic'),o.get('semantic','unknown'));o.pass_index=classes.get(semantic,8)
# IndexOB follows visibility/occlusion and original leaf alpha. No material overrides.
s.view_layers[0].use_pass_object_index=True
s.use_nodes=True;nodes=s.node_tree.nodes;links=s.node_tree.links;nodes.clear()
rl=nodes.new('CompositorNodeRLayers');composite=nodes.new('CompositorNodeComposite');links.new(rl.outputs['Image'],composite.inputs[0])
files=nodes.new('CompositorNodeOutputFile');files.base_path=str(OUT);files.format.file_format='PNG';files.format.color_mode='BW';files.format.color_depth='8';files.file_slots.clear()
if hasattr(files,'save_as_render'):files.save_as_render=False
for name,index in classes.items():
 mask=nodes.new('CompositorNodeIDMask');mask.index=index;mask.use_antialiasing=False;links.new(rl.outputs['IndexOB'],mask.inputs[0])
 sock=files.file_slots.new(name);links.new(mask.outputs[0],files.inputs[name])
# Sky includes uncovered world pixels (object index zero).
zero=nodes.new('CompositorNodeIDMask');zero.index=0;zero.use_antialiasing=False;links.new(rl.outputs['IndexOB'],zero.inputs[0])
sky_input=files.inputs['sky'];existing=sky_input.links[0].from_socket;links.remove(sky_input.links[0]);maximum=nodes.new('CompositorNodeMath');maximum.operation='MAXIMUM';links.new(existing,maximum.inputs[0]);links.new(zero.outputs[0],maximum.inputs[1]);links.new(maximum.outputs[0],sky_input)
views=json.loads(args.cameras.read_text())
state=[{'name':o.name,'semantic':o.get('semantic'),'matrix':[list(r) for r in o.matrix_world]} for o in s.objects if o.type!='CAMERA']
identity=hashlib.sha256(json.dumps(state,sort_keys=True).encode()).hexdigest()
receipts=[]
for name,pos,target,lens in views:
 d=OUT/name;d.mkdir(exist_ok=True)
 s.camera.location=pos;s.camera.data.lens=lens;renderer.look_at(s.camera,target)
 sky=renderer.add_sky_backdrop(pos,target,renderer.sky_backdrop_material('native_v1'));sky.pass_index=9;sky.visible_shadow=False;sky.visible_diffuse=False;sky.visible_glossy=False
 for class_name,slot in zip(classes,files.file_slots):slot.path=name+'/mask-'+class_name+'-'
 s.render.filepath=str(d/'native-v2.png');bpy.ops.render.render(write_still=True)
 receipts.append({'view':name,'scene_object_transform_hash':identity,'camera':{'position':pos,'target':target,'lens':lens},'image_sha256':hashlib.sha256((d/'native-v2.png').read_bytes()).hexdigest()})
 (OUT/'receipt.json').write_text(json.dumps({'status':'native_stability_candidate','neural_finish':False,'seed':271828,'appearance':{'world_strength':1.0,'world_color':[.83,.90,1],'sun_energy':2.0,'sun_angle_degrees':2.5,'exposure':.45,'look':'AgX - Medium High Contrast','leaf_tuning':leaf_tuning},'classes':classes,'source_blend':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'views':receipts,'mask_method':'Cycles IndexOB, occlusion and original material alpha, exclusive hard masks','known_limits':['Unknown surfaces are explicitly unknown; no inferred grass fill','Grass mesh detail has legacy 85m radius; lawn surface masks remain full extent','Generic facade geometry remains an appearance proxy']},indent=2))
 bpy.data.objects.remove(sky,do_unlink=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'stable-shared-scene.blend'))
