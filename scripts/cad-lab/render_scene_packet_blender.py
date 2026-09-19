"""Build and render a deterministic Blender scene from a scene packet.

Run with Blender, for example:

  Blender --background --python scripts/cad-lab/render_scene_packet_blender.py -- \
    .runtime/deterministic-render-pipeline-20260920/scene-packet.json

The beauty scene contains only fragments with an available height surface.
Plan-only geometry is used exclusively for orthographic semantic diagnostics.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import bpy
from mathutils import Vector


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def argv_after_separator() -> list[str]:
    return sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for datablocks in (bpy.data.meshes, bpy.data.curves, bpy.data.materials,
                       bpy.data.cameras, bpy.data.lights, bpy.data.worlds):
        for datablock in list(datablocks):
            datablocks.remove(datablock)


def make_mesh_object(name: str, mesh_data: dict, collection: bpy.types.Collection,
                     material: bpy.types.Material, pass_index: int) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + ":mesh")
    mesh.from_pydata(mesh_data["vertices"], [], mesh_data["triangles"])
    mesh.validate(clean_customdata=True)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.data.materials.append(material)
    obj.pass_index = pass_index
    return obj


def pbr_material(name: str, low: tuple[float, float, float], high: tuple[float, float, float],
                 roughness: float, broad_scale: float, micro_scale: float,
                 bump_strength: float, bump_distance: float) -> bpy.types.Material:
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Roughness"].default_value = roughness
    coordinate = nodes.new("ShaderNodeTexCoord")
    broad = nodes.new("ShaderNodeTexNoise")
    broad.inputs["Scale"].default_value = broad_scale
    broad.inputs["Detail"].default_value = 3.0
    broad.inputs["Roughness"].default_value = 0.55
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*low, 1.0)
    ramp.color_ramp.elements[1].color = (*high, 1.0)
    micro = nodes.new("ShaderNodeTexNoise")
    micro.inputs["Scale"].default_value = micro_scale
    micro.inputs["Detail"].default_value = 2.0
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = bump_strength
    bump.inputs["Distance"].default_value = bump_distance
    links.new(coordinate.outputs["Generated"], broad.inputs["Vector"])
    links.new(coordinate.outputs["Generated"], micro.inputs["Vector"])
    links.new(broad.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
    links.new(micro.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def flat_material(name: str, color: tuple[float, float, float, float]) -> bpy.types.Material:
    material = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = color
    emission.inputs["Strength"].default_value = 1.0
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    material.diffuse_color = color
    return material


def set_world_sky(scene: bpy.types.Scene, config: dict) -> None:
    world = bpy.data.worlds.new("Source daylight")
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Strength"].default_value = 0.32
    sky = nodes.new("ShaderNodeTexSky")
    sky.sky_type = "NISHITA"
    sky.sun_elevation = math.radians(config["sun_elevation_deg"])
    sky.air_density = config["air_density"]
    sky.dust_density = config["dust_density"]
    links.new(sky.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    scene.world = world


def set_world_flat(scene: bpy.types.Scene, color: tuple[float, float, float, float]) -> None:
    world = scene.world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Color"].default_value = color
    background.inputs["Strength"].default_value = 1.0
    links.new(background.outputs["Background"], output.inputs["Surface"])


def aim(camera: bpy.types.Object, target: Vector) -> None:
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def save_render(scene: bpy.types.Scene, path: Path, file_format: str, color_mode: str = "RGBA") -> None:
    scene.render.image_settings.file_format = file_format
    scene.render.image_settings.color_mode = color_mode
    scene.render.filepath = str(path)
    bpy.data.images["Render Result"].save_render(filepath=str(path), scene=scene)


def use_material(obj: bpy.types.Object, material: bpy.types.Material) -> None:
    if len(obj.data.materials):
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)


def render_flat(scene: bpy.types.Scene, path: Path) -> None:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = str(path)
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "Medium High Contrast"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    bpy.ops.render.render(write_still=True)


def main() -> None:
    args = argv_after_separator()
    if not args:
        raise SystemExit("scene-packet.json path is required after --")
    packet_path = Path(args[0]).resolve()
    output = Path(args[1]).resolve() if len(args) > 1 else packet_path.parent
    output.mkdir(parents=True, exist_ok=True)
    packet = json.loads(packet_path.read_text())
    clear_scene()
    scene = bpy.context.scene
    render_config = packet["render"]
    width, height = render_config["resolution"]
    percent = int(os.environ.get("GA_RENDER_PERCENT", "100"))
    samples = int(os.environ.get("GA_RENDER_SAMPLES", str(render_config["samples"])))
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = percent
    scene.render.pixel_aspect_x = 1.0
    scene.render.pixel_aspect_y = 1.0
    scene.render.film_transparent = False
    scene.render.engine = "CYCLES"
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.01
    scene.cycles.use_denoising = render_config["denoise"]
    scene.cycles.transparent_max_bounces = 16
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.35
    scene.view_settings.gamma = 1.0

    device = "CPU"
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "METAL"
        preferences.get_devices()
        metal = [item for item in preferences.devices if item.type == "METAL"]
        if metal:
            for item in preferences.devices:
                item.use = item.type == "METAL"
            scene.cycles.device = "GPU"
            device = "METAL"
    except Exception as error:
        print(f"Cycles Metal unavailable, using CPU: {error}")

    materials = {
        "road": pbr_material("Road / source", (0.055, 0.06, 0.065), (0.095, 0.10, 0.105), 0.88, 0.38, 38.0, 0.10, 0.0012),
        "sidewalk": pbr_material("Sidewalk / source", (0.31, 0.30, 0.28), (0.47, 0.45, 0.41), 0.82, 0.22, 28.0, 0.08, 0.0009),
        "lawn": pbr_material("Lawn substrate / source", (0.045, 0.085, 0.025), (0.105, 0.19, 0.055), 0.93, 0.30, 18.0, 0.07, 0.0018),
    }
    palette = render_config["semantic_palette_srgb"]
    semantic_materials = {
        key: flat_material("Semantic / " + key, tuple(channel / 255 for channel in value) + (1.0,))
        for key, value in palette.items() if key != "unknown"
    }
    black = flat_material("Mask / known", (0.0, 0.0, 0.0, 1.0))
    amber = flat_material("Confidence / XY only", (1.0, 0.35, 0.0, 1.0))
    green = flat_material("Confidence / estimated Z", (0.0, 0.8, 0.12, 1.0))

    render_collection = bpy.data.collections.new("RENDERABLE_ESTIMATED_Z")
    plan_collection = bpy.data.collections.new("PLAN_ONLY_EXACT_XY")
    scene.collection.children.link(render_collection)
    scene.collection.children.link(plan_collection)
    render_objects = []
    plan_objects = []
    for row in packet["geometry"]["render_objects"]:
        obj = make_mesh_object(row["id"], row["mesh"], render_collection,
                               materials[row["semantic_class"]], row["semantic_id"])
        obj["semantic_class"] = row["semantic_class"]
        obj["source_handle"] = row["source"]["source_handle"]
        obj["height_status"] = row["height_status"]
        render_objects.append(obj)
    for row in packet["geometry"]["plan_objects"]:
        obj = make_mesh_object("plan:" + row["id"], row["mesh"], plan_collection,
                               semantic_materials[row["semantic_class"]], row["semantic_id"])
        obj["semantic_class"] = row["semantic_class"]
        obj["source_handle"] = row["source"]["source_handle"]
        obj.location.z = -20.0
        obj.hide_render = True
        plan_objects.append(obj)

    camera_data = bpy.data.cameras.new("Camera")
    camera = bpy.data.objects.new("Camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera_data.type = render_config["camera"]["projection"]
    camera_data.lens = render_config["camera"]["lens_mm"]
    camera_data.clip_start = 0.05
    camera_data.clip_end = 1000.0
    camera.location = render_config["camera"]["position_local"]
    aim(camera, Vector(render_config["camera"]["target_local"]))

    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = render_config["sun"]["energy"]
    sun_data.angle = math.radians(render_config["sun"]["angle_deg"])
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = [math.radians(value) for value in render_config["sun"]["rotation_deg"]]
    set_world_sky(scene, render_config["world"])

    view_layer = scene.view_layers[0]
    for property_name in (
        "use_pass_z", "use_pass_normal", "use_pass_position", "use_pass_diffuse_color",
        "use_pass_roughness", "use_pass_shadow", "use_pass_ambient_occlusion", "use_pass_object_index",
        "use_pass_cryptomatte_object",
    ):
        if hasattr(view_layer, property_name):
            setattr(view_layer, property_name, True)
    if hasattr(view_layer, "pass_cryptomatte_depth"):
        view_layer.pass_cryptomatte_depth = 6

    scene.render.image_settings.file_format = "OPEN_EXR_MULTILAYER"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "32"
    scene.render.image_settings.exr_codec = "ZIP"
    beauty_exr = output / "beauty-passes.exr"
    scene.render.filepath = str(beauty_exr)
    bpy.ops.render.render(write_still=True)
    beauty_png = output / "beauty.png"
    save_render(scene, beauty_png, "PNG", "RGBA")

    # Perspective semantic diagnostic: the unknown class is the world color.
    original_materials = {obj.name: obj.data.materials[0] for obj in render_objects}
    for obj in render_objects:
        use_material(obj, semantic_materials[obj["semantic_class"]])
    unknown = tuple(channel / 255 for channel in palette["unknown"]) + (1.0,)
    set_world_flat(scene, unknown)
    semantic_perspective = output / "semantic-perspective.png"
    render_flat(scene, semantic_perspective)

    # Orthographic plan products use all exact authored XY, including areas
    # whose Z is unknown and therefore absent from the beauty scene.
    for obj in render_objects:
        obj.hide_render = True
    for obj in plan_objects:
        obj.hide_render = False
        use_material(obj, semantic_materials[obj["semantic_class"]])
    camera.data.type = "ORTHO"
    bbox = packet["coordinates"]["scope_bbox_source_xy_m"]
    scope_width, scope_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    camera.data.ortho_scale = max(scope_height, scope_width * height / width) * 1.01
    camera.location = (0.0, 0.0, 100.0)
    camera.rotation_euler = (0.0, 0.0, 0.0)
    aim(camera, Vector((0.0, 0.0, -20.0)))
    set_world_flat(scene, unknown)
    semantic_topdown = output / "semantic-topdown.png"
    render_flat(scene, semantic_topdown)

    # Dedicated binary unknown pass: white means there is no authored surface.
    for obj in plan_objects:
        use_material(obj, black)
    set_world_flat(scene, (1.0, 1.0, 1.0, 1.0))
    unknown_topdown = output / "unknown-topdown.png"
    render_flat(scene, unknown_topdown)

    # Confidence pass: red = no authored XY, amber = authored XY with unknown Z,
    # green = estimated (still unconfirmed) Z coverage.
    for obj in plan_objects:
        use_material(obj, amber)
    for obj in render_objects:
        obj.hide_render = False
        use_material(obj, green)
    set_world_flat(scene, (0.65, 0.0, 0.0, 1.0))
    confidence_topdown = output / "height-confidence-topdown.png"
    render_flat(scene, confidence_topdown)

    # Restore the reviewable scene in its beauty configuration before saving.
    for obj in plan_objects:
        obj.hide_render = True
    for obj in render_objects:
        obj.hide_render = False
        use_material(obj, original_materials[obj.name])
    camera.data.type = render_config["camera"]["projection"]
    camera.data.lens = render_config["camera"]["lens_mm"]
    camera.location = render_config["camera"]["position_local"]
    aim(camera, Vector(render_config["camera"]["target_local"]))
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = percent
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.35
    set_world_sky(scene, render_config["world"])
    blend_path = output / "scene.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    paths = [beauty_exr, beauty_png, semantic_perspective, semantic_topdown,
             unknown_topdown, confidence_topdown, blend_path]
    receipt = {
        "schema": "green-atlas.render-receipt.v1",
        "scene_packet": str(packet_path),
        "scene_packet_sha256": sha256(packet_path),
        "blender_version": bpy.app.version_string,
        "engine": "CYCLES",
        "device": device,
        "samples": samples,
        "resolution": [round(width * percent / 100), round(height * percent / 100)],
        "render_objects": len(render_objects),
        "plan_objects": len(plan_objects),
        "vegetation_rendered": 0,
        "unknown_fill_allowed": False,
        "height_extrapolation": False,
        "outputs": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)} for path in paths
        ],
        "quality": packet["quality"],
    }
    (output / "render-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
