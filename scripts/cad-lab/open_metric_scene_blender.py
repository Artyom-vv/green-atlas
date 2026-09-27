"""Create an inspectable Blender scene from an assembled metric scene packet.

This is a diagnostic 3D representation. Generic objects and colours communicate
uncertainty; it intentionally does not claim photographic facade or tree shapes.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy


def material(name, color):
    result = bpy.data.materials.new(name)
    result.diffuse_color = (*color, 1.0)
    result.use_nodes = True
    result.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (*color, 1.0)
    return result


def surface_objects(packet, materials):
    grouped = {}
    for row in packet["surfaces"]:
        grouped.setdefault(row["class"], []).extend(row["triangles"])
    for kind, triangles in grouped.items():
        vertices, faces = [], []
        for triangle in triangles:
            start = len(vertices)
            vertices.extend(triangle)
            faces.append((start, start + 1, start + 2))
        if not faces:
            continue
        mesh = bpy.data.meshes.new(f"CAD {kind} mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(f"CAD {kind}", mesh)
        bpy.context.collection.objects.link(obj)
        obj.data.materials.append(materials.get(kind, materials["unknown"]))
        obj["evidence"] = "authored CAD XY; smooth estimated ground Z"


def building_objects(packet, materials):
    for row in packet["buildings"]:
        ring = row["footprint"][:-1]
        if len(ring) < 3:
            continue
        height = row["height_m"]
        base = row["ground_z"]
        vertices = [(x, y, base) for x, y in ring]
        if height is not None:
            vertices += [(x, y, base + height) for x, y in ring]
        count = len(ring)
        faces = [tuple(reversed(range(count)))]
        if height is not None:
            faces.append(tuple(range(count, count * 2)))
            faces.extend((i, (i + 1) % count, (i + 1) % count + count, i + count)
                         for i in range(count))
        mesh = bpy.data.meshes.new(f"building {row['id']}")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        obj = bpy.data.objects.new(f"building {row['id']}", mesh)
        bpy.context.collection.objects.link(obj)
        obj.data.materials.append(materials["building"] if height is not None else materials["unknown"])
        obj["height_status"] = row["height_status"]
        obj["alignment_status"] = row["alignment_status"]
        if row.get("address"):
            obj["address"] = str(row["address"])


def tree_objects(packet, materials):
    # One shared diagnostic crown mesh is instanced, so placement remains
    # inspectable even when no licensed species-specific library is installed.
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1)
    prototype = bpy.context.object
    prototype.name = "tree-crown-proxy-template"
    prototype.data.materials.append(materials["tree"])
    prototype.hide_render = True
    prototype.hide_viewport = True
    for row in packet["trees"]:
        x, y, z = row["xyz"]
        height = float(row["height_m"])
        bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=max(0.08, height * 0.014),
                                            depth=height * 0.55,
                                            location=(x, y, z + height * 0.275))
        trunk = bpy.context.object
        trunk.name = f"tree-trunk {row['id']}"
        trunk.data.materials.append(materials["trunk"])
        crown = bpy.data.objects.new(f"tree-crown {row['id']}", prototype.data)
        bpy.context.collection.objects.link(crown)
        crown.hide_render = False
        crown.hide_viewport = False
        crown.location = (x, y, z + height * 0.75)
        crown.scale = (height * 0.18, height * 0.18, height * 0.25)
        crown["inventory_id"] = row["id"]
        crown["source_species"] = row["species"] or "unknown"
        crown["height_status"] = "inventory_record"


def road_axis_objects(packet, materials):
    for row in packet.get("road_axes", []):
        points = row["xyz"]
        if len(points) < 2:
            continue
        curve = bpy.data.curves.new(f"map road axis {row['id']}", "CURVE")
        curve.dimensions = "3D"
        curve.bevel_depth = 0.035
        path = curve.splines.new("POLY")
        path.points.add(len(points) - 1)
        for item, xyz in zip(path.points, points):
            item.co = (*xyz, 1.0)
        obj = bpy.data.objects.new(f"map road axis {row['id']}", curve)
        bpy.context.collection.objects.link(obj)
        curve.materials.append(materials["road_axis"])
        obj["status"] = row["status"]


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    if len(args) not in (2, 3):
        raise SystemExit("Usage: blender --background --python open_metric_scene_blender.py -- scene.json output.blend [preview.png]")
    packet_path, output = Path(args[0]), Path(args[1])
    packet = json.loads(packet_path.read_text())
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    colors = {"road": (0.27, 0.29, 0.32), "sidewalk": (0.69, 0.69, 0.66),
              "lawn": (0.31, 0.54, 0.25), "marking": (0.91, 0.89, 0.78),
              "special_surface": (0.61, 0.57, 0.51), "unknown": (0.78, 0.23, 0.53),
              "building": (0.72, 0.73, 0.72), "tree": (0.13, 0.38, 0.17),
              "trunk": (0.32, 0.22, 0.14), "road_axis": (0.72, 0.51, 0.12)}
    materials = {key: material(key, color) for key, color in colors.items()}
    surface_objects(packet, materials)
    building_objects(packet, materials)
    tree_objects(packet, materials)
    road_axis_objects(packet, materials)
    # Two camera presets are views into one invariant scene. The local view
    # exposes volume; the top view helps inspect source coverage.
    min_x, min_y, max_x, max_y = packet["scope_bounds_local_m"]
    span = max(max_x - min_x, max_y - min_y)
    bpy.ops.object.camera_add(location=(0, 0, span))
    overview = bpy.context.object
    overview.name = "Full top view"
    overview.rotation_euler = (0, 0, 0)
    overview.data.type = "ORTHO"
    overview.data.ortho_scale = span * 1.15
    detail_span = max(120, min(span * 0.38, 350))
    bpy.ops.object.camera_add(location=(0, -detail_span * 0.5, detail_span * 0.6))
    camera = bpy.context.object
    camera.name = "Local oblique view"
    direction = -camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = detail_span
    bpy.context.scene.camera = camera
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.render.resolution_x = 1280
    bpy.context.scene.render.resolution_y = 720
    bpy.context.scene.render.resolution_percentage = 100
    world = bpy.context.scene.world
    world.use_nodes = True
    world.node_tree.nodes.get("Background").inputs["Color"].default_value = (0.62, 0.68, 0.75, 1)
    world.node_tree.nodes.get("Background").inputs["Strength"].default_value = 0.45
    bpy.context.scene.view_settings.view_transform = "AgX"
    bpy.context.scene.view_settings.look = "AgX - Medium High Contrast"
    bpy.context.scene.view_settings.exposure = -0.7
    bpy.ops.object.light_add(type="SUN", location=(20, -30, 100))
    bpy.context.object.data.energy = 2.0
    bpy.context.object.rotation_euler = (math.radians(30), math.radians(-15), math.radians(45))
    bpy.context.scene["scene_packet"] = str(packet_path)
    bpy.context.scene["scene_status"] = packet["status"]
    bpy.context.preferences.filepaths.save_version = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    if len(args) == 3:
        bpy.context.scene.render.engine = "CYCLES"
        bpy.context.scene.cycles.samples = 8
        bpy.context.scene.render.resolution_percentage = 50
        bpy.context.scene.render.filepath = str(Path(args[2]))
        bpy.ops.render.render(write_still=True)
    print(json.dumps({"blend": str(output), "buildings": len(packet["buildings"]),
                      "trees": len(packet["trees"]), "surfaces": len(packet["surfaces"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
