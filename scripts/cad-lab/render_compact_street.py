"""Render one compact street frame on the source-derived curb corridor."""
import bpy
import json
import math
import os
import random
from pathlib import Path

from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".runtime/compact-street-render-20260919"
DATA = json.loads((OUT / "elevated-corridor.json").read_text())
ORIGIN = Vector(DATA["origin"])

bpy.ops.wm.open_mainfile(
    filepath=str(ROOT / ".runtime/material-quality-trial-20260919/material-quality.blend"),
    use_scripts=False,
)
scene = bpy.context.scene
for obj in list(scene.objects):
    if obj.type not in {"CAMERA", "LIGHT"}:
        bpy.data.objects.remove(obj, do_unlink=True)


def mesh_object(name, vertices, faces, material):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    return obj


def quiet_material(name, low, high, scale, roughness, bump_distance):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Roughness"].default_value = roughness
    coordinate = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 2
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*low, 1)
    ramp.color_ramp.elements[1].color = (*high, 1)
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.15
    bump.inputs["Distance"].default_value = bump_distance
    links.new(coordinate.outputs["Object"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs[0])
    links.new(ramp.outputs[0], shader.inputs["Base Color"])
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs[0], shader.inputs["Normal"])
    links.new(shader.outputs[0], output.inputs[0])
    return material


asphalt = bpy.data.materials["Scanned asphalt01"]
concrete = quiet_material("Calm concrete", (0.38, 0.39, 0.39), (0.44, 0.45, 0.45), 55, 0.83, 0.0006)
soil = quiet_material("Grass substrate", (0.025, 0.04, 0.012), (0.04, 0.065, 0.018), 3, 0.92, 0.003)

road_vertices = [tuple(Vector(vertex)-ORIGIN) for vertex in DATA["road_mesh"]["vertices"]]
road = mesh_object("Source-derived road corridor", road_vertices, DATA["road_mesh"]["triangles"], asphalt)
road["height_status"] = DATA["height_method"]["status"]
uv = road.data.uv_layers.new(name="Metric UV")
for face in road.data.polygons:
    for loop_index in face.loop_indices:
        point = road.data.vertices[road.data.loops[loop_index].vertex_index].co
        uv.data[loop_index].uv = (point.x/2.1, point.y/2.1)


def outward_normals(samples):
    values = []
    center = sum((Vector((sample["xy"][0]-ORIGIN.x, sample["xy"][1]-ORIGIN.y, 0)) for sample in samples), Vector())/len(samples)
    road_center = sum((Vector(v) for v in road_vertices), Vector())/len(road_vertices)
    for index, sample in enumerate(samples):
        previous = samples[max(0, index-1)]["xy"]
        following = samples[min(len(samples)-1, index+1)]["xy"]
        tangent = Vector((following[0]-previous[0], following[1]-previous[1], 0)).normalized()
        normal = Vector((-tangent.y, tangent.x, 0))
        point = Vector((sample["xy"][0]-ORIGIN.x, sample["xy"][1]-ORIGIN.y, 0))
        if normal.dot(road_center-point) > 0:
            normal = -normal
        values.append(normal)
    return values


verge_records = []
for chain_index, chain in enumerate(DATA["chains"]):
    samples = chain["samples"]
    normals = outward_normals(samples)
    curb_vertices = []
    verge_vertices = []
    curb_faces = []
    verge_faces = []
    for index, (sample, normal) in enumerate(zip(samples, normals)):
        x, y = sample["xy"]
        local = Vector((x-ORIGIN.x, y-ORIGIN.y, 0))
        road_z = sample["road_z"]-ORIGIN.z
        top_z = sample["curb_top_z"]-ORIGIN.z
        outer = local + normal*0.18
        far = local + normal*6.0
        curb_vertices.extend([
            (local.x, local.y, road_z), (local.x, local.y, top_z),
            (outer.x, outer.y, top_z),
        ])
        verge_vertices.extend([(outer.x, outer.y, top_z), (far.x, far.y, top_z+0.02)])
        if index:
            base = index*3
            curb_faces.extend([
                (base-3, base, base+1, base-2),
                (base-2, base+1, base+2, base-1),
            ])
            verge_base = index*2
            verge_faces.append((verge_base-2, verge_base, verge_base+1, verge_base-1))
    curb = mesh_object(f"Curb {chain_index}", curb_vertices, curb_faces, concrete)
    verge = mesh_object(f"Six metre render verge {chain_index}", verge_vertices, verge_faces, soil)
    curb["geometry_status"] = "source curb XY; paired-height visual interpolation"
    verge["geometry_status"] = "render-only six metre strip, not a recovered lawn boundary"
    verge_records.append((samples, normals))

# Load the source maple once and instance it at two inventory positions close to the corridor.
assets = ROOT / ".runtime/botaniq-68-inspection/selected/botaniq_full"
maple_path = assets / "blends_280/deciduous/Tree_Acer-pseudoplatanus_A_summer.blend"
with bpy.data.libraries.load(str(maple_path), link=False) as (available, requested):
    requested.objects = available.objects
maple_collection = bpy.data.collections.new("Site maple source")
for obj in requested.objects:
    if obj:
        maple_collection.objects.link(obj)
