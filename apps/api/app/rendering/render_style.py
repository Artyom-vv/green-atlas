"""Blender appearance functions retained from the historical native_v1 pipeline.

Only materials and appearance proxies. No historical packet, coordinates, CAD
reader, site classes, camera or external-map alignment are imported.
"""

import hashlib
import math
import random
from pathlib import Path

import bpy
from mathutils import Vector


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_value(node, *names):
    for name in names:
        if name in node.inputs:
            return node.inputs[name]
    return None


def simple_material(name: str, color, roughness: float, metallic: float = 0.0):
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1.0)
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    input_value(principled, "Base Color").default_value = (*color, 1.0)
    input_value(principled, "Roughness").default_value = roughness
    input_value(principled, "Metallic").default_value = metallic
    return material


def procedural_material(
    name: str, dark, light, scale: float, roughness: float, bump_strength: float
):
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*dark, 1.0)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    for node in list(nodes):
        nodes.remove(node)
    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    noise = nodes.new("ShaderNodeTexNoise")
    ramp = nodes.new("ShaderNodeValToRGB")
    bump = nodes.new("ShaderNodeBump")
    texcoord = nodes.new("ShaderNodeTexCoord")
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 2.0
    noise.inputs["Roughness"].default_value = 0.55
    ramp.color_ramp.elements[0].position = 0.28
    ramp.color_ramp.elements[0].color = (*dark, 1.0)
    ramp.color_ramp.elements[1].position = 0.72
    ramp.color_ramp.elements[1].color = (*light, 1.0)
    bump.inputs["Strength"].default_value = bump_strength
    bump.inputs["Distance"].default_value = 0.025
    input_value(principled, "Roughness").default_value = roughness
    # Object coordinates stay in scene metres because surface vertices are
    # already expressed in the object-local frame.  Generated coordinates had
    # normalized every polygon independently and erased the material scale.
    links.new(texcoord.outputs["Object"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], input_value(principled, "Base Color"))
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], input_value(principled, "Normal"))
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    return material


