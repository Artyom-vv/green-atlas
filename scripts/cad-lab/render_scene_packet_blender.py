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
from array import array
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


def add_value_aovs(material: bpy.types.Material, semantic_id: int,
                   roughness: float, height_confidence: float) -> None:
    if not material.use_nodes:
        material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    existing = {node.aov_name for node in nodes if node.type == "OUTPUT_AOV"}
    for aov_name, value in (
        ("Roughness", roughness),
        ("SemanticID", float(semantic_id)),
        ("HeightConfidence", height_confidence),
    ):
        if aov_name in existing:
            continue
        value_node = nodes.new("ShaderNodeValue")
        value_node.outputs["Value"].default_value = value
        aov = nodes.new("ShaderNodeOutputAOV")
        aov.aov_name = aov_name
        links.new(value_node.outputs["Value"], aov.inputs["Value"])


def pbr_material(name: str, low: tuple[float, float, float], high: tuple[float, float, float],
                 roughness: float, broad_scale: float, micro_scale: float,
                 bump_strength: float, bump_distance: float, semantic_id: int) -> bpy.types.Material:
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
    # Object coordinates keep procedural texture scale in scene metres instead
    # of stretching one normalized 0..1 texture across every differently sized
    # HATCH fragment.
    links.new(coordinate.outputs["Object"], broad.inputs["Vector"])
    links.new(coordinate.outputs["Object"], micro.inputs["Vector"])
    links.new(broad.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
    links.new(micro.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    add_value_aovs(material, semantic_id, roughness, 0.5)
    return material


def texture_material(name: str, spec: dict, semantic_id: int) -> bpy.types.Material:
    if not spec["render_enabled"]:
        raise ValueError(f"Texture material gate failed: {spec['id']}")
    for row in spec["maps"].values():
        path = Path(row["path"])
        if not path.is_file() or path.stat().st_size != row["expected_bytes"] or sha256(path) != row["expected_sha256"]:
            raise ValueError(f"Texture hash/size gate failed: {path}")
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    scale = 1.0 / spec["tile_width_m"]
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(coordinate.outputs["Object"], mapping.inputs["Vector"])

    textures = {}
    for role, row in spec["maps"].items():
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(row["path"], check_existing=True)
        texture.projection = "BOX"
        texture.projection_blend = 0.18
        texture.extension = "REPEAT"
        if role != "diffuse":
            texture.image.colorspace_settings.name = "Non-Color"
        links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        textures[role] = texture

    hue = nodes.new("ShaderNodeHueSaturation")
    hue.inputs["Saturation"].default_value = spec["saturation"]
    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MIX"
    mix.inputs[0].default_value = 0.35
    mix.inputs[2].default_value = spec["neutral_base_linear"]
    links.new(textures["diffuse"].outputs["Color"], hue.inputs["Color"])
    links.new(hue.outputs["Color"], mix.inputs[1])
    links.new(mix.outputs["Color"], shader.inputs["Base Color"])
    links.new(textures["roughness"].outputs["Color"], shader.inputs["Roughness"])
    normal = nodes.new("ShaderNodeNormalMap")
    normal.inputs["Strength"].default_value = spec["normal_strength"]
    links.new(textures["normal_gl"].outputs["Color"], normal.inputs["Color"])
    links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    add_value_aovs(material, semantic_id, spec["roughness_nominal"], 0.5)
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


def srgb_to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def srgb_color(channels: list[int]) -> tuple[float, float, float, float]:
    return tuple(srgb_to_linear(channel / 255.0) for channel in channels) + (1.0,)


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


def save_alpha_review(scene: bpy.types.Scene, source_path: Path, path: Path,
                      background=(0.72, 0.76, 0.78)) -> None:
    source = bpy.data.images.load(str(source_path), check_existing=False)
    width, height = source.size
    pixels = array("f", [0.0]) * (width*height*4)
    source.pixels.foreach_get(pixels)
    for index in range(0, len(pixels), 4):
        alpha = pixels[index+3]
        inverse = 1.0-alpha
        pixels[index] = pixels[index]*alpha + background[0]*inverse
        pixels[index+1] = pixels[index+1]*alpha + background[1]*inverse
        pixels[index+2] = pixels[index+2]*alpha + background[2]*inverse
        pixels[index+3] = 1.0
    review = bpy.data.images.new("Beauty review background", width=width, height=height, alpha=True)
    review.pixels.foreach_set(pixels)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    review.save_render(filepath=str(path), scene=scene)
    bpy.data.images.remove(review)
    bpy.data.images.remove(source)


def use_material(obj: bpy.types.Object, material: bpy.types.Material) -> None:
    if len(obj.data.materials):
        obj.data.materials[0] = material
    else:
        obj.data.materials.append(material)


def collection_objects(collection: bpy.types.Collection) -> list[bpy.types.Object]:
    result = list(collection.objects)
    for child in collection.children:
        result.extend(collection_objects(child))
    return result


def load_collection_asset(asset: dict) -> tuple[bpy.types.Collection, list[bpy.types.Object]]:
    path = Path(asset["installed_path"])
    if sha256(path) != asset["expected_sha256"] or path.stat().st_size != asset["expected_bytes"]:
        raise ValueError(f"Asset hash/size gate failed: {asset['id']}")
    requested_names = [asset["collection"]]
    if asset.get("dependency_collection"):
        requested_names.append(asset["dependency_collection"])
    with bpy.data.libraries.load(str(path), link=False) as (available, requested):
        missing = sorted(set(requested_names)-set(available.collections))
        if missing:
            raise ValueError(f"Missing asset collections in {path}: {missing}")
        requested.collections = [name for name in requested_names]
    collections = {collection.name: collection for collection in requested.collections if collection}
    main = collections[asset["collection"]]
    objects = []
    for collection in requested.collections:
        if collection:
            objects.extend(collection_objects(collection))
    return main, list({obj.name: obj for obj in objects}.values())


def render_flat(scene: bpy.types.Scene, path: Path) -> None:
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    # Diagnostic products encode unknown/background explicitly in the world.
    # Transparency would discard that class and many viewers would display it
    # as black, making the semantic product ambiguous.
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.filepath = str(path)
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
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
        "road": texture_material("Road / Poly Haven asphalt_01", packet["material_registry"]["road"], 1),
        "sidewalk": texture_material("Sidewalk / Poly Haven concrete_floor_02", packet["material_registry"]["sidewalk"], 2),
        "lawn": pbr_material("Lawn substrate / source", (0.045, 0.085, 0.025), (0.105, 0.19, 0.055), 0.93, 0.30, 18.0, 0.07, 0.0018, 3),
    }
    palette = render_config["semantic_palette_srgb"]
    semantic_materials = {
        key: flat_material("Semantic / " + key, srgb_color(value))
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

    assets = {row["id"]: row for row in packet["asset_registry"]}
    loaded_assets = {}
    asset_source_objects = []
    vegetation_instances = []
    for row in packet["vegetation_candidates"]:
        if not row["render_enabled"]:
            continue
        asset = assets[row["asset_id"]]
        if row["asset_id"] not in loaded_assets:
            loaded_assets[row["asset_id"]] = load_collection_asset(asset)
            asset_source_objects.extend(loaded_assets[row["asset_id"]][1])
        asset_collection, _objects = loaded_assets[row["asset_id"]]
        instance = bpy.data.objects.new(row["id"], None)
        instance.instance_type = "COLLECTION"
        instance.instance_collection = asset_collection
        scene.collection.objects.link(instance)
        scale = row["height_m"] / asset["native_height_m"]
        min_z = asset["native_bounds_m"][0][2]
        instance.scale = (scale, scale, scale)
        instance.location = (
            row["position_local"][0],
            row["position_local"][1],
            row["position_local"][2] - min_z*scale,
        )
        instance.rotation_euler.z = row["rotation_z_rad"]
        instance.pass_index = 10
        instance["semantic_class"] = row["semantic_class"]
        instance["inventory_number"] = row["source"]["inventory_number"]
        instance["source_handle"] = row["source"]["handle"]
        instance["asset_id"] = row["asset_id"]
        instance["placement_status"] = row["placement_status"]
        vegetation_instances.append(instance)

    asset_material_state = []
    for obj in asset_source_objects:
        if obj.type != "MESH":
            continue
        asset_material_state.append((obj, list(obj.data.materials)))
        for material in obj.data.materials:
            if material:
                add_value_aovs(material, 10, 0.58, 0.5)

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
    for aov_name in ("Roughness", "SemanticID", "HeightConfidence"):
        aov = view_layer.aovs.add()
        aov.name = aov_name
        aov.type = "VALUE"
    for property_name in (
        "use_pass_z", "use_pass_normal", "use_pass_position", "use_pass_diffuse_color",
        "use_pass_shadow", "use_pass_ambient_occlusion", "use_pass_object_index",
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
    scene.render.film_transparent = True
    bpy.ops.render.render(write_still=True)
    beauty_png = output / "beauty.png"
    save_render(scene, beauty_png, "PNG", "RGBA")
    beauty_review = output / "beauty-review.png"
    save_alpha_review(scene, beauty_png, beauty_review)

    # Perspective semantic diagnostic: the unknown class is the world color.
    original_materials = {obj.name: obj.data.materials[0] for obj in render_objects}
    for obj in render_objects:
        use_material(obj, semantic_materials[obj["semantic_class"]])
    for obj, _original in asset_material_state:
        for slot_index in range(len(obj.data.materials)):
            obj.data.materials[slot_index] = semantic_materials["existing_tree"]
    unknown = srgb_color(palette["unknown"])
    set_world_flat(scene, unknown)
    semantic_perspective = output / "semantic-perspective.png"
    render_flat(scene, semantic_perspective)

    # Binary perspective masks for the optional neural material finish. Each
    # mask contains one ground class only; the tree silhouette, other surface
    # classes and unknown background remain black. A later stage erodes every
    # class independently before combining them, so semantic edges cannot be
    # edited by an image model.
    class_masks = []
    for semantic_class in ("road", "sidewalk", "lawn"):
        for obj in render_objects:
            use_material(obj, black)
        for obj in render_objects:
            if obj["semantic_class"] == semantic_class:
                use_material(obj, flat_material("Mask / " + semantic_class, (1.0, 1.0, 1.0, 1.0)))
        for obj, _original in asset_material_state:
            for slot_index in range(len(obj.data.materials)):
                obj.data.materials[slot_index] = black
        set_world_flat(scene, (0.0, 0.0, 0.0, 1.0))
        mask_path = output / f"neural-{semantic_class}-candidate-mask.png"
        render_flat(scene, mask_path)
        class_masks.append(mask_path)

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
    for instance in vegetation_instances:
        instance.hide_render = True
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
    for obj, original in asset_material_state:
        obj.data.materials.clear()
        for material in original:
            obj.data.materials.append(material)
    for instance in vegetation_instances:
        instance.hide_render = False
    camera.data.type = render_config["camera"]["projection"]
    camera.data.lens = render_config["camera"]["lens_mm"]
    camera.location = render_config["camera"]["position_local"]
    aim(camera, Vector(render_config["camera"]["target_local"]))
    scene.render.engine = "CYCLES"
    scene.render.film_transparent = True
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = percent
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.35
    set_world_sky(scene, render_config["world"])
    bpy.ops.file.pack_all()
    blend_path = output / "scene.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))

    paths = [beauty_exr, beauty_png, beauty_review, semantic_perspective, *class_masks, semantic_topdown,
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
        "vegetation_rendered": len(vegetation_instances),
        "asset_instances": [
            {"object": instance.name, "asset_id": instance["asset_id"],
             "source_handle": instance["source_handle"], "inventory_number": instance["inventory_number"]}
            for instance in vegetation_instances
        ],
        "unknown_fill_allowed": False,
        "height_extrapolation": False,
        "passes": render_config["passes"],
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