maple_height = max(obj.dimensions.z for obj in requested.objects if obj and obj.type == "MESH")
textures = {path.name: path for path in (assets / "textures").rglob("*") if path.is_file()}
for image in bpy.data.images:
    if image.source == "FILE" and not image.packed_file:
        filename = Path(image.filepath.replace("\\", "/")).name
        if filename in textures:
            image.filepath = str(textures[filename])
            image.reload()
for material in bpy.data.materials:
    if material.use_nodes and material.name.startswith("bq_Leaf_"):
        for node in material.node_tree.nodes:
            if node.type == "GROUP":
                for key, value in [("Normal Strength", .22), ("Bump Strength", .1), ("Roughness", .64), ("Specular", .28)]:
                    if key in node.inputs:
                        node.inputs[key].default_value = value


def closest_verge_base(x, y):
    best = None
    for samples, normals in verge_records:
        for sample, normal in zip(samples, normals):
            distance = math.dist((x, y), sample["xy"])
            if best is None or distance < best[0]:
                best = (distance, sample["curb_top_z"]-ORIGIN.z, normal)
    return best


tree_source = json.loads((ROOT / ".runtime/curb-corridor-audit-20260919/curb-corridor.json").read_text())
shown_trees = []
for tree in tree_source["tree_positions"]:
    distance, base_z, _normal = closest_verge_base(*tree["xy"])
    if tree["species"] != "Клен" or distance > 6.1:
        continue
    instance = bpy.data.objects.new(f"Inventory {tree['number']} {tree['species']}", None)
    instance.instance_type = "COLLECTION"
    instance.instance_collection = maple_collection
    bpy.context.collection.objects.link(instance)
    instance.location = (tree["xy"][0]-ORIGIN.x, tree["xy"][1]-ORIGIN.y, base_z)
    scale = tree["height_m"]/maple_height
    instance.scale = (scale, scale, scale)
    instance.rotation_euler.z = (tree["number"] % 17)/17*math.tau
    instance["inventory_number"] = tree["number"]
    instance["base_elevation_status"] = "nearest curb-top visual continuation"
    shown_trees.append(tree["number"])

# Short, dense grass only on the two narrow render strips.
grass_collection = bpy.data.collections.get("Asset grass")
rng = random.Random(20260919)
grass_count = 0
if grass_collection:
    for samples, normals in verge_records:
        distances = [0.0]
        for first, second in zip(samples, samples[1:]):
            distances.append(distances[-1] + math.dist(first["xy"], second["xy"]))
        length = distances[-1]
        step = .20
        for station_index in range(int(length/step)+1):
            for offset_index in range(28):
                station = min(length, (station_index+rng.uniform(.08, .92))*step)
                segment = next((i for i in range(len(distances)-1) if distances[i] <= station <= distances[i+1]), len(distances)-2)
                span = max(distances[segment+1]-distances[segment], 1e-6)
                ratio = (station-distances[segment])/span
                a, b = samples[segment], samples[segment+1]
                point = Vector((a["xy"][0], a["xy"][1], a["curb_top_z"])).lerp(
                    Vector((b["xy"][0], b["xy"][1], b["curb_top_z"])), ratio
                )
                normal = normals[segment].lerp(normals[segment+1], ratio).normalized()
                offset = .20 + (offset_index+rng.uniform(.08, .92))*.20
                position = point + normal*offset - ORIGIN
                instance = bpy.data.objects.new("Short grass", None)
                instance.instance_type = "COLLECTION"
                instance.instance_collection = grass_collection
                bpy.context.collection.objects.link(instance)
                instance.location = position
                scale = .45+rng.random()*.07
                instance.scale = (scale, scale, scale*.78)
                instance.rotation_euler.z = rng.random()*math.tau
                grass_count += 1

for light in [obj for obj in scene.objects if obj.type == "LIGHT"]:
    if light.data.type == "SUN":
        light.data.energy = 1.7
        light.data.angle = math.radians(5)
        light.rotation_euler = (math.radians(38), math.radians(-18), math.radians(-140))
scene.view_settings.look = "AgX - Medium High Contrast"
scene.view_settings.exposure = .65
scene.cycles.samples = int(os.environ.get("COMPACT_SAMPLES", "256"))
scene.cycles.adaptive_threshold = .004
scene.cycles.adaptive_min_samples = 48
scene.cycles.use_denoising = True
scene.cycles.transparent_max_bounces = 32
preferences = bpy.context.preferences.addons["cycles"].preferences
preferences.compute_device_type = "METAL"
preferences.get_devices()
for device in preferences.devices:
    device.use = device.type == "METAL"
scene.cycles.device = "GPU"
scene.render.resolution_x = 1920
scene.render.resolution_y = 1280
scene.render.resolution_percentage = 100

camera = scene.camera
camera.data.type = "PERSP"
camera.data.lens = 55
camera.location = (44, 14, 5.5)
target = Vector((24, 25, .55))
camera.rotation_euler = (target-camera.location).to_track_quat("-Z", "Y").to_euler()
scene.render.filepath = str(OUT / "compact-street.png")
bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "compact-street.blend"))
bpy.ops.render.render(write_still=True)
(OUT / "render-receipt.json").write_text(json.dumps({
    "resolution": [1920, 1280],
    "samples": 256,
    "road_area_m2": DATA["corridor_area_m2"],
    "trees_shown": shown_trees,
    "grass_instances": grass_count,
    "unknown_territory_fill": False,
    "verge_status": "two six-metre render-only strips outside source curb chains",
    "height_status": DATA["height_method"]["status"],
}, ensure_ascii=False, indent=2))