def texture_material(
    name,
    spec,
    saturation,
    normal_strength,
    tile_width_m,
    neutral_color,
    texture_weight,
    value=0.86,
    macro_strength=0.0,
    macro_scale=0.10,
    micro_color_strength=0.0,
    micro_scale=80.0,
    micro_bump_strength=0.0,
):
    for row in spec["maps"].values():
        path = Path(row["path"])
        if not path.is_file() or sha256(path) != row["expected_sha256"]:
            raise RuntimeError(f"Material input missing or changed: {path}")
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (1.0 / tile_width_m,) * 3
    links.new(coordinate.outputs["Object"], mapping.inputs["Vector"])
    textures = {}
    for role, row in spec["maps"].items():
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(row["path"], check_existing=True)
        texture.extension = "REPEAT"
        texture.projection = "BOX"
        texture.projection_blend = 0.25
        if role != "diffuse":
            texture.image.colorspace_settings.name = "Non-Color"
        links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        textures[role] = texture
    hue = nodes.new("ShaderNodeHueSaturation")
    hue.inputs["Saturation"].default_value = saturation
    hue.inputs["Value"].default_value = value
    links.new(textures["diffuse"].outputs["Color"], hue.inputs["Color"])
    # Blend the scan with a neutral measured-looking base.  This keeps useful
    # low-frequency variation without the conspicuous tiled/"rendered" look.
    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = "MIX"
    mix.inputs[0].default_value = texture_weight
    mix.inputs[1].default_value = (*neutral_color, 1.0)
    links.new(hue.outputs["Color"], mix.inputs[2])
    color_output = mix.outputs["Color"]
    if macro_strength > 0.0:
        macro_noise = nodes.new("ShaderNodeTexNoise")
        macro_noise.inputs["Scale"].default_value = macro_scale
        macro_noise.inputs["Detail"].default_value = 3.0
        macro_noise.inputs["Roughness"].default_value = 0.72
        macro_ramp = nodes.new("ShaderNodeValToRGB")
        macro_ramp.color_ramp.elements[0].position = 0.25
        macro_ramp.color_ramp.elements[0].color = (0.58, 0.59, 0.60, 1.0)
        macro_ramp.color_ramp.elements[1].position = 0.78
        macro_ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        macro_mix = nodes.new("ShaderNodeMixRGB")
        macro_mix.blend_type = "MULTIPLY"
        macro_mix.inputs[0].default_value = macro_strength
        links.new(mapping.outputs["Vector"], macro_noise.inputs["Vector"])
        links.new(macro_noise.outputs["Fac"], macro_ramp.inputs["Fac"])
        links.new(color_output, macro_mix.inputs[1])
        links.new(macro_ramp.outputs["Color"], macro_mix.inputs[2])
        color_output = macro_mix.outputs["Color"]
    micro_noise = None
    if micro_color_strength > 0.0 or micro_bump_strength > 0.0:
        micro_noise = nodes.new("ShaderNodeTexNoise")
        micro_noise.inputs["Scale"].default_value = micro_scale
        micro_noise.inputs["Detail"].default_value = 2.0
        micro_noise.inputs["Roughness"].default_value = 0.62
        links.new(mapping.outputs["Vector"], micro_noise.inputs["Vector"])
    if micro_color_strength > 0.0:
        micro_ramp = nodes.new("ShaderNodeValToRGB")
        micro_ramp.color_ramp.elements[0].color = (0.72, 0.74, 0.78, 1.0)
        micro_ramp.color_ramp.elements[1].color = (1.10, 1.10, 1.08, 1.0)
        micro_mix = nodes.new("ShaderNodeMixRGB")
        micro_mix.blend_type = "MULTIPLY"
        micro_mix.inputs[0].default_value = micro_color_strength
        links.new(micro_noise.outputs["Fac"], micro_ramp.inputs["Fac"])
        links.new(color_output, micro_mix.inputs[1])
        links.new(micro_ramp.outputs["Color"], micro_mix.inputs[2])
        color_output = micro_mix.outputs["Color"]
    links.new(color_output, input_value(shader, "Base Color"))
    links.new(textures["roughness"].outputs["Color"], input_value(shader, "Roughness"))
    normal = nodes.new("ShaderNodeNormalMap")
    normal.inputs["Strength"].default_value = normal_strength
    links.new(textures["normal_gl"].outputs["Color"], normal.inputs["Color"])
    if micro_bump_strength > 0.0:
        micro_bump = nodes.new("ShaderNodeBump")
        micro_bump.inputs["Strength"].default_value = micro_bump_strength
        micro_bump.inputs["Distance"].default_value = 0.006
        links.new(micro_noise.outputs["Fac"], micro_bump.inputs["Height"])
        links.new(normal.outputs["Normal"], micro_bump.inputs["Normal"])
        links.new(micro_bump.outputs["Normal"], input_value(shader, "Normal"))
    else:
        links.new(normal.outputs["Normal"], input_value(shader, "Normal"))
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def native_asphalt_material(name, spec):
    """Dark, fine-grained urban asphalt with a bounded photometric range.

    The generic texture mixer used for concept previews let the albedo scan and
    daylight lift the road into an even middle grey. Here the source scan is
    used only as spatial variation and is remapped into a narrow asphalt
    reflectance range. This keeps texture without high-contrast tiling.
    """
    for row in spec["maps"].values():
        path = Path(row["path"])
        if not path.is_file() or sha256(path) != row["expected_sha256"]:
            raise RuntimeError(f"Material input missing or changed: {path}")
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (1.0 / 1.85,) * 3
    links.new(coordinate.outputs["Object"], mapping.inputs["Vector"])

    textures = {}
    for role, row in spec["maps"].items():
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(row["path"], check_existing=True)
        texture.extension = "REPEAT"
        texture.projection = "BOX"
        texture.projection_blend = 0.30
        if role != "diffuse":
            texture.image.colorspace_settings.name = "Non-Color"
        links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        textures[role] = texture

    aggregate = nodes.new("ShaderNodeTexNoise")
    aggregate.inputs["Scale"].default_value = 34.0
    aggregate.inputs["Detail"].default_value = 4.0
    aggregate.inputs["Roughness"].default_value = 0.64
    aggregate_ramp = nodes.new("ShaderNodeValToRGB")
    aggregate_ramp.color_ramp.elements[0].position = 0.18
    aggregate_ramp.color_ramp.elements[0].color = (0.028, 0.034, 0.043, 1.0)
    aggregate_ramp.color_ramp.elements[1].position = 0.82
    aggregate_ramp.color_ramp.elements[1].color = (0.090, 0.102, 0.116, 1.0)
    links.new(mapping.outputs["Vector"], aggregate.inputs["Vector"])
    links.new(aggregate.outputs["Fac"], aggregate_ramp.inputs["Fac"])
    links.new(aggregate_ramp.outputs["Color"], input_value(shader, "Base Color"))

    roughness_range = nodes.new("ShaderNodeMapRange")
    roughness_range.inputs["From Min"].default_value = 0.0
    roughness_range.inputs["From Max"].default_value = 1.0
    roughness_range.inputs["To Min"].default_value = 0.76
    roughness_range.inputs["To Max"].default_value = 0.94
    roughness_range.clamp = True
    links.new(textures["roughness"].outputs["Color"], roughness_range.inputs["Value"])
    links.new(roughness_range.outputs["Result"], input_value(shader, "Roughness"))

    normal = nodes.new("ShaderNodeNormalMap")
    normal.inputs["Strength"].default_value = 0.34
    links.new(textures["normal_gl"].outputs["Color"], normal.inputs["Color"])
    micro = nodes.new("ShaderNodeTexNoise")
    micro.inputs["Scale"].default_value = 78.0
    micro.inputs["Detail"].default_value = 2.0
    micro.inputs["Roughness"].default_value = 0.56
    links.new(mapping.outputs["Vector"], micro.inputs["Vector"])
    micro_bump = nodes.new("ShaderNodeBump")
    micro_bump.inputs["Strength"].default_value = 0.18
    micro_bump.inputs["Distance"].default_value = 0.004
    links.new(micro.outputs["Fac"], micro_bump.inputs["Height"])
    links.new(normal.outputs["Normal"], micro_bump.inputs["Normal"])
    links.new(micro_bump.outputs["Normal"], input_value(shader, "Normal"))
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def load_collection_asset(source: Path, collection_name: str, semantic: str):
    if not source.is_file():
        raise RuntimeError(f"Botaniq asset is missing: {source}")
    with bpy.data.libraries.load(str(source), link=False) as (available, requested):
        requested.objects = available.objects
    collection = bpy.data.collections.new(collection_name)
    height = 0.0
    for obj in requested.objects:
        if obj is None:
            continue
        collection.objects.link(obj)
        if obj.type == "MESH":
            obj["semantic"] = semantic
            height = max(height, float(obj.dimensions.z))
    if height <= 0.0:
        raise RuntimeError(f"Botaniq asset has no measurable mesh: {source}")
    return collection, height


