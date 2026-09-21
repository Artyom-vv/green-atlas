"""Render a clay geometry check from the compact geometry audit packet."""
import json
import math
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".runtime/geometry-audit-20260919"
packet = json.loads((OUT / "geometry-audit.json").read_text())
terrain = packet["terrain"]
origin = Vector((packet["bbox"][0], packet["bbox"][1], 150.0))

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = "BLENDER_EEVEE_NEXT"
scene.render.resolution_x = 1600
scene.render.resolution_y = 1100
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.view_settings.look = "AgX - Medium High Contrast"


def material(name, color, roughness=0.82):
    value = bpy.data.materials.new(name)
    value.diffuse_color = (*color, 1)
    value.use_nodes = True
    shader = value.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    return value


road_mat = material("Verified extent / hypothesised boundary", (0.47, 0.55, 0.55))
curb_mat = material("Source breakline", (0.23, 0.27, 0.27))
tree_mat = material("Tree position", (0.32, 0.38, 0.32))
conflict_mat = material("Conflict", (0.72, 0.25, 0.07))
source_mat = material("Source plan line", (0.18, 0.21, 0.21))

vertices = [tuple(Vector(row["xyz"]) - origin) for row in terrain["vertices"]]
mesh = bpy.data.meshes.new("Road evidence mesh")
mesh.from_pydata(vertices, [], terrain["triangles"])
road = bpy.data.objects.new("Road evidence mesh", mesh)
bpy.context.collection.objects.link(road)
road.data.materials.append(road_mat)
road["status"] = terrain["status"]


def terrain_z(x, y):
    local = Vector((x - origin.x, y - origin.y, 0))
    for face in terrain["triangles"]:
        a, b, c = [Vector(vertices[index]) for index in face]
        denominator = (b.y-c.y)*(a.x-c.x)+(c.x-b.x)*(a.y-c.y)
        u = ((b.y-c.y)*(local.x-c.x)+(c.x-b.x)*(local.y-c.y))/denominator
        v = ((c.y-a.y)*(local.x-c.x)+(a.x-c.x)*(local.y-c.y))/denominator
        if min(u, v, 1-u-v) >= -1e-6:
            return u*a.z + v*b.z + (1-u-v)*c.z
    return None


for index, line in enumerate(packet["source_lines"]):
    points = []
    for x, y in line["xy"]:
        height = terrain_z(x, y) if line["layer"] == "Бортовой камень" else None
        points.append((x-origin.x, y-origin.y, height + 0.03 if height is not None else -0.25))
    if len(points) < 2:
        continue
    curve = bpy.data.curves.new(f"Source curb {index}", "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = 0.035 if line["layer"] == "Бортовой камень" else 0.018
    curve.bevel_resolution = 2
    spline = curve.splines.new("POLY")
    spline.points.add(len(points)-1)
    for target, point in zip(spline.points, points):
        target.co = (*point, 1)
    obj = bpy.data.objects.new(curve.name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(curb_mat if line["layer"] == "Бортовой камень" else source_mat)

for tree in packet["trees"]:
    conflict = tree["number"] in packet["conflicting_tree_numbers"]
    rejected_conflict = tree["number"] in packet["rejected_context_conflicting_tree_numbers"]
    x, y = tree["xy"]
    height = terrain_z(x, y)
    base = height if height is not None else -0.2
    bpy.ops.mesh.primitive_cylinder_add(vertices=20, radius=0.16, depth=1.8,
                                        location=(x-origin.x, y-origin.y, base+0.9))
    trunk = bpy.context.object
    trunk.name = f"{'Conflict' if conflict else 'Tree'} {tree['number']}"
    trunk.data.materials.append(conflict_mat if conflict or rejected_conflict else tree_mat)
    trunk["base_elevation_status"] = "road surface" if height is not None else "unknown, shown on audit datum"
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=0.8,
                                         location=(x-origin.x, y-origin.y, base+2.2))
    bpy.context.object.data.materials.append(conflict_mat if conflict or rejected_conflict else tree_mat)

# Unknown territory is intentionally absent; a thin frame only shows the audit crop.
x0, y0, x1, y1 = packet["bbox"]
frame = [(x0-origin.x, y0-origin.y, -0.03), (x1-origin.x, y0-origin.y, -0.03),
         (x1-origin.x, y1-origin.y, -0.03), (x0-origin.x, y1-origin.y, -0.03)]
curve = bpy.data.curves.new("Audit boundary", "CURVE")
curve.dimensions = "3D"
curve.bevel_depth = 0.025
spline = curve.splines.new("POLY")
spline.points.add(4)
for target, point in zip(spline.points, frame + [frame[0]]):
    target.co = (*point, 1)
obj = bpy.data.objects.new("Audit boundary — unknown inside", curve)
bpy.context.collection.objects.link(obj)
obj.data.materials.append(curb_mat)

world = bpy.data.worlds.new("Neutral diagnostic world")
scene.world = world
world.use_nodes = True
world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.76, 0.8, 0.84, 1)
world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.65
bpy.ops.object.light_add(type="AREA", location=(-8, -10, 25))
bpy.context.object.data.energy = 900
bpy.context.object.data.shape = "DISK"
bpy.context.object.data.size = 15

bpy.ops.object.camera_add(location=(-12, -20, 49))
camera = bpy.context.object
camera.data.type = "ORTHO"
camera.data.ortho_scale = 68
target = Vector((26.5, 21.5, 0.3))
camera.rotation_euler = (target-camera.location).to_track_quat("-Z", "Y").to_euler()
scene.camera = camera
scene.render.filepath = str(OUT / "geometry-clay.png")
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "geometry-clay.blend"))
bpy.ops.render.render(write_still=True)
(OUT / "render-receipt.json").write_text(json.dumps({
    "engine": scene.render.engine,
    "resolution": [scene.render.resolution_x, scene.render.resolution_y],
    "unknown_surface_fill": False,
    "tree_conflicts": packet["conflicting_tree_numbers"],
    "purpose": "geometry-only diagnostic, not a presentation render",
}, ensure_ascii=False, indent=2))
