"""Bake reviewed Poly Haven plants to transparent, whole-tree impostors.

Run with Blender, not CPython:
  Blender --background --python blender-bake-plant-impostors.py -- input.gltf node output_dir
"""
import bpy
import math
import os
import sys
from mathutils import Vector

source_path, source_node, output_dir = sys.argv[sys.argv.index("--") + 1:]
os.makedirs(output_dir, exist_ok=True)

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.import_scene.gltf(filepath=os.path.abspath(source_path))

if source_node == "-":
    targets = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
else:
    target = bpy.data.objects.get(source_node)
    if target is None:
        raise RuntimeError(f"Missing reviewed source node: {source_node}")
    targets = [target, *list(target.children_recursive)]
    for obj in list(bpy.context.scene.objects):
        if obj not in targets:
            bpy.data.objects.remove(obj, do_unlink=True)
if not targets:
    raise RuntimeError("Prepared asset contains no mesh objects")

corners = [obj.matrix_world @ Vector(corner) for obj in targets for corner in obj.bound_box]
minimum = Vector((min(p.x for p in corners), min(p.y for p in corners), min(p.z for p in corners)))
maximum = Vector((max(p.x for p in corners), max(p.y for p in corners), max(p.z for p in corners)))
center = (minimum + maximum) * 0.5
extent = maximum - minimum

scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.render.resolution_x = 512
scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.film_transparent = True
scene.render.image_settings.color_depth = "8"
scene.view_settings.look = "AgX - Medium High Contrast"
scene.view_settings.exposure = 2.0
scene.world.color = (0.055, 0.07, 0.05)

camera_data = bpy.data.cameras.new("Impostor Camera")
camera = bpy.data.objects.new("Impostor Camera", camera_data)
scene.collection.objects.link(camera)
scene.camera = camera
camera_data.type = "ORTHO"
camera_data.ortho_scale = max(extent.z, extent.x, extent.y) * 1.12

def point_at(obj, point):
    obj.rotation_euler = (Vector(point) - obj.location).to_track_quat("-Z", "Y").to_euler()

key_data = bpy.data.lights.new("Key", "AREA")
key_data.energy = 3200
key_data.shape = "DISK"
key_data.size = max(extent) * 1.5
key = bpy.data.objects.new("Key", key_data)
scene.collection.objects.link(key)
key.location = center + Vector((max(extent) * 1.4, -max(extent) * 1.6, max(extent) * 1.8))
point_at(key, center)

fill_data = bpy.data.lights.new("Fill", "AREA")
fill_data.energy = 1800
fill_data.size = max(extent) * 1.7
fill = bpy.data.objects.new("Fill", fill_data)
scene.collection.objects.link(fill)
fill.location = center + Vector((-max(extent) * 1.1, max(extent) * 1.2, max(extent) * 0.9))
point_at(fill, center)

# glTF is Y-up but Blender imports it Z-up. Four views avoid the unmistakable
# flat wall from a raw atlas or a single billboard.
for angle in (0, 45, 90, 135):
    radians = math.radians(angle)
    radius = max(extent) * 3
    camera.location = center + Vector((math.sin(radians) * radius, -math.cos(radians) * radius, extent.z * 0.03))
    point_at(camera, center)
    scene.render.filepath = os.path.join(output_dir, f"view-{angle}.png")
    bpy.ops.render.render(write_still=True)