def add_botaniq_lawn_instances(
    surface_rows,
    sources,
    camera_xy=(0.0, 0.0),
    radius_m=85.0,
    spacing_m=0.56,
    max_instances=50000,
):
    """Scatter a stable Botaniq turf patch only inside admitted lawn triangles.

    The near-camera radius prevents distant sub-pixel blades from turning into
    temporal/high-frequency noise.  Outside it, the metric PBR lawn remains.
    """
    assets = []
    tuned_materials = []
    for asset_index, source in enumerate(sources):
        collection, native_height = load_collection_asset(
            source,
            f"Botaniq_lawn_patch_{asset_index}",
            "lawn_detail_proxy",
        )
        tuned_materials.extend(tune_botaniq_lawn_material(collection))
        assets.append((collection, native_height, source))
    seed_text = (
        ":".join(
            sorted(row["id"] for row in surface_rows if row.get("semantic") == "lawn")
        )
        + ":botaniq"
    )
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    count = 0
    radius_squared = radius_m * radius_m
    phase_x = ((seed & 0xFFFF) / 0xFFFF) * spacing_m
    phase_y = (((seed >> 16) & 0xFFFF) / 0xFFFF) * spacing_m
    occupied_cells = set()
    for row in surface_rows:
        if row.get("semantic") != "lawn":
            continue
        for triangle in row["triangles_scene_xyz_m"]:
            a, b, c = (Vector(tuple(map(float, point))) for point in triangle)
            denominator = (b.y - c.y) * (a.x - c.x) + (c.x - b.x) * (a.y - c.y)
            if abs(denominator) <= 1e-10:
                continue
            ix0 = math.ceil(
                (max(min(a.x, b.x, c.x), camera_xy[0] - radius_m) - phase_x) / spacing_m
            )
            ix1 = math.floor(
                (min(max(a.x, b.x, c.x), camera_xy[0] + radius_m) - phase_x) / spacing_m
            )
            iy0 = math.ceil(
                (max(min(a.y, b.y, c.y), camera_xy[1] - radius_m) - phase_y) / spacing_m
            )
            iy1 = math.floor(
                (min(max(a.y, b.y, c.y), camera_xy[1] + radius_m) - phase_y) / spacing_m
            )
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    cell = (ix, iy)
                    if cell in occupied_cells:
                        continue
                    cell_seed = (
                        seed
                        ^ ((ix * 73856093) & 0xFFFFFFFFFFFFFFFF)
                        ^ ((iy * 19349663) & 0xFFFFFFFFFFFFFFFF)
                    )
                    cell_rng = random.Random(cell_seed)
                    x = phase_x + ix * spacing_m
                    y = phase_y + iy * spacing_m
                    if (x - camera_xy[0]) ** 2 + (
                        y - camera_xy[1]
                    ) ** 2 > radius_squared:
                        continue
                    w0 = (
                        (b.y - c.y) * (x - c.x) + (c.x - b.x) * (y - c.y)
                    ) / denominator
                    w1 = (
                        (c.y - a.y) * (x - c.x) + (a.x - c.x) * (y - c.y)
                    ) / denominator
                    w2 = 1.0 - w0 - w1
                    if min(w0, w1, w2) < -1e-7:
                        continue
                    jitter_x = x + (cell_rng.random() - 0.5) * spacing_m * 0.28
                    jitter_y = y + (cell_rng.random() - 0.5) * spacing_m * 0.28
                    jw0 = (
                        (b.y - c.y) * (jitter_x - c.x) + (c.x - b.x) * (jitter_y - c.y)
                    ) / denominator
                    jw1 = (
                        (c.y - a.y) * (jitter_x - c.x) + (a.x - c.x) * (jitter_y - c.y)
                    ) / denominator
                    jw2 = 1.0 - jw0 - jw1
                    if min(jw0, jw1, jw2) >= -1e-7:
                        x, y, w0, w1, w2 = jitter_x, jitter_y, jw0, jw1, jw2
                    occupied_cells.add(cell)
                    point = a * w0 + b * w1 + c * w2
                    if count >= max_instances:
                        break
                    asset_index = min(
                        int(cell_rng.random() * len(assets)), len(assets) - 1
                    )
                    collection, _, _ = assets[asset_index]
                    instance = bpy.data.objects.new(f"Botaniq_lawn_{count:04d}", None)
                    instance.instance_type = "COLLECTION"
                    instance.instance_collection = collection
                    instance.location = (point.x, point.y, point.z + 0.003)
                    horizontal_scale = 1.02 + cell_rng.random() * 0.10
                    vertical_scale = 0.62 + cell_rng.random() * 0.12
                    instance.scale = (
                        horizontal_scale,
                        horizontal_scale,
                        vertical_scale,
                    )
                    instance.rotation_euler.z = cell_rng.random() * math.tau
                    instance["semantic"] = "lawn_detail_proxy"
                    instance["source_surface_id"] = row["id"]
                    bpy.context.collection.objects.link(instance)
                    count += 1
                    if count >= max_instances:
                        break
                if count >= max_instances:
                    break
            if count >= max_instances:
                break
    return count, assets, seed, sorted(set(tuned_materials))


def tune_botaniq_lawn_material(collection):
    """Keep the supplied texture while matching a maintained urban lawn."""
    tuned = []
    for obj in collection.objects:
        if obj.type != "MESH":
            continue
        for material in obj.data.materials:
            if material is None or not material.use_nodes:
                continue
            group = next(
                (
                    node
                    for node in material.node_tree.nodes
                    if node.type == "GROUP"
                    and "Color Top" in node.inputs
                    and "Fresh Value" in node.inputs
                ),
                None,
            )
            if group is None:
                continue
            # Preserve the authored Botaniq palette. Only a restrained global
            # correction is applied; forcing dark replacement colours made the
            # lawn look dirty and disconnected from daylight.
            values = {
                "Fresh Saturation": 1.02,
                "Fresh Value": 0.86,
                "Macro Strength": 0.18,
            }
            for name, value in values.items():
                if name in group.inputs:
                    group.inputs[name].default_value = value
            tuned.append(material.name)
    return sorted(set(tuned))


def native_facade_material(name, base=(0.44, 0.42, 0.38), panel_period_m=3.0):
    """Clean maintained concrete with restrained wear and metric floor joints."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.42
    noise.inputs["Detail"].default_value = 3.0
    noise.inputs["Roughness"].default_value = 0.64
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.20
    ramp.color_ramp.elements[0].color = tuple(
        max(0.0, value * 0.84) for value in base
    ) + (1.0,)
    ramp.color_ramp.elements[1].position = 0.85
    ramp.color_ramp.elements[1].color = tuple(
        min(1.0, value * 1.08) for value in base
    ) + (1.0,)
    micro = nodes.new("ShaderNodeTexNoise")
    micro.inputs["Scale"].default_value = 42.0
    micro.inputs["Detail"].default_value = 5.0
    micro.inputs["Roughness"].default_value = 0.72
    micro_ramp = nodes.new("ShaderNodeValToRGB")
    micro_ramp.color_ramp.elements[0].color = (0.94, 0.94, 0.92, 1.0)
    micro_ramp.color_ramp.elements[1].color = (1.02, 1.02, 1.0, 1.0)
    color_mix = nodes.new("ShaderNodeMixRGB")
    color_mix.blend_type = "MULTIPLY"
    color_mix.inputs[0].default_value = 0.20
    wave = nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "Z"
    wave.inputs["Scale"].default_value = math.tau / panel_period_m
    wave.inputs["Distortion"].default_value = 0.04
    joint_ramp = nodes.new("ShaderNodeValToRGB")
    joint_ramp.color_ramp.elements[0].position = 0.485
    joint_ramp.color_ramp.elements[0].color = (0.46, 0.46, 0.46, 1.0)
    joint_ramp.color_ramp.elements[1].position = 0.515
    joint_ramp.color_ramp.elements[1].color = (0.52, 0.52, 0.52, 1.0)
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.035
    micro_bump = nodes.new("ShaderNodeBump")
    micro_bump.inputs["Strength"].default_value = 0.040
    micro_bump.inputs["Distance"].default_value = 0.006
    input_value(shader, "Roughness").default_value = 0.88
    links.new(coordinate.outputs["Object"], noise.inputs["Vector"])
    links.new(coordinate.outputs["Object"], micro.inputs["Vector"])
    links.new(coordinate.outputs["Object"], wave.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(micro.outputs["Fac"], micro_ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], color_mix.inputs[1])
    links.new(micro_ramp.outputs["Color"], color_mix.inputs[2])
    links.new(color_mix.outputs["Color"], input_value(shader, "Base Color"))
    links.new(micro.outputs["Fac"], micro_bump.inputs["Height"])
    links.new(micro_bump.outputs["Normal"], bump.inputs["Normal"])
    links.new(wave.outputs["Color"], joint_ramp.inputs["Fac"])
    links.new(joint_ramp.outputs["Color"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], input_value(shader, "Normal"))
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def glass_material(
    name="Non-reflective glazing appearance proxy",
    dark=(0.005, 0.009, 0.012),
    light=(0.018, 0.026, 0.030),
):
    """Opaque diffuse glazing proxy with no invented environment reflection."""
    material = bpy.data.materials.new(name)
    material.diffuse_color = (0.045, 0.065, 0.07, 1.0)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    diffuse = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse.inputs["Roughness"].default_value = 1.0
    coordinate = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.22
    noise.inputs["Detail"].default_value = 1.2
    noise.inputs["Roughness"].default_value = 0.42
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.25
    ramp.color_ramp.elements[0].color = (*dark, 1.0)
    ramp.color_ramp.elements[1].position = 0.75
    ramp.color_ramp.elements[1].color = (*light, 1.0)
    links.new(coordinate.outputs["Object"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], diffuse.inputs["Color"])
    links.new(diffuse.outputs["BSDF"], output.inputs["Surface"])
    return material


def add_building_joint_proxies(row, material):
    """Add deterministic shallow floor joints inside the known building envelope."""
    building_class = row.get("class")
    if building_class not in {"apartments", "retail"} or float(row["height_m"]) < 5.0:
        return 0
    vertices = []
    faces = []
    joint_count = 0
    for ring in row["rings_scene_xyz_m"]:
        centroid = Vector(
            (
                sum(float(point[0]) for point in ring) / len(ring),
                sum(float(point[1]) for point in ring) / len(ring),
                0.0,
            )
        )
        floors = max(1, int(float(row["height_m"]) / 3.0) - 1)
        for edge_index, start_raw in enumerate(ring):
            end_raw = ring[(edge_index + 1) % len(ring)]
            start = Vector(tuple(map(float, start_raw)))
            end = Vector(tuple(map(float, end_raw)))
            delta = end - start
            length = math.hypot(delta.x, delta.y)
            if length < 2.0:
                continue
            tangent = Vector((delta.x / length, delta.y / length, 0.0))
            outward = Vector((-tangent.y, tangent.x, 0.0))
            midpoint = (start + end) * 0.5
            if (centroid - midpoint).dot(outward) > 0.0:
                outward.negate()
            for floor in range(1, floors + 1):
                z = (start.z + end.z) * 0.5 + floor * 3.0
                half_height = 0.018
                depth = 0.028
                p0 = Vector((start.x, start.y, z - half_height)) + outward * 0.042
                p1 = Vector((end.x, end.y, z - half_height)) + outward * 0.042
                p2 = Vector((end.x, end.y, z + half_height)) + outward * 0.042
                p3 = Vector((start.x, start.y, z + half_height)) + outward * 0.042
                back = outward * -depth
                offset = len(vertices)
                vertices.extend(
                    tuple(point)
                    for point in (
                        p0,
                        p1,
                        p2,
                        p3,
                        p0 + back,
                        p1 + back,
                        p2 + back,
                        p3 + back,
                    )
                )
                faces.extend(
                    (
                        (offset, offset + 1, offset + 2, offset + 3),
                        (offset, offset + 4, offset + 5, offset + 1),
                        (offset + 1, offset + 5, offset + 6, offset + 2),
                        (offset + 2, offset + 6, offset + 7, offset + 3),
                    )
                )
                joint_count += 1
            if building_class == "retail":
                columns = max(2, int(length / 4.2))
                base_z = (start.z + end.z) * 0.5 + 0.45
                top_z = (start.z + end.z) * 0.5 + float(row["height_m"]) - 0.30
                for column in range(1, columns):
                    center = (
                        start + tangent * (length * column / columns) + outward * 0.042
                    )
                    half_width = 0.018
                    depth = 0.028
                    p0 = Vector(
                        (
                            center.x - tangent.x * half_width,
                            center.y - tangent.y * half_width,
                            base_z,
                        )
                    )
                    p1 = Vector(
                        (
                            center.x + tangent.x * half_width,
                            center.y + tangent.y * half_width,
                            base_z,
                        )
                    )
                    p2 = Vector(
                        (
                            center.x + tangent.x * half_width,
                            center.y + tangent.y * half_width,
                            top_z,
                        )
                    )
                    p3 = Vector(
                        (
                            center.x - tangent.x * half_width,
                            center.y - tangent.y * half_width,
                            top_z,
                        )
                    )
                    back = outward * -depth
                    offset = len(vertices)
                    vertices.extend(
                        tuple(point)
                        for point in (
                            p0,
                            p1,
                            p2,
                            p3,
                            p0 + back,
                            p1 + back,
                            p2 + back,
                            p3 + back,
                        )
                    )
                    faces.extend(
                        (
                            (offset, offset + 1, offset + 2, offset + 3),
                            (offset, offset + 4, offset + 5, offset + 1),
                            (offset + 1, offset + 5, offset + 6, offset + 2),
                            (offset + 2, offset + 6, offset + 7, offset + 3),
                        )
                    )
                    joint_count += 1
    if not vertices:
        return 0
    mesh = bpy.data.meshes.new(f"{row['id']}_facade_joints_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new(f"{row['id']}_facade_joints", mesh)
    bpy.context.collection.objects.link(obj)
    obj["semantic"] = "building_detail_proxy"
    obj["status"] = (
        "class-generic 3 m facade joint proxy; exact facade construction not surveyed"
    )
    obj["source_id"] = row["id"]
    return joint_count


def add_context_windows(
    row, materials, camera_xy=(0.0, 0.0), frame_material=None, native_depth=False
):
    """Add a deterministic distant-facade proxy; exact openings remain unknown."""
    if row.get("class") != "apartments" or float(row["height_m"]) < 9.0:
        return 0
    count = 0
    for ring in row["rings_scene_xyz_m"]:
        edges = []
        for index, start in enumerate(ring):
            end = ring[(index + 1) % len(ring)]
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if length < 3.45:
                continue
            # Display rings retain their exact CAD segments. Near-coincident
            # outlines can produce tiny edges and split a wall into segments;
            # decorate usable facing segments rather than the closest two.
            outward_x, outward_y = (
                (end[1] - start[1]) / length,
                -(end[0] - start[0]) / length,
            )
            if (camera_xy[0] - midpoint[0]) * outward_x + (
                camera_xy[1] - midpoint[1]
            ) * outward_y <= 0:
                continue
            edges.append((math.dist(midpoint, camera_xy), -length, start, end))
        for _, _, start, end in sorted(edges):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            columns = max(0, int(length / 3.45))
            floors = max(1, int(float(row["height_m"]) / 3.0) - 1)
            if columns == 0:
                continue
            nx, ny = -dy / length, dx / length
            midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if (camera_xy[0] - midpoint[0]) * nx + (
                camera_xy[1] - midpoint[1]
            ) * ny < 0:
                nx, ny = -nx, -ny
            ux, uy = dx / length, dy / length
            for column in range(columns):
                # A stable blank bay breaks the synthetic all-window grid and
                # stands in for unsurveyed stair/service facade zones.
                blank_key = int(
                    hashlib.sha256(f"{row['id']}:{column}:blank".encode()).hexdigest()[
                        :8
                    ],
                    16,
                )
                if columns >= 6 and blank_key % 11 == 0:
                    continue
                center_distance = (column + 0.5) / columns * length
                center_x = start[0] + ux * center_distance + nx * 0.035
                center_y = start[1] + uy * center_distance + ny * 0.035
                base_z = start[2] + (end[2] - start[2]) * ((column + 0.5) / columns)
                dimension_key = int(
                    hashlib.sha256(
                        f"{row['id']}:{column}:dimension".encode()
                    ).hexdigest()[:8],
                    16,
                )
                width_factor = 0.48 + (dimension_key % 9) * 0.012
                width = min(1.35, length / columns * width_factor)
                for floor in range(floors):
                    center_z = base_z + 1.25 + floor * 3.0
                    height_factor = 0.54 + ((dimension_key >> 4) % 7) * 0.015
                    half_w, half_h = width / 2.0, height_factor
                    pane = [
                        (
                            center_x - ux * half_w,
                            center_y - uy * half_w,
                            center_z - half_h,
                        ),
                        (
                            center_x + ux * half_w,
                            center_y + uy * half_w,
                            center_z - half_h,
                        ),
                        (
                            center_x + ux * half_w,
                            center_y + uy * half_w,
                            center_z + half_h,
                        ),
                        (
                            center_x - ux * half_w,
                            center_y - uy * half_w,
                            center_z + half_h,
                        ),
                    ]
                    vertices = pane
                    faces = [(0, 1, 2, 3)]
                    if native_depth and frame_material:
                        frame_width = 0.085
                        outer = [
                            (
                                center_x - ux * (half_w + frame_width),
                                center_y - uy * (half_w + frame_width),
                                center_z - half_h - frame_width,
                            ),
                            (
                                center_x + ux * (half_w + frame_width),
                                center_y + uy * (half_w + frame_width),
                                center_z - half_h - frame_width,
                            ),
                            (
                                center_x + ux * (half_w + frame_width),
                                center_y + uy * (half_w + frame_width),
                                center_z + half_h + frame_width,
                            ),
                            (
                                center_x - ux * (half_w + frame_width),
                                center_y - uy * (half_w + frame_width),
                                center_z + half_h + frame_width,
                            ),
                        ]
                        vertices = pane + outer
                        faces.extend(
                            ((4, 5, 1, 0), (5, 6, 2, 1), (6, 7, 3, 2), (7, 4, 0, 3))
                        )
                    mesh = bpy.data.meshes.new(f"context_window_{count:04d}_mesh")
                    mesh.from_pydata(vertices, [], faces)
                    variant_key = f"{row['id']}:{column}:{floor}".encode()
                    variant = int(
                        hashlib.sha256(variant_key).hexdigest()[:8], 16
                    ) % len(materials)
                    mesh.materials.append(materials[variant])
                    if native_depth and frame_material:
                        mesh.materials.append(frame_material)
                        for polygon in mesh.polygons[1:]:
                            polygon.material_index = 1
                    mesh.update()
                    obj = bpy.data.objects.new(f"Context_window_{count:04d}", mesh)
                    bpy.context.collection.objects.link(obj)
                    obj["status"] = (
                        "procedural facade appearance proxy; opening positions not source geometry"
                    )
                    obj["semantic"] = "building_detail_proxy"
                    if native_depth:
                        solidify = obj.modifiers.new(
                            "window reveal depth proxy", "SOLIDIFY"
                        )
                        solidify.thickness = 0.045
                        solidify.offset = -0.5
                        bevel = obj.modifiers.new("window frame edge", "BEVEL")
                        bevel.width = 0.006
                        bevel.segments = 1
                    count += 1
    return count


def tune_stable_leaf_materials():
    """Restore the leaf smoothing used for the 21 September stable views."""
    changes = []
    targets = {
        "Value": 1.22,
        "Roughness": 0.62,
        "Normal Strength": 0.25,
        "Bump Strength": 0.18,
        "Translucency Factor": 0.6,
    }
    for material in bpy.data.materials:
        if not material.use_nodes or not material.name.startswith("bq_Leaf_"):
            continue
        for node in material.node_tree.nodes:
            if node.type != "GROUP":
                continue
            applied = {}
            for key, value in targets.items():
                socket = node.inputs.get(key)
                if socket is not None and not socket.is_linked:
                    applied[key] = {
                        "before": float(socket.default_value),
                        "after": value,
                    }
                    socket.default_value = value
            if applied:
                changes.append({"material": material.name, "changes": applied})
    return changes


def sky_backdrop_material(profile="prototype"):
    """Deterministic appearance-only sky with broad, non-photographic clouds."""
    material = bpy.data.materials.new("Locked daylight sky backdrop")
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (1.25, 1.0, 0.90)
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 10.0 if profile == "native_v1" else 4.6
    noise.inputs["Detail"].default_value = 5.0 if profile == "native_v1" else 2.6
    noise.inputs["Roughness"].default_value = 0.58 if profile == "native_v1" else 0.52
    noise.inputs["Distortion"].default_value = 0.22 if profile == "native_v1" else 0.28
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.50 if profile == "native_v1" else 0.54
    ramp.color_ramp.elements[0].color = (
        (0.22, 0.55, 1.0, 1.0) if profile == "native_v1" else (0.32, 0.48, 0.66, 1.0)
    )
    ramp.color_ramp.elements[1].position = 0.62 if profile == "native_v1" else 0.70
    ramp.color_ramp.elements[1].color = (
        (0.90, 0.93, 0.96, 1.0) if profile == "native_v1" else (0.84, 0.85, 0.84, 1.0)
    )
    emission.inputs["Strength"].default_value = 1.08 if profile == "native_v1" else 0.82
    links.new(coordinate.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def add_sky_backdrop(camera_position, target_position, material, distance_m=520.0):
    camera = Vector(camera_position)
    target = Vector(target_position)
    forward = Vector((target.x - camera.x, target.y - camera.y, 0.0)).normalized()
    right = Vector((forward.y, -forward.x, 0.0))
    ratio = distance_m / 520.0
    centre = camera + forward * distance_m + Vector((0.0, 0.0, 125.0 * ratio))
    half_width, bottom, top = 650.0 * ratio, -135.0 * ratio, 385.0 * ratio
    vertices = [
        tuple(centre - right * half_width + Vector((0.0, 0.0, bottom))),
        tuple(centre + right * half_width + Vector((0.0, 0.0, bottom))),
        tuple(centre + right * half_width + Vector((0.0, 0.0, top))),
        tuple(centre - right * half_width + Vector((0.0, 0.0, top))),
    ]
    mesh = bpy.data.meshes.new("sky_backdrop_mesh")
    mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new("Sky_backdrop_locked_preset", mesh)
    bpy.context.collection.objects.link(obj)
    obj["semantic"] = "sky_preset"
    obj["status"] = (
        "deterministic appearance-only daylight preset; not observed weather"
    )
    # This camera backdrop is not physical scene geometry or an area light.
    obj.visible_shadow = False
    obj.visible_diffuse = False
    obj.visible_glossy = False
    return obj


def add_retail_glazing(
    row, material, camera_xy=(0.0, 0.0), frame_material=None, native_depth=False
):
    ring = row["rings_scene_xyz_m"][0]
    centroid = (
        sum(float(point[0]) for point in ring) / len(ring),
        sum(float(point[1]) for point in ring) / len(ring),
    )
    candidates = []
    for index, start in enumerate(ring):
        end = ring[(index + 1) % len(ring)]
        midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        dx, dy = end[0] - start[0], end[1] - start[1]
        if math.hypot(dx, dy) < 5.0:
            continue
        camera_side = dx * (camera_xy[1] - start[1]) - dy * (camera_xy[0] - start[0])
        centroid_side = dx * (centroid[1] - start[1]) - dy * (centroid[0] - start[0])
        if camera_side * centroid_side < 0:
            candidates.append(
                (math.dist(midpoint, camera_xy), -math.hypot(dx, dy), index, start, end)
            )
    # Preserve segmented CAD facades, including all usable facing segments.
    candidates = sorted(candidates)
    objects = []
    for facade_index, (_, _, _, start, end) in enumerate(candidates):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 5.0:
            continue
        nx, ny = -dy / length, dx / length
        midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        if (camera_xy[0] - midpoint[0]) * nx + (camera_xy[1] - midpoint[1]) * ny < 0:
            nx, ny = -nx, -ny
        offset = 0.035
        usable_start, usable_end = 0.12, 0.88
        panel_count = max(2, min(9, int(length / 3.2)))
        for panel in range(panel_count):
            left = (
                usable_start
                + (usable_end - usable_start) * (panel + 0.08) / panel_count
            )
            right = (
                usable_start
                + (usable_end - usable_start) * (panel + 0.92) / panel_count
            )
            a = [start[i] + (end[i] - start[i]) * left for i in range(3)]
            b = [start[i] + (end[i] - start[i]) * right for i in range(3)]
            pane = [
                (a[0] + nx * offset, a[1] + ny * offset, a[2] + 0.65),
                (b[0] + nx * offset, b[1] + ny * offset, b[2] + 0.65),
                (b[0] + nx * offset, b[1] + ny * offset, b[2] + 3.20),
                (a[0] + nx * offset, a[1] + ny * offset, a[2] + 3.20),
            ]
            vertices = pane
            faces = [(0, 1, 2, 3)]
            if native_depth and frame_material:
                ux, uy = dx / length, dy / length
                frame_width = 0.10
                outer = [
                    (
                        pane[0][0] - ux * frame_width,
                        pane[0][1] - uy * frame_width,
                        pane[0][2] - frame_width,
                    ),
                    (
                        pane[1][0] + ux * frame_width,
                        pane[1][1] + uy * frame_width,
                        pane[1][2] - frame_width,
                    ),
                    (
                        pane[2][0] + ux * frame_width,
                        pane[2][1] + uy * frame_width,
                        pane[2][2] + frame_width,
                    ),
                    (
                        pane[3][0] - ux * frame_width,
                        pane[3][1] - uy * frame_width,
                        pane[3][2] + frame_width,
                    ),
                ]
                vertices = pane + outer
                faces.extend(((4, 5, 1, 0), (5, 6, 2, 1), (6, 7, 3, 2), (7, 4, 0, 3)))
            mesh = bpy.data.meshes.new(
                f"retail_glazing_{facade_index:02d}_{panel:02d}_mesh"
            )
            mesh.from_pydata(vertices, [], faces)
            mesh.materials.append(material)
            if native_depth and frame_material:
                mesh.materials.append(frame_material)
                for polygon in mesh.polygons[1:]:
                    polygon.material_index = 1
            mesh.update()
            obj = bpy.data.objects.new(
                f"Retail_class_glazing_proxy_{facade_index:02d}_{panel:02d}", mesh
            )
            bpy.context.collection.objects.link(obj)
            obj["status"] = (
                "class-generic non-reflective appearance proxy; exact openings not surveyed"
            )
            obj["semantic"] = "building_detail_proxy"
            if native_depth:
                solidify = obj.modifiers.new("window reveal depth proxy", "SOLIDIFY")
                solidify.thickness = 0.055
                solidify.offset = -0.5
                bevel = obj.modifiers.new("window frame edge", "BEVEL")
                bevel.width = 0.008
                bevel.segments = 1
            objects.append(obj)
    return objects


def add_environment_curbs(rows, material):
    """Build the native_v1 stone appearance along supplied scene curb chains.

    Locations come only from the supplied local XYZ endpoints. Width, height,
    metre-scale stones, joints and bevel are visual dimensions, not survey
    evidence. Separate source chains are never joined across a visible gap.
    """
    from bisect import bisect_right

    chains = []
    active = None
    for row in rows:
        start_values = tuple(float(value) for value in row["start_scene_xyz_m"])
        end_values = tuple(float(value) for value in row["end_scene_xyz_m"])
        width = float(row["width_m"])
        height = float(row["height_m"])
        if (
            len(start_values) != 3
            or len(end_values) != 3
            or not all(math.isfinite(value) for value in (*start_values, *end_values))
            or not math.isfinite(width)
            or not math.isfinite(height)
            or width <= 0
            or height <= 0
        ):
            raise ValueError("Invalid environment curb coordinates or dimensions")
        if math.dist(start_values[:2], end_values[:2]) <= 1e-5:
            continue
        key = (row.get("source_handle"), row.get("curb_class"), width, height)
        if active is not None and active["key"] == key:
            if active["points"][-1] == start_values:
                active["points"].append(end_values)
                continue
            if active["points"][-1] == end_values:
                active["points"].append(start_values)
                continue
        active = {"key": key, "points": [start_values, end_values], "row": row}
        chains.append(active)

    vertices, faces = [], []
    stone_count = 0
    for chain in chains:
        points = chain["points"]
        row = chain["row"]
        cumulative = [0.0]
        for start, end in zip(points, points[1:]):
            cumulative.append(cumulative[-1] + math.dist(start[:2], end[:2]))
        total_length = cumulative[-1]

        def point_at(station):
            station = min(max(station, 0.0), total_length)
            index = min(bisect_right(cumulative, station) - 1, len(points) - 2)
            length = cumulative[index + 1] - cumulative[index]
            ratio = (station - cumulative[index]) / length
            return Vector(
                tuple(
                    a + (b - a) * ratio
                    for a, b in zip(points[index], points[index + 1])
                )
            )

        pieces = max(1, math.ceil(total_length / 1.0))
        block_length = total_length / pieces
        joint_gap = 0.014 if pieces > 1 else 0.0
        half_width = float(row["width_m"]) * 0.5
        height = float(row["height_m"])
        for piece in range(pieces):
            start = point_at(piece * block_length + joint_gap * 0.5)
            end = point_at((piece + 1) * block_length - joint_gap * 0.5)
            delta = end - start
            length = math.hypot(delta.x, delta.y)
            if length <= 1e-5:
                continue
            normal = Vector((-delta.y / length, delta.x / length, 0.0))
            bottom = [
                start + normal * half_width,
                start - normal * half_width,
                end - normal * half_width,
                end + normal * half_width,
            ]
            top = [point + Vector((0.0, 0.0, height)) for point in bottom]
            offset = len(vertices)
            vertices.extend(tuple(point) for point in bottom + top)
            faces.extend(
                tuple(reversed(face))
                for face in [
                    (offset, offset + 1, offset + 2, offset + 3),
                    (offset + 4, offset + 7, offset + 6, offset + 5),
                    (offset, offset + 4, offset + 5, offset + 1),
                    (offset + 1, offset + 5, offset + 6, offset + 2),
                    (offset + 2, offset + 6, offset + 7, offset + 3),
                    (offset + 3, offset + 7, offset + 4, offset),
                ]
            )
            stone_count += 1
    if not stone_count:
        return None
    mesh = bpy.data.meshes.new("Environment_curbs_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new("Environment_curbs_native_stones", mesh)
    bpy.context.collection.objects.link(obj)
    obj["semantic"] = "curb"
    obj["status"] = (
        "source-positioned curb chains; dimensions, stone joints and bevel are "
        "appearance proxies"
    )
    obj["stone_count"] = stone_count
    obj["source_chain_count"] = len(chains)
    obj["joint_gap_m"] = 0.014
    bevel = obj.modifiers.new("curb edge", "BEVEL")
    bevel.width = 0.010
    bevel.segments = 2
    return obj
