"""Render the reviewed NSPD street scene packet in Blender.

Run with Blender and pass arguments after ``--``.  This stage deliberately
produces a deterministic geometry/material prototype; it is not beauty-admitted
and neural finishing remains disabled by the packet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PACKET = ROOT / ".runtime/nspd-scene-kustanayskaya-20260920/scene-packet.json"
DEFAULT_OUTPUT = ROOT / ".runtime/nspd-scene-kustanayskaya-20260920/render"
BOTANIQ = Path(os.environ.get("GREEN_ATLAS_BOTANIQ_ROOT", str(ROOT / ".runtime/botaniq-68-inspection/selected/botaniq_full")))
TREE_ASSETS = {
    "Tilia-europaea": (
        BOTANIQ / "blends_280/deciduous/Tree_Tilia-europaea_A_summer.blend",
        BOTANIQ / "blends_280/deciduous/Tree_Tilia-europaea_B_summer.blend",
    ),
    "Picea-abies": (
        BOTANIQ / "blends_280/coniferous/Tree_Picea-abies_A_spring-summer-autumn.blend",
        BOTANIQ / "blends_280/coniferous/Tree_Picea-abies_B_spring-summer-autumn.blend",
    ),
    "Betula-pendula": (
        BOTANIQ / "blends_280/deciduous/Tree_Betula-pendula_A_summer.blend",
        BOTANIQ / "blends_280/deciduous/Tree_Betula-pendula_B_summer.blend",
    ),
    "Acer-pseudoplatanus": (
        BOTANIQ / "blends_280/deciduous/Tree_Acer-pseudoplatanus_A_summer.blend",
        BOTANIQ / "blends_280/deciduous/Tree_Acer-pseudoplatanus_B_summer.blend",
    ),
    "Populus-tremuloides": (
        BOTANIQ / "blends_280/deciduous/Tree_Populus-tremuloides_A_summer.blend",
        BOTANIQ / "blends_280/deciduous/Tree_Populus-tremuloides_B_summer.blend",
    ),
    "Salix-babylonica": (
        BOTANIQ / "blends_280/deciduous/Tree_Salix-babylonica_A_summer.blend",
        BOTANIQ / "blends_280/deciduous/Tree_Salix-babylonica_B_summer.blend",
    ),
    "Prunus-cerasifera": (
        BOTANIQ / "blends_280/deciduous/Tree_Prunus-cerasifera_A_summer.blend",
        BOTANIQ / "blends_280/deciduous/Tree_Prunus-cerasifera_B_summer.blend",
    ),
    "Larix-decidua": (
        BOTANIQ / "blends_280/coniferous/Tree_Larix-decidua_A_spring-summer-autumn.blend",
        BOTANIQ / "blends_280/coniferous/Tree_Larix-decidua_B_spring-summer-autumn.blend",
    ),
    "Aesculus-hippocastanum": (BOTANIQ / "blends_280/deciduous/Tree_Aesculus-hippocastanum_A_summer.blend",),
    "Alnus-glutinosa": (BOTANIQ / "blends_280/deciduous/Tree_Alnus-glutinosa_A_summer.blend",),
    "Fraxinus-excelsior": (BOTANIQ / "blends_280/deciduous/Tree_Fraxinus-excelsior_A_summer.blend",),
    "Quercus-cerris": (BOTANIQ / "blends_280/deciduous/Tree_Quercus-cerris_A_summer.blend",),
    "Malus-domestica": (BOTANIQ / "blends_280/deciduous/Tree_Malus-domestica_A_summer.blend",),
    "Chamaecyparis-lawsoniana": (BOTANIQ / "blends_280/coniferous/Tree_Chamaecyparis-lawsoniana_A_spring-summer-autumn.blend",),
    "Acer-winter": (BOTANIQ / "blends_280/deciduous/Tree_Acer-pseudoplatanus_A_winter.blend",),
}
SOURCE_SPECIES_ASSET_KEY = {
    "Клен": "Acer-pseudoplatanus",
    "Клен ясенелистный": "Acer-pseudoplatanus",
    "Береза": "Betula-pendula",
    "Тополь": "Populus-tremuloides",
    "Тополь пирамидальный": "Populus-tremuloides",
    "Осина": "Populus-tremuloides",
    "Ива": "Salix-babylonica",
    "Вишня": "Prunus-cerasifera",
    "Слива": "Prunus-cerasifera",
    "Черемуха": "Prunus-cerasifera",
    "Лиственница": "Larix-decidua",
    "Липа": "Tilia-europaea",
    "Каштан": "Aesculus-hippocastanum",
    "Ольха": "Alnus-glutinosa",
    "Ясень": "Fraxinus-excelsior",
    "Дуб": "Quercus-cerris",
    "Ель": "Picea-abies",
    "Яблоня": "Malus-domestica",
    "Туя": "Chamaecyparis-lawsoniana",
    "Рябина": "Malus-domestica",
    "Боярышник": "Malus-domestica",
    "Груша": "Malus-domestica",
    "Сухостой": "Acer-winter",
}
SURROGATE_SOURCE_SPECIES = {"Рябина", "Боярышник", "Груша", "Туя", "Сухостой"}
BOTANIQ_LAWN_ASSETS = (
    BOTANIQ / "blends_280/grass/Grass_Cut-grid_B_spring-summer.blend",
    BOTANIQ / "blends_280/grass/Grass_Cut_A_spring-summer.blend",
)
NATIVE_HDRI = ROOT / "apps/web/public/assets/environment/kloofendal_48d_partly_cloudy_1k.hdr"
NATIVE_HDRI_SHA256 = "5477b7dbb2aea4a6947cb36b96339c119f90bf36a03b039af3254b5e6a22896d"
NATIVE_LAWN_DIR = Path(os.environ.get("GREEN_ATLAS_LAWN_ROOT", str(ROOT / ".runtime/cad-vegetation-20260919/pbr/grass005")))
NATIVE_LAWN_SPEC = {
    "id": "ambientcg.Grass005.2k-jpg",
    "source_url": "https://ambientcg.com/view?id=Grass005",
    "license": "CC0",
    "maps": {
        "diffuse": {"path": str(NATIVE_LAWN_DIR / "Grass005_2K-JPG_Color.jpg"), "expected_sha256": "2acdf15d09e790a114743f21bc0332a86e386cb77c54507323d626f67f0f2e10"},
        "normal_gl": {"path": str(NATIVE_LAWN_DIR / "Grass005_2K-JPG_NormalGL.jpg"), "expected_sha256": "c8406e8b784fbcd22d706fbe0c24175c327bca7e19ad8d09457fad8a05089548"},
        "roughness": {"path": str(NATIVE_LAWN_DIR / "Grass005_2K-JPG_Roughness.jpg"), "expected_sha256": "20c302c4cf059d6676d83528ae7cbf62ca35944e691dc6377a164ce9c31c8e67"},
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packet", type=Path, default=DEFAULT_PACKET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--samples", type=int, default=96)
    parser.add_argument("--engine", choices=("eevee", "cycles"), default="eevee")
    parser.add_argument("--resolution-x", type=int, default=1600)
    parser.add_argument("--resolution-y", type=int, default=900)
    parser.add_argument("--adaptive-threshold", type=float, default=0.02)
    parser.add_argument("--skip-diagnostics", action="store_true")
    parser.add_argument("--skip-blend-save", action="store_true")
    parser.add_argument("--appearance-profile", choices=("prototype", "native_v1"), default="prototype")
    parser.add_argument("--lawn-microgeometry", action="store_true")
    parser.add_argument("--botaniq-lawn", action="store_true")
    parser.add_argument("--scene-state", choices=("project", "existing_context"), default="project")
    parser.add_argument("--presentation-population", action="store_true")
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return parser.parse_args(argv)


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


def semantic_material(name: str, color):
    material = simple_material(name, color, 1.0)
    principled = material.node_tree.nodes.get("Principled BSDF")
    emission = input_value(principled, "Emission Color", "Emission")
    if emission:
        emission.default_value = (*color, 1.0)
    strength = input_value(principled, "Emission Strength")
    if strength:
        strength.default_value = 0.35
    return material


def flat_emission_material(name: str, color):
    """Unlit material for class masks; lighting must not alter its value."""
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*color, 1.0)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Color"].default_value = (*color, 1.0)
    emission.inputs["Strength"].default_value = 1.0
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def procedural_material(name: str, dark, light, scale: float, roughness: float, bump_strength: float):
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


def texture_material(name, spec, saturation, normal_strength, tile_width_m,
                     neutral_color, texture_weight, value=0.86,
                     macro_strength=0.0, macro_scale=0.10,
                     micro_color_strength=0.0, micro_scale=80.0,
                     micro_bump_strength=0.0):
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


def native_lawn_material(name="Native metric lawn"):
    """Multi-scale lawn appearance confined to the exact authored lawn mesh."""
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    macro = nodes.new("ShaderNodeTexNoise")
    macro.inputs["Scale"].default_value = 0.22
    macro.inputs["Detail"].default_value = 4.0
    macro.inputs["Roughness"].default_value = 0.68
    macro.inputs["Distortion"].default_value = 0.18
    macro_ramp = nodes.new("ShaderNodeValToRGB")
    macro_ramp.color_ramp.elements[0].position = 0.22
    macro_ramp.color_ramp.elements[0].color = (0.018, 0.050, 0.008, 1.0)
    macro_ramp.color_ramp.elements[1].position = 0.82
    macro_ramp.color_ramp.elements[1].color = (0.105, 0.225, 0.035, 1.0)
    mottling = nodes.new("ShaderNodeTexNoise")
    mottling.inputs["Scale"].default_value = 5.5
    mottling.inputs["Detail"].default_value = 4.0
    mottling.inputs["Roughness"].default_value = 0.78
    mottling_ramp = nodes.new("ShaderNodeValToRGB")
    mottling_ramp.color_ramp.elements[0].position = 0.18
    mottling_ramp.color_ramp.elements[0].color = (0.46, 0.48, 0.34, 1.0)
    mottling_ramp.color_ramp.elements[1].position = 0.84
    mottling_ramp.color_ramp.elements[1].color = (1.0, 1.0, 0.92, 1.0)
    color_mix = nodes.new("ShaderNodeMixRGB")
    color_mix.blend_type = "MULTIPLY"
    color_mix.inputs[0].default_value = 0.34
    micro = nodes.new("ShaderNodeTexNoise")
    micro.inputs["Scale"].default_value = 34.0
    micro.inputs["Detail"].default_value = 3.0
    micro.inputs["Roughness"].default_value = 0.75
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.22
    bump.inputs["Distance"].default_value = 0.018
    input_value(shader, "Roughness").default_value = 0.92
    links.new(coordinate.outputs["Object"], macro.inputs["Vector"])
    links.new(coordinate.outputs["Object"], mottling.inputs["Vector"])
    links.new(coordinate.outputs["Object"], micro.inputs["Vector"])
    links.new(macro.outputs["Fac"], macro_ramp.inputs["Fac"])
    links.new(mottling.outputs["Fac"], mottling_ramp.inputs["Fac"])
    links.new(macro_ramp.outputs["Color"], color_mix.inputs[1])
    links.new(mottling_ramp.outputs["Color"], color_mix.inputs[2])
    links.new(color_mix.outputs["Color"], input_value(shader, "Base Color"))
    links.new(micro.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], input_value(shader, "Normal"))
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def native_grass_material(name="Native lawn blade proxy"):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 2.7
    noise.inputs["Detail"].default_value = 2.0
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.012, 0.040, 0.004, 1.0)
    ramp.color_ramp.elements[1].color = (0.075, 0.18, 0.018, 1.0)
    input_value(shader, "Roughness").default_value = 0.88
    links.new(coordinate.outputs["Object"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], input_value(shader, "Base Color"))
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def add_native_lawn_blades(surface_rows, material, camera_xy=(0.0, 0.0), radius_m=58.0,
                           density_m2=10.0, max_clumps=42000):
    """Create deterministic micro-geometry only on admitted lawn triangles."""
    seed_text = ":".join(sorted(row["id"] for row in surface_rows if row.get("semantic") == "lawn"))
    seed = int(hashlib.sha256(seed_text.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    vertices = []
    faces = []
    clumps = 0
    radius_squared = radius_m * radius_m
    for row in surface_rows:
        if row.get("semantic") != "lawn":
            continue
        for triangle in row["triangles_scene_xyz_m"]:
            a, b, c = (Vector(tuple(map(float, point))) for point in triangle)
            centroid_x = (a.x + b.x + c.x) / 3.0
            centroid_y = (a.y + b.y + c.y) / 3.0
            if (centroid_x - camera_xy[0]) ** 2 + (centroid_y - camera_xy[1]) ** 2 > radius_squared:
                continue
            area_xy = abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)) * 0.5
            expected = area_xy * density_m2
            count = int(expected)
            if rng.random() < expected - count:
                count += 1
            for _ in range(count):
                if clumps >= max_clumps:
                    break
                u = math.sqrt(rng.random())
                v = rng.random()
                w0, w1, w2 = 1.0 - u, u * (1.0 - v), u * v
                point = a * w0 + b * w1 + c * w2
                height = 0.035 + rng.random() * 0.045
                half_width = 0.007 + rng.random() * 0.007
                angle = rng.random() * math.tau
                right = Vector((math.cos(angle), math.sin(angle), 0.0)) * half_width
                right_cross = Vector((-right.y, right.x, 0.0))
                bottom_z = point.z + 0.006
                top_z = bottom_z + height
                offset = len(vertices)
                vertices.extend([
                    (point.x - right.x, point.y - right.y, bottom_z),
                    (point.x + right.x, point.y + right.y, bottom_z),
                    (point.x + right.x * 0.18, point.y + right.y * 0.18, top_z),
                    (point.x - right.x * 0.18, point.y - right.y * 0.18, top_z),
                    (point.x - right_cross.x, point.y - right_cross.y, bottom_z),
                    (point.x + right_cross.x, point.y + right_cross.y, bottom_z),
                    (point.x + right_cross.x * 0.18, point.y + right_cross.y * 0.18, top_z),
                    (point.x - right_cross.x * 0.18, point.y - right_cross.y * 0.18, top_z),
                ])
                faces.extend(((offset, offset + 1, offset + 2, offset + 3),
                              (offset + 4, offset + 5, offset + 6, offset + 7)))
                clumps += 1
            if clumps >= max_clumps:
                break
    mesh = bpy.data.meshes.new("Native_lawn_blades_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new("Native_lawn_blades_exact_surface", mesh)
    bpy.context.collection.objects.link(obj)
    obj["semantic"] = "lawn_detail_proxy"
    obj["status"] = "deterministic grass micro-geometry clipped to exact authored lawn triangles"
    obj["seed"] = str(seed)
    obj["clump_count"] = clumps
    return clumps


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


def add_botaniq_lawn_instances(surface_rows, sources, camera_xy=(0.0, 0.0),
                               radius_m=85.0, spacing_m=0.56, max_instances=50000):
    """Scatter a stable Botaniq turf patch only inside admitted lawn triangles.

    The near-camera radius prevents distant sub-pixel blades from turning into
    temporal/high-frequency noise.  Outside it, the metric PBR lawn remains.
    """
    assets = []
    tuned_materials = []
    for asset_index, source in enumerate(sources):
        collection, native_height = load_collection_asset(
            source, f"Botaniq_lawn_patch_{asset_index}", "lawn_detail_proxy",
        )
        tuned_materials.extend(tune_botaniq_lawn_material(collection))
        assets.append((collection, native_height, source))
    seed_text = ":".join(sorted(row["id"] for row in surface_rows if row.get("semantic") == "lawn")) + ":botaniq"
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
            ix0 = math.ceil((min(a.x, b.x, c.x) - phase_x) / spacing_m)
            ix1 = math.floor((max(a.x, b.x, c.x) - phase_x) / spacing_m)
            iy0 = math.ceil((min(a.y, b.y, c.y) - phase_y) / spacing_m)
            iy1 = math.floor((max(a.y, b.y, c.y) - phase_y) / spacing_m)
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    cell = (ix, iy)
                    if cell in occupied_cells:
                        continue
                    cell_seed = seed ^ ((ix * 73856093) & 0xFFFFFFFFFFFFFFFF) ^ ((iy * 19349663) & 0xFFFFFFFFFFFFFFFF)
                    cell_rng = random.Random(cell_seed)
                    x = phase_x + ix * spacing_m
                    y = phase_y + iy * spacing_m
                    if (x - camera_xy[0]) ** 2 + (y - camera_xy[1]) ** 2 > radius_squared:
                        continue
                    w0 = ((b.y - c.y) * (x - c.x) + (c.x - b.x) * (y - c.y)) / denominator
                    w1 = ((c.y - a.y) * (x - c.x) + (a.x - c.x) * (y - c.y)) / denominator
                    w2 = 1.0 - w0 - w1
                    if min(w0, w1, w2) < -1e-7:
                        continue
                    jitter_x = x + (cell_rng.random() - 0.5) * spacing_m * 0.28
                    jitter_y = y + (cell_rng.random() - 0.5) * spacing_m * 0.28
                    jw0 = ((b.y - c.y) * (jitter_x - c.x) + (c.x - b.x) * (jitter_y - c.y)) / denominator
                    jw1 = ((c.y - a.y) * (jitter_x - c.x) + (a.x - c.x) * (jitter_y - c.y)) / denominator
                    jw2 = 1.0 - jw0 - jw1
                    if min(jw0, jw1, jw2) >= -1e-7:
                        x, y, w0, w1, w2 = jitter_x, jitter_y, jw0, jw1, jw2
                    occupied_cells.add(cell)
                    point = a * w0 + b * w1 + c * w2
                    if count >= max_instances:
                        break
                    asset_index = min(int(cell_rng.random() * len(assets)), len(assets) - 1)
                    collection, _, _ = assets[asset_index]
                    instance = bpy.data.objects.new(f"Botaniq_lawn_{count:04d}", None)
                    instance.instance_type = "COLLECTION"
                    instance.instance_collection = collection
                    instance.location = (point.x, point.y, point.z + 0.003)
                    horizontal_scale = 1.02 + cell_rng.random() * 0.10
                    vertical_scale = 0.62 + cell_rng.random() * 0.12
                    instance.scale = (horizontal_scale, horizontal_scale, vertical_scale)
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
            group = next((
                node for node in material.node_tree.nodes
                if node.type == "GROUP" and "Color Top" in node.inputs and "Fresh Value" in node.inputs
            ), None)
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
    ramp.color_ramp.elements[0].color = tuple(max(0.0, value * 0.84) for value in base) + (1.0,)
    ramp.color_ramp.elements[1].position = 0.85
    ramp.color_ramp.elements[1].color = tuple(min(1.0, value * 1.08) for value in base) + (1.0,)
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


def glass_material(name="Non-reflective glazing appearance proxy",
                   dark=(0.005, 0.009, 0.012), light=(0.018, 0.026, 0.030)):
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
    ramp.color_ramp.elements[0].color = (0.22, 0.55, 1.0, 1.0) if profile == "native_v1" else (0.32, 0.48, 0.66, 1.0)
    ramp.color_ramp.elements[1].position = 0.62 if profile == "native_v1" else 0.70
    ramp.color_ramp.elements[1].color = (0.90, 0.93, 0.96, 1.0) if profile == "native_v1" else (0.84, 0.85, 0.84, 1.0)
    emission.inputs["Strength"].default_value = 1.08 if profile == "native_v1" else 0.82
    links.new(coordinate.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def add_sky_backdrop(camera_position, target_position, material):
    camera = Vector(camera_position)
    target = Vector(target_position)
    forward = Vector((target.x - camera.x, target.y - camera.y, 0.0)).normalized()
    right = Vector((forward.y, -forward.x, 0.0))
    centre = camera + forward * 520.0 + Vector((0.0, 0.0, 125.0))
    half_width, bottom, top = 650.0, -135.0, 385.0
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
    obj["status"] = "deterministic appearance-only daylight preset; not observed weather"
    # This camera backdrop is not physical scene geometry or an area light.
    obj.visible_shadow = False
    obj.visible_diffuse = False
    obj.visible_glossy = False
    return obj


def cutout_material(name: str, path: Path, expected_sha256: str):
    if not path.is_file() or sha256(path) != expected_sha256:
        raise RuntimeError(f"Presentation cutout missing or changed: {path}")
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    try:
        material.surface_render_method = "DITHERED"
    except AttributeError:
        pass
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    transparent = nodes.new("ShaderNodeBsdfTransparent")
    emission = nodes.new("ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 0.78
    mix = nodes.new("ShaderNodeMixShader")
    texture = nodes.new("ShaderNodeTexImage")
    texture.image = bpy.data.images.load(str(path), check_existing=True)
    texture.interpolation = "Linear"
    links.new(texture.outputs["Color"], emission.inputs["Color"])
    links.new(texture.outputs["Alpha"], mix.inputs[0])
    links.new(transparent.outputs["BSDF"], mix.inputs[1])
    links.new(emission.outputs["Emission"], mix.inputs[2])
    links.new(mix.outputs["Shader"], output.inputs["Surface"])
    return material, int(texture.image.size[0]), int(texture.image.size[1])


def add_cutout_billboard(row, material, image_size, physical_width=None, physical_height=None,
                         camera_xy=(0.0, 0.0), semantic="presentation_object", ground_sink_m=0.0):
    image_width, image_height = image_size
    if physical_width is None:
        physical_width = float(physical_height) * image_width / image_height
    if physical_height is None:
        physical_height = float(physical_width) * image_height / image_width
    x, y, z = map(float, row["scene_xyz_m"])
    z -= float(ground_sink_m)
    vx, vy = camera_xy[0] - x, camera_xy[1] - y
    length = max(math.hypot(vx, vy), 1e-9)
    right_x, right_y = -vy / length, vx / length
    half_width = float(physical_width) * 0.5
    vertices = [
        (x - right_x * half_width, y - right_y * half_width, z),
        (x + right_x * half_width, y + right_y * half_width, z),
        (x + right_x * half_width, y + right_y * half_width, z + float(physical_height)),
        (x - right_x * half_width, y - right_y * half_width, z + float(physical_height)),
    ]
    mesh = bpy.data.meshes.new(f"{row['id']}_mesh")
    mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
    mesh.uv_layers.new(name="UVMap")
    for loop, uv in zip(mesh.uv_layers.active.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = uv
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new(row["id"], mesh)
    bpy.context.collection.objects.link(obj)
    obj["semantic"] = semantic
    obj["placement_status"] = row["placement_status"]
    obj["observed"] = False
    return obj


def mesh_from_triangles(name: str, triangles, material, properties=None):
    vertices = []
    faces = []
    for triangle in triangles:
        offset = len(vertices)
        vertices.extend(tuple(float(value) for value in point) for point in triangle)
        faces.append((offset, offset + 1, offset + 2))
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    for key, value in (properties or {}).items():
        obj[key] = value
    if obj.get("semantic") in {"marking", "road_marking"}:
        # Painted markings are a thin surface treatment.  Let them receive
        # illumination while preventing the physically impossible cast shadow
        # produced by the small anti-z-fighting lift above the road mesh.
        obj.visible_shadow = False
        obj["shadow_status"] = "disabled_for_painted_surface"
    return obj


def building_object(row, material):
    objects = []
    for ring_index, ring in enumerate(row["rings_scene_xyz_m"]):
        count = len(ring)
        base = [tuple(map(float, point)) for point in ring]
        top = [(x, y, z + float(row["height_m"])) for x, y, z in base]
        vertices = base + top
        faces = [tuple(reversed(range(count))), tuple(range(count, 2 * count))]
        faces.extend((index, (index + 1) % count, count + (index + 1) % count, count + index) for index in range(count))
        mesh = bpy.data.meshes.new(f"{row['id']}_{ring_index}_mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.materials.append(material)
        mesh.update()
        obj = bpy.data.objects.new(f"{row['id']}_{ring_index}", mesh)
        bpy.context.collection.objects.link(obj)
        obj["source_id"] = row["id"]
        obj["height_status"] = row["height_status"] or "unknown"
        obj["semantic"] = "building"
        bevel = obj.modifiers.new("bounded edge bevel", "BEVEL")
        bevel.width = 0.04 if row.get("class") == "retail" else 0.08
        bevel.segments = 2
        objects.append(obj)
    return objects


def add_building_joint_proxies(row, material):
    """Add deterministic shallow floor joints inside the known building envelope."""
    building_class = row.get("class")
    if building_class not in {"apartments", "retail"} or float(row["height_m"]) < 5.0:
        return 0
    vertices = []
    faces = []
    joint_count = 0
    for ring in row["rings_scene_xyz_m"]:
        centroid = Vector((
            sum(float(point[0]) for point in ring) / len(ring),
            sum(float(point[1]) for point in ring) / len(ring),
            0.0,
        ))
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
                vertices.extend(tuple(point) for point in (p0, p1, p2, p3, p0 + back, p1 + back, p2 + back, p3 + back))
                faces.extend((
                    (offset, offset + 1, offset + 2, offset + 3),
                    (offset, offset + 4, offset + 5, offset + 1),
                    (offset + 1, offset + 5, offset + 6, offset + 2),
                    (offset + 2, offset + 6, offset + 7, offset + 3),
                ))
                joint_count += 1
            if building_class == "retail":
                columns = max(2, int(length / 4.2))
                base_z = (start.z + end.z) * 0.5 + 0.45
                top_z = (start.z + end.z) * 0.5 + float(row["height_m"]) - 0.30
                for column in range(1, columns):
                    center = start + tangent * (length * column / columns) + outward * 0.042
                    half_width = 0.018
                    depth = 0.028
                    p0 = Vector((center.x - tangent.x * half_width, center.y - tangent.y * half_width, base_z))
                    p1 = Vector((center.x + tangent.x * half_width, center.y + tangent.y * half_width, base_z))
                    p2 = Vector((center.x + tangent.x * half_width, center.y + tangent.y * half_width, top_z))
                    p3 = Vector((center.x - tangent.x * half_width, center.y - tangent.y * half_width, top_z))
                    back = outward * -depth
                    offset = len(vertices)
                    vertices.extend(tuple(point) for point in (p0, p1, p2, p3, p0 + back, p1 + back, p2 + back, p3 + back))
                    faces.extend((
                        (offset, offset + 1, offset + 2, offset + 3),
                        (offset, offset + 4, offset + 5, offset + 1),
                        (offset + 1, offset + 5, offset + 6, offset + 2),
                        (offset + 2, offset + 6, offset + 7, offset + 3),
                    ))
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
    obj["status"] = "class-generic 3 m facade joint proxy; exact facade construction not surveyed"
    obj["source_id"] = row["id"]
    return joint_count


def add_retail_glazing(row, material, camera_xy=(0.0, 0.0), frame_material=None, native_depth=False):
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
        camera_side = dx * (camera_xy[1] - start[1]) - dy * (camera_xy[0] - start[0])
        centroid_side = dx * (centroid[1] - start[1]) - dy * (centroid[0] - start[0])
        if camera_side * centroid_side < 0:
            candidates.append((math.dist(midpoint, camera_xy), -math.hypot(dx, dy), index, start, end))
    # A convex footprint normally exposes two walls.  Treat both as a generic
    # retail-class facade; no panorama or chat reference selects their design.
    candidates = sorted(candidates)[:2]
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
            left = usable_start + (usable_end - usable_start) * (panel + 0.08) / panel_count
            right = usable_start + (usable_end - usable_start) * (panel + 0.92) / panel_count
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
                    (pane[0][0] - ux * frame_width, pane[0][1] - uy * frame_width, pane[0][2] - frame_width),
                    (pane[1][0] + ux * frame_width, pane[1][1] + uy * frame_width, pane[1][2] - frame_width),
                    (pane[2][0] + ux * frame_width, pane[2][1] + uy * frame_width, pane[2][2] + frame_width),
                    (pane[3][0] - ux * frame_width, pane[3][1] - uy * frame_width, pane[3][2] + frame_width),
                ]
                vertices = pane + outer
                faces.extend(((4, 5, 1, 0), (5, 6, 2, 1), (6, 7, 3, 2), (7, 4, 0, 3)))
            mesh = bpy.data.meshes.new(f"retail_glazing_{facade_index:02d}_{panel:02d}_mesh")
            mesh.from_pydata(vertices, [], faces)
            mesh.materials.append(material)
            if native_depth and frame_material:
                mesh.materials.append(frame_material)
                for polygon in mesh.polygons[1:]:
                    polygon.material_index = 1
            mesh.update()
            obj = bpy.data.objects.new(f"Retail_class_glazing_proxy_{facade_index:02d}_{panel:02d}", mesh)
            bpy.context.collection.objects.link(obj)
            obj["status"] = "class-generic non-reflective appearance proxy; exact openings not surveyed"
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


def add_context_windows(row, materials, camera_xy=(0.0, 0.0), frame_material=None, native_depth=False):
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
            edges.append((math.dist(midpoint, camera_xy), -length, start, end))
        for _, _, start, end in sorted(edges)[:2]:
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            columns = max(0, int(length / 3.45))
            floors = max(1, int(float(row["height_m"]) / 3.0) - 1)
            if columns == 0:
                continue
            nx, ny = -dy / length, dx / length
            midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if (camera_xy[0] - midpoint[0]) * nx + (camera_xy[1] - midpoint[1]) * ny < 0:
                nx, ny = -nx, -ny
            ux, uy = dx / length, dy / length
            for column in range(columns):
                # A stable blank bay breaks the synthetic all-window grid and
                # stands in for unsurveyed stair/service facade zones.
                blank_key = int(hashlib.sha256(f"{row['id']}:{column}:blank".encode()).hexdigest()[:8], 16)
                if columns >= 6 and blank_key % 11 == 0:
                    continue
                center_distance = (column + 0.5) / columns * length
                center_x = start[0] + ux * center_distance + nx * 0.035
                center_y = start[1] + uy * center_distance + ny * 0.035
                base_z = start[2] + (end[2] - start[2]) * ((column + 0.5) / columns)
                dimension_key = int(hashlib.sha256(f"{row['id']}:{column}:dimension".encode()).hexdigest()[:8], 16)
                width_factor = 0.48 + (dimension_key % 9) * 0.012
                width = min(1.35, length / columns * width_factor)
                for floor in range(floors):
                    center_z = base_z + 1.25 + floor * 3.0
                    height_factor = 0.54 + ((dimension_key >> 4) % 7) * 0.015
                    half_w, half_h = width / 2.0, height_factor
                    pane = [
                        (center_x - ux * half_w, center_y - uy * half_w, center_z - half_h),
                        (center_x + ux * half_w, center_y + uy * half_w, center_z - half_h),
                        (center_x + ux * half_w, center_y + uy * half_w, center_z + half_h),
                        (center_x - ux * half_w, center_y - uy * half_w, center_z + half_h),
                    ]
                    vertices = pane
                    faces = [(0, 1, 2, 3)]
                    if native_depth and frame_material:
                        frame_width = 0.085
                        outer = [
                            (center_x - ux * (half_w + frame_width), center_y - uy * (half_w + frame_width), center_z - half_h - frame_width),
                            (center_x + ux * (half_w + frame_width), center_y + uy * (half_w + frame_width), center_z - half_h - frame_width),
                            (center_x + ux * (half_w + frame_width), center_y + uy * (half_w + frame_width), center_z + half_h + frame_width),
                            (center_x - ux * (half_w + frame_width), center_y - uy * (half_w + frame_width), center_z + half_h + frame_width),
                        ]
                        vertices = pane + outer
                        faces.extend(((4, 5, 1, 0), (5, 6, 2, 1), (6, 7, 3, 2), (7, 4, 0, 3)))
                    mesh = bpy.data.meshes.new(f"context_window_{count:04d}_mesh")
                    mesh.from_pydata(vertices, [], faces)
                    variant_key = f"{row['id']}:{column}:{floor}".encode()
                    variant = int(hashlib.sha256(variant_key).hexdigest()[:8], 16) % len(materials)
                    mesh.materials.append(materials[variant])
                    if native_depth and frame_material:
                        mesh.materials.append(frame_material)
                        for polygon in mesh.polygons[1:]:
                            polygon.material_index = 1
                    mesh.update()
                    obj = bpy.data.objects.new(f"Context_window_{count:04d}", mesh)
                    bpy.context.collection.objects.link(obj)
                    obj["status"] = "procedural facade appearance proxy; opening positions not source geometry"
                    obj["semantic"] = "building_detail_proxy"
                    if native_depth:
                        solidify = obj.modifiers.new("window reveal depth proxy", "SOLIDIFY")
                        solidify.thickness = 0.045
                        solidify.offset = -0.5
                        bevel = obj.modifiers.new("window frame edge", "BEVEL")
                        bevel.width = 0.006
                        bevel.segments = 1
                    count += 1
    return count


def add_curb(row, material, index):
    start = Vector(row["start_scene_xyz_m"])
    end = Vector(row["end_scene_xyz_m"])
    delta = end - start
    length_xy = math.hypot(delta.x, delta.y)
    midpoint = (start + end) * 0.5
    height = float(row["height_m"])
    bpy.ops.mesh.primitive_cube_add(location=(midpoint.x, midpoint.y, midpoint.z + height / 2.0))
    obj = bpy.context.object
    obj.name = f"Curb_{index:03d}"
    obj.dimensions = (length_xy, float(row["width_m"]), height)
    obj.rotation_euler.z = math.atan2(delta.y, delta.x)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(material)
    obj["status"] = row["status"]
    obj["semantic"] = "curb"
    bevel = obj.modifiers.new("curb edge", "BEVEL")
    bevel.width = 0.015
    bevel.segments = 2
    return obj


def add_combined_curbs(rows, material):
    """Build metre-scale curb stones along source-backed curb chains.

    The input rows are exact flattened polyline segments, often only a few
    centimetres long on curves. Treating every row as a stone creates noise;
    merging everything creates a monolithic ribbon. Rows are therefore joined
    only while their source handle and endpoint continuity agree, then sampled
    into 0.8–1.0 m appearance blocks separated by a 14 mm joint.
    """
    chains = []
    active = None
    for row in rows:
        start = Vector(row["start_scene_xyz_m"])
        end = Vector(row["end_scene_xyz_m"])
        key = (
            row.get("source_handle"), row.get("curb_class"),
            round(float(row["width_m"]), 6), round(float(row["height_m"]), 6),
        )
        if active is not None and active["key"] == key and (active["points"][-1] - start).length <= 0.025:
            active["points"].append(end)
        elif active is not None and active["key"] == key and (active["points"][-1] - end).length <= 0.025:
            active["points"].append(start)
        else:
            active = {"key": key, "points": [start, end], "row": row}
            chains.append(active)

    vertices = []
    faces = []
    stone_count = 0
    for chain in chains:
        points = chain["points"]
        row = chain["row"]
        cumulative = [0.0]
        for start, end in zip(points, points[1:]):
            cumulative.append(cumulative[-1] + math.hypot(end.x - start.x, end.y - start.y))
        total_length = cumulative[-1]
        if total_length <= 1e-5:
            continue

        def point_at(station):
            station = min(max(station, 0.0), total_length)
            segment_index = 0
            while segment_index + 1 < len(cumulative) and cumulative[segment_index + 1] < station:
                segment_index += 1
            segment_length = max(cumulative[segment_index + 1] - cumulative[segment_index], 1e-9)
            ratio = (station - cumulative[segment_index]) / segment_length
            return points[segment_index].lerp(points[segment_index + 1], ratio)

        pieces = max(1, math.ceil(total_length / 1.0))
        block_length = total_length / pieces
        joint_gap = 0.014 if pieces > 1 else 0.0
        half_width = float(row["width_m"]) * 0.5
        height = float(row["height_m"])
        for piece in range(pieces):
            station_a = piece * block_length + joint_gap * 0.5
            station_b = (piece + 1) * block_length - joint_gap * 0.5
            start = point_at(station_a)
            end = point_at(station_b)
            delta = end - start
            length = math.hypot(delta.x, delta.y)
            if length <= 1e-5:
                continue
            normal = Vector((-delta.y / length, delta.x / length, 0.0))
            bottom = [
                start + normal * half_width, start - normal * half_width,
                end - normal * half_width, end + normal * half_width,
            ]
            top = [point + Vector((0.0, 0.0, height)) for point in bottom]
            offset = len(vertices)
            vertices.extend(tuple(point) for point in bottom + top)
            faces.extend([
                (offset, offset + 1, offset + 2, offset + 3),
                (offset + 4, offset + 7, offset + 6, offset + 5),
                (offset, offset + 4, offset + 5, offset + 1),
                (offset + 1, offset + 5, offset + 6, offset + 2),
                (offset + 2, offset + 6, offset + 7, offset + 3),
                (offset + 3, offset + 7, offset + 4, offset),
            ])
            stone_count += 1
    mesh = bpy.data.meshes.new("Project_curbs_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new("Project_curbs_exact_XY", mesh)
    bpy.context.collection.objects.link(obj)
    obj["semantic"] = "curb"
    obj["status"] = "source-backed curb chains; class dimensions and metre-stone joints are appearance proxies"
    obj["stone_count"] = stone_count
    obj["joint_gap_m"] = 0.014
    bevel = obj.modifiers.new("curb edge", "BEVEL")
    bevel.width = 0.010
    bevel.segments = 2
    return obj


def add_polyline(name, points, width, material, semantic, status):
    curve = bpy.data.curves.new(f"{name}_curve", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = width / 2.0
    curve.bevel_resolution = 1
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for target, source in zip(spline.points, points):
        target.co = (*map(float, source), 1.0)
    obj = bpy.data.objects.new(name, curve)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(material)
    obj["semantic"] = semantic
    obj["status"] = status
    return obj


def add_road_marking_ribbon(row, material, index):
    points = [Vector(point) for point in row["points_scene_xyz_m"]]
    if len(points) != 2:
        return add_polyline(
            f"Road_marking_{index:02d}", row["points_scene_xyz_m"], float(row["width_m"]),
            material, "road_marking", row["status"],
        )
    start, end = points
    delta = end - start
    length = math.hypot(delta.x, delta.y)
    if length <= 1e-6:
        return None
    normal = Vector((-delta.y / length, delta.x / length, 0.0)) * (float(row["width_m"]) / 2.0)
    lift = Vector((0.0, 0.0, 0.004))
    vertices = [tuple(start + normal + lift), tuple(end + normal + lift), tuple(end - normal + lift), tuple(start - normal + lift)]
    mesh = bpy.data.meshes.new(f"Road_marking_{index:02d}_mesh")
    mesh.from_pydata(vertices, [], [(0, 1, 2, 3)])
    mesh.materials.append(material)
    mesh.update()
    obj = bpy.data.objects.new(f"Road_marking_{index:02d}", mesh)
    bpy.context.collection.objects.link(obj)
    obj["semantic"] = "road_marking"
    obj["status"] = row["status"]
    obj["source_id"] = row.get("source_id", "unknown")
    obj.visible_shadow = False
    obj["shadow_status"] = "disabled_for_painted_surface"
    return obj


def add_box(name, location, dimensions, material, semantic, status, rotation_z=0.0):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.dimensions = dimensions
    obj.rotation_euler.z = rotation_z
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(material)
    obj["semantic"] = semantic
    obj["status"] = status or "cartographic_position_with_render_dimensions_proxy"
    return obj


def add_cylinder(name, location, radius, depth, material, semantic, status):
    bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(material)
    obj["semantic"] = semantic
    obj["status"] = status or "cartographic_position_with_render_dimensions_proxy"
    return obj


def rotated_offset(x, y, rotation):
    return x * math.cos(rotation) - y * math.sin(rotation), x * math.sin(rotation) + y * math.cos(rotation)


def add_project_bench(row, materials):
    x, y, z = map(float, row["anchor_scene_xyz_m"])
    rotation = float(row.get("rotation_rad") or 0.0)
    status = row.get("dimensions_status")
    add_box(row["id"] + "_seat", (x, y, z + 0.48), (1.80, 0.48, 0.10), materials["bench"], "street_furniture", status, rotation)
    back_dx, back_dy = rotated_offset(0.0, 0.22, rotation)
    add_box(row["id"] + "_back", (x + back_dx, y + back_dy, z + 0.78), (1.80, 0.08, 0.55), materials["bench"], "street_furniture", status, rotation)
    for index, local_x in enumerate((-0.62, 0.62)):
        dx, dy = rotated_offset(local_x, 0.0, rotation)
        add_box(row["id"] + f"_leg_{index}", (x + dx, y + dy, z + 0.24), (0.08, 0.38, 0.48), materials["street_furniture"], "street_furniture", status, rotation)


def add_transit_shelter(row, materials):
    x, y, z = map(float, row["anchor_scene_xyz_m"])
    rotation = float(row.get("rotation_rad") or 0.0)
    status = row.get("dimensions_status")
    add_box(row["id"] + "_roof", (x, y, z + 2.38), (4.2, 1.55, 0.12), materials["street_furniture"], "street_furniture", status, rotation)
    back_dx, back_dy = rotated_offset(0.0, 0.70, rotation)
    add_box(row["id"] + "_glass", (x + back_dx, y + back_dy, z + 1.25), (3.95, 0.035, 2.15), materials["shelter_glass"], "street_furniture", status, rotation)
    for index, (local_x, local_y) in enumerate(((-1.9, -0.65), (-1.9, 0.65), (1.9, -0.65), (1.9, 0.65))):
        dx, dy = rotated_offset(local_x, local_y, rotation)
        add_cylinder(row["id"] + f"_post_{index}", (x + dx, y + dy, z + 1.18), 0.045, 2.30, materials["street_furniture"], "street_furniture", status)


def select_tree_asset(row):
    source_species = row.get("source_species")
    asset_key = SOURCE_SPECIES_ASSET_KEY.get(source_species, row["asset_species"])
    candidates = TREE_ASSETS.get(asset_key) or TREE_ASSETS[row["asset_species"]]
    stable_hash = int(hashlib.sha256(row["id"].encode()).hexdigest()[:8], 16)
    source = candidates[stable_hash % len(candidates)]
    match_status = (
        "documented_morphology_surrogate"
        if source_species in SURROGATE_SOURCE_SPECIES
        else "source_inventory_species_or_genus_matched"
    )
    return asset_key, source, match_status


def load_tree_collection(asset_key: str, source: Path):
    collection, height = load_collection_asset(
        source,
        f"Botaniq_{asset_key}_{source.stem}",
        "vegetation",
    )
    return collection, height, source


def repair_botaniq_textures():
    texture_index = {path.name: path for path in (BOTANIQ / "textures").rglob("*") if path.is_file()}
    repaired = 0
    for image in bpy.data.images:
        if image.source != "FILE" or image.packed_file:
            continue
        filename = Path(image.filepath.replace("\\", "/")).name
        replacement = texture_index.get(filename)
        if replacement:
            image.filepath = str(replacement)
            try:
                image.reload()
                repaired += 1
            except RuntimeError:
                pass
    return repaired


def look_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def configure_render_backend(scene, args):
    """Select a reproducible native renderer and return its device receipt."""
    if args.engine == "eevee":
        scene.render.engine = "BLENDER_EEVEE_NEXT"
        try:
            scene.render.image_settings.color_mode = "RGBA"
        except AttributeError:
            pass
        return {"requested": "eevee", "engine": scene.render.engine, "device": "GPU/default"}

    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = args.adaptive_threshold
    scene.cycles.use_denoising = True
    scene.cycles.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    scene.cycles.max_bounces = 8
    scene.cycles.diffuse_bounces = 3
    scene.cycles.glossy_bounces = 3
    scene.cycles.transparent_max_bounces = 12
    receipt = {"requested": "cycles", "engine": scene.render.engine, "device": "CPU", "devices": []}
    try:
        preferences = bpy.context.preferences.addons["cycles"].preferences
        preferences.compute_device_type = "METAL"
        preferences.get_devices()
        devices = [
            {"name": item.name, "type": item.type, "use": item.type == "METAL"}
            for item in preferences.devices
        ]
        for item in preferences.devices:
            item.use = item.type == "METAL"
        if any(item["type"] == "METAL" for item in devices):
            scene.cycles.device = "GPU"
            receipt["device"] = "METAL"
        receipt["devices"] = devices
    except Exception as error:
        receipt["fallback_reason"] = str(error)
    return receipt


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    packet = json.loads(args.packet.read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    render_backend = configure_render_backend(scene, args)
    beauty_engine = scene.render.engine
    scene.render.resolution_x = args.resolution_x
    scene.render.resolution_y = args.resolution_y
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = False
    scene.render.use_file_extension = True
    scene.render.filepath = str((args.output / "street-geometry-prototype.png").resolve())
    scene.render.resolution_percentage = 100
    scene.view_settings.look = "AgX - Medium High Contrast" if args.appearance_profile == "native_v1" else "AgX - Medium Low Contrast"
    scene.view_settings.exposure = 0.08 if args.appearance_profile == "native_v1" else 0.05
    if beauty_engine == "BLENDER_EEVEE_NEXT":
        try:
            scene.eevee.taa_render_samples = args.samples
        except AttributeError:
            pass

    materials = {
        "context": procedural_material("Unresolved context ground", (0.18, 0.21, 0.15), (0.23, 0.26, 0.18), 0.16, 0.94, 0.015),
        "road": procedural_material("Low-frequency asphalt", (0.048, 0.052, 0.056), (0.075, 0.080, 0.084), 5.2, 0.84, 0.022),
        "parking_apron": procedural_material("Parking asphalt", (0.062, 0.064, 0.066), (0.09, 0.094, 0.098), 4.4, 0.88, 0.018),
        "sidewalk": procedural_material("Concrete paving", (0.31, 0.30, 0.285), (0.39, 0.38, 0.355), 2.8, 0.90, 0.014),
        "lawn": procedural_material("Lawn base", (0.045, 0.115, 0.025), (0.105, 0.225, 0.050), 0.32, 0.97, 0.010),
        "curb": simple_material("Concrete curb", (0.47, 0.465, 0.45), 0.91),
        "retail": procedural_material("Ordinary maintained commercial facade", (0.24, 0.24, 0.23), (0.49, 0.47, 0.435), 0.11, 0.93, 0.003),
        "background": procedural_material("Ordinary occupied context facade", (0.27, 0.285, 0.28), (0.59, 0.575, 0.54), 0.085, 0.94, 0.003),
        "glass": glass_material("Retail non-reflective glazing proxy"),
        "window_variants": [
            glass_material("Context window dark blue-grey", (0.018, 0.029, 0.033), (0.058, 0.074, 0.079)),
            glass_material("Context window muted grey", (0.030, 0.033, 0.034), (0.080, 0.083, 0.081)),
            glass_material("Context window shaded warm", (0.045, 0.040, 0.031), (0.105, 0.092, 0.068)),
            glass_material("Context window dim neutral", (0.021, 0.025, 0.026), (0.065, 0.068, 0.067)),
        ],
        "window_frame": simple_material("Window frame proxy", (0.20, 0.205, 0.20), 0.72, 0.03),
        "facade_joint": simple_material("Facade joint proxy", (0.20, 0.205, 0.195), 0.92),
        "marking": simple_material("Road marking paint", (0.76, 0.77, 0.72), 0.68),
        "context_road": procedural_material("Automatic context road", (0.055, 0.058, 0.061), (0.078, 0.082, 0.086), 4.2, 0.87, 0.015),
        "context_sidewalk": procedural_material("Automatic context footway", (0.30, 0.295, 0.28), (0.37, 0.36, 0.34), 2.4, 0.91, 0.012),
        "context_wood": simple_material("OSM woodland ground", (0.075, 0.18, 0.065), 0.97),
        "context_playground": simple_material("OSM playground", (0.34, 0.20, 0.10), 0.86),
        "context_pitch": simple_material("OSM pitch", (0.12, 0.28, 0.11), 0.95),
        "context_green": simple_material("OSM green area", (0.11, 0.24, 0.09), 0.96),
        "street_furniture": simple_material("OSM street furniture proxy", (0.095, 0.11, 0.12), 0.68, 0.08),
        "bench": simple_material("OSM bench proxy", (0.23, 0.10, 0.045), 0.78),
        "fence": simple_material("OSM fence proxy", (0.12, 0.14, 0.14), 0.74, 0.12),
        "shelter_glass": glass_material("Shelter non-reflective glazing proxy"),
    }
    native_appearance_assets = []
    if args.appearance_profile == "native_v1":
        materials["lawn"] = texture_material(
            "PBR lawn / ambientCG Grass005",
            NATIVE_LAWN_SPEC,
            saturation=1.10,
            normal_strength=0.30,
            tile_width_m=2.2,
            neutral_color=(0.045, 0.115, 0.020),
            texture_weight=0.88,
            value=0.74,
            macro_strength=0.08,
            macro_scale=0.10,
        )
        native_appearance_assets.append({
            "id": NATIVE_LAWN_SPEC["id"],
            "source_url": NATIVE_LAWN_SPEC["source_url"],
            "license": NATIVE_LAWN_SPEC["license"],
            "maps": {
                role: {"path": row["path"], "sha256": sha256(Path(row["path"]))}
                for role, row in NATIVE_LAWN_SPEC["maps"].items()
            },
        })
        native_appearance_assets.append({
            "id": "polyhaven.kloofendal_48d_partly_cloudy.1k-hdr",
            "source_url": "https://polyhaven.com/a/kloofendal_48d_partly_cloudy",
            "license": "CC0",
            "path": str(NATIVE_HDRI.relative_to(ROOT)),
            "sha256": sha256(NATIVE_HDRI),
            "use": "image-based lighting only; camera background remains the deterministic sky preset",
        })
        materials["grass_blade"] = native_grass_material()
        materials["retail"] = native_facade_material(
            "Native maintained retail facade", (0.43, 0.42, 0.40), 3.2,
        )
        materials["background"] = native_facade_material(
            "Native occupied context facade", (0.46, 0.46, 0.44), 3.0,
        )
        materials["curb"] = simple_material("Native curb concrete", (0.30, 0.295, 0.28), 0.86)
    if packet.get("material_registry"):
        native_profile = args.appearance_profile == "native_v1"
        if native_profile:
            materials["road"] = native_asphalt_material(
                "Native bounded PBR asphalt / Poly Haven asphalt_01",
                packet["material_registry"]["road"],
            )
        else:
            materials["road"] = texture_material(
                "PBR asphalt / Poly Haven asphalt_01",
                packet["material_registry"]["road"],
                saturation=0.18,
                normal_strength=0.025,
                tile_width_m=5.5,
                neutral_color=(0.105, 0.110, 0.116),
                texture_weight=0.28,
                value=0.90,
            )
        materials["sidewalk"] = texture_material(
            "PBR concrete / Poly Haven concrete_floor_02",
            packet["material_registry"]["sidewalk"],
            saturation=0.22 if native_profile else 0.18,
            normal_strength=0.10 if native_profile else 0.025,
            tile_width_m=2.8 if native_profile else 4.8,
            neutral_color=(0.285, 0.28, 0.265) if native_profile else (0.40, 0.395, 0.38),
            texture_weight=0.46 if native_profile else 0.24,
            value=0.82 if native_profile else 0.92,
            macro_strength=0.16 if native_profile else 0.0,
            macro_scale=0.15,
            micro_color_strength=0.04 if native_profile else 0.0,
            micro_scale=28.0,
            micro_bump_strength=0.035 if native_profile else 0.0,
        )
        if native_profile:
            materials["curb"] = texture_material(
                "Native PBR curb concrete",
                packet["material_registry"]["sidewalk"],
                saturation=0.14,
                normal_strength=0.08,
                tile_width_m=2.2,
                neutral_color=(0.32, 0.315, 0.30),
                texture_weight=0.38,
                value=0.82,
                macro_strength=0.12,
                macro_scale=0.18,
            )

    mesh_from_triangles("Context_grade", packet["context_ground"]["triangles_scene_xyz_m"], materials["context"], {"status": packet["context_ground"]["status"]})
    rendered_project_marking_surfaces = 0
    hidden_project_marking_surfaces = 0
    for row in packet["surfaces"]:
        visibility = row.get("scenario_visibility")
        if visibility and args.scene_state not in visibility:
            if row.get("semantic") == "marking":
                hidden_project_marking_surfaces += 1
            continue
        mesh_from_triangles(
            row["id"].replace(":", "_"),
            row["triangles_scene_xyz_m"],
            materials[row["semantic"]],
            {"semantic": row["semantic"], "source_id": row["id"], "review_status": row["review"]["status"]},
        )
        if row.get("semantic") == "marking" and row.get("authority") == "authored_project_dxf":
            rendered_project_marking_surfaces += 1
    lawn_blade_clumps = 0
    botaniq_lawn_receipt = None
    if args.appearance_profile == "native_v1" and args.botaniq_lawn:
        lawn_blade_clumps, lawn_assets, lawn_seed, tuned_lawn_materials = add_botaniq_lawn_instances(
            packet["surfaces"],
            BOTANIQ_LAWN_ASSETS,
            camera_xy=tuple(packet["camera"]["position_scene_xyz_m"][:2]),
        )
        botaniq_lawn_receipt = {
            "id": "botaniq.controlled_mown_lawn_mix",
            "assets": [
                {
                    "path": str(path.relative_to(ROOT)),
                    "sha256": sha256(path),
                    "native_height_m": native_height,
                }
                for _, native_height, path in lawn_assets
            ],
            "instance_count": lawn_blade_clumps,
            "seed": str(lawn_seed),
            "tuned_materials": tuned_lawn_materials,
            "placement": "deterministic 0.56 m overlapping grid clipped to exact authored lawn triangles within 85 m of camera",
        }
        native_appearance_assets.append(botaniq_lawn_receipt)
    if args.appearance_profile == "native_v1" and args.lawn_microgeometry:
        lawn_blade_clumps = add_native_lawn_blades(
            packet["surfaces"], materials["grass_blade"], camera_xy=(0.0, 0.0),
        )
    for row in packet.get("automatic_context_surfaces", []):
        mesh_from_triangles(
            row["id"].replace(":", "_"), row["triangles_scene_xyz_m"], materials[row["semantic"]],
            {"semantic": row["semantic"], "source_id": row["id"], "geometry_status": row["geometry_status"] or "unknown"},
        )
    if packet["curbs"]:
        add_combined_curbs(packet["curbs"], materials["curb"])
    for index, row in enumerate(packet.get("road_markings", [])):
        add_road_marking_ribbon(row, materials["marking"], index)

    rendered_infrastructure = 0
    for row in packet.get("automatic_infrastructure", []):
        cls = row["class"]
        x, y, z = map(float, row["anchor_scene_xyz_m"])
        status = row.get("dimensions_status")
        safe_name = row["id"].replace(":", "_")
        if cls == "bench":
            if row.get("placement_status", "").startswith("exact_transformed_project"):
                add_project_bench(row, materials)
            else:
                rotation = 0.0
                line = row.get("line_scene_xyz_m")
                if line and len(line) >= 2:
                    rotation = math.atan2(line[-1][1] - line[0][1], line[-1][0] - line[0][0])
                add_box(safe_name, (x, y, z + 0.32), (1.65, 0.52, 0.64), materials["bench"], "street_furniture", status, rotation)
            rendered_infrastructure += 1
        elif cls in {"waste_basket", "waste_disposal", "recycling"}:
            add_cylinder(safe_name, (x, y, z + 0.42), 0.24, 0.84, materials["street_furniture"], "street_furniture", status)
            rendered_infrastructure += 1
        elif cls in {"bus_stop", "stop_position"}:
            add_cylinder(safe_name, (x, y, z + 1.15), 0.055, 2.30, materials["street_furniture"], "street_furniture", status)
            add_box(f"{safe_name}_sign", (x, y, z + 2.05), (0.42, 0.08, 0.42), materials["street_furniture"], "street_furniture", status)
            rendered_infrastructure += 1
        elif cls == "transit_shelter":
            add_transit_shelter(row, materials)
            rendered_infrastructure += 1
        elif cls == "fence" and row.get("line_scene_xyz_m"):
            for rail_index, rail_height in enumerate((0.35, 0.90, 1.45)):
                points = [[point[0], point[1], point[2] + rail_height] for point in row["line_scene_xyz_m"]]
                add_polyline(f"{safe_name}_rail_{rail_index}", points, 0.035, materials["fence"], "barrier", status)
            rendered_infrastructure += 1
        elif cls in {"gate", "entrance"}:
            add_cylinder(safe_name, (x, y, z + 0.75), 0.06, 1.5, materials["fence"], "barrier", status)
            rendered_infrastructure += 1

    window_count = 0
    retail_glazing_count = 0
    facade_joint_count = 0
    allow_building_detail_proxies = packet.get("admission", {}).get("building_detail_proxies", True)
    native_depth = args.appearance_profile == "native_v1"
    for row in packet["buildings"]:
        building_object(row, materials["retail"] if row.get("class") == "retail" else materials["background"])
        if native_depth:
            facade_joint_count += add_building_joint_proxies(row, materials["facade_joint"])
        if allow_building_detail_proxies:
            window_count += add_context_windows(
                row,
                materials["window_variants"],
                frame_material=materials["window_frame"],
                native_depth=native_depth,
            )
            if row.get("class") == "retail":
                retail_glazing_count += len(add_retail_glazing(
                    row,
                    materials["glass"],
                    frame_material=materials["window_frame"],
                    native_depth=native_depth,
                ) or [])

    loaded = {}
    asset_receipt = []
    for row in packet["vegetation"]:
        asset_key, path, match_status = select_tree_asset(row)
        cache_key = str(path)
        row["render_asset_key"] = asset_key
        row["render_asset_path"] = cache_key
        row["render_asset_match_status"] = match_status
        if cache_key not in loaded:
            collection, native_height, path = load_tree_collection(asset_key, path)
            loaded[cache_key] = (collection, native_height)
            asset_receipt.append({
                "asset_key": asset_key,
                "path": str(path.relative_to(ROOT)),
                "sha256": sha256(path),
                "native_height_m": native_height,
            })
    repaired_textures = repair_botaniq_textures()
    for row in packet["vegetation"]:
        collection, native_height = loaded[row["render_asset_path"]]
        obj = bpy.data.objects.new(row["id"], None)
        obj.instance_type = "COLLECTION"
        obj.instance_collection = collection
        obj["semantic"] = "vegetation"
        bpy.context.collection.objects.link(obj)
        obj.location = tuple(row["scene_xyz_m"])
        scale = float(row["height_m"]) / native_height
        obj.scale = (scale, scale, scale)
        # Stable ID-derived rotation prevents repeated visual coincidence.
        angle = int(hashlib.sha256(row["id"].encode()).hexdigest()[:8], 16) / 0xFFFFFFFF * math.tau
        obj.rotation_euler.z = angle
        obj["source_id"] = row["id"]
        obj["source_species"] = row.get("source_species") or "unknown"
        obj["render_asset_key"] = row["render_asset_key"]
        obj["asset_match_status"] = row["render_asset_match_status"]
        obj["position_status"] = row["position_status"]
        obj["height_status"] = row["height_status"]

    presentation_counts = {"pedestrian": 0, "passenger_car": 0}
    presentation_asset_receipt = {}
    population = packet.get("presentation_population", {})
    if args.presentation_population:
        for asset_class, specification in population.get("assets", {}).items():
            material, image_width, image_height = cutout_material(
                f"Presentation cutout {asset_class}",
                Path(specification["path"]),
                specification["sha256"],
            )
            presentation_asset_receipt[asset_class] = {
                "path": specification["path"],
                "sha256": specification["sha256"],
                "source": specification["source"],
            }
            for row in population.get("anchors", {}).get(asset_class, []):
                if asset_class == "pedestrian":
                    add_cutout_billboard(
                        row, material, (image_width, image_height), physical_height=1.74,
                        semantic="presentation_pedestrian",
                        ground_sink_m=0.025,
                    )
                elif asset_class == "passenger_car":
                    add_cutout_billboard(
                        row, material, (image_width, image_height), physical_width=4.45,
                        semantic="presentation_vehicle",
                        ground_sink_m=0.16,
                    )
                else:
                    continue
                presentation_counts[asset_class] += 1

    world = scene.world or bpy.data.worlds.new("Natural daylight")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    if args.appearance_profile == "native_v1":
        if not NATIVE_HDRI.is_file() or sha256(NATIVE_HDRI) != NATIVE_HDRI_SHA256:
            raise RuntimeError(f"Native daylight HDRI is missing or changed: {NATIVE_HDRI}")
        coordinate = nodes.new("ShaderNodeTexCoord")
        mapping = nodes.new("ShaderNodeMapping")
        mapping.inputs["Rotation"].default_value[2] = math.radians(24.0)
        environment = nodes.new("ShaderNodeTexEnvironment")
        environment.image = bpy.data.images.load(str(NATIVE_HDRI), check_existing=True)
        environment.image.colorspace_settings.name = "Non-Color"
        background.inputs["Strength"].default_value = 0.72
        links.new(coordinate.outputs["Generated"], mapping.inputs["Vector"])
        links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
        links.new(environment.outputs["Color"], background.inputs["Color"])
    else:
        sky = nodes.new("ShaderNodeTexSky")
        sky.sky_type = "NISHITA"
        sky.sun_elevation = math.radians(packet["lighting"]["sun_elevation_deg"])
        sky.sun_rotation = math.radians(packet["lighting"]["sun_azimuth_deg"])
        sky.altitude = 180.0
        background.inputs["Strength"].default_value = 0.40
        links.new(sky.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])

    sun_data = bpy.data.lights.new("Locked clear-day sun", type="SUN")
    sun_data.energy = 0.80 if args.appearance_profile == "native_v1" else 2.35
    sun_data.angle = math.radians(4.5 if args.appearance_profile == "native_v1" else 5.5)
    sun = bpy.data.objects.new("Locked clear-day sun", sun_data)
    bpy.context.collection.objects.link(sun)
    elevation = math.radians(packet["lighting"]["sun_elevation_deg"])
    azimuth = math.radians(packet["lighting"]["sun_azimuth_deg"])
    sun.rotation_euler = (math.pi / 2.0 - elevation, 0.0, azimuth)

    camera_data = bpy.data.cameras.new("KartaView source camera")
    camera = bpy.data.objects.new("KartaView source camera", camera_data)
    bpy.context.collection.objects.link(camera)
    scene.camera = camera
    camera.location = tuple(packet["camera"]["position_scene_xyz_m"])
    camera.data.lens = float(packet["camera"]["lens_mm_proxy"])
    camera.data.sensor_width = float(packet["camera"]["sensor_width_mm"])
    camera.data.dof.use_dof = False
    camera.data.clip_start = 0.08
    camera.data.clip_end = 800.0
    look_at(camera, packet["camera"]["target_scene_xyz_m"])
    add_sky_backdrop(
        packet["camera"]["position_scene_xyz_m"],
        packet["camera"]["target_scene_xyz_m"],
        sky_backdrop_material(args.appearance_profile),
    )

    blend_path = args.output / "street-geometry-prototype.blend"
    if not args.skip_blend_save:
        bpy.ops.wm.save_as_mainfile(filepath=str(blend_path.resolve()))
    bpy.ops.render.render(write_still=True)
    image_path = args.output / "street-geometry-prototype.png"
    image_hash = sha256(image_path)

    if args.skip_diagnostics:
        receipt = {
            "schema": "green-atlas.nspd-street-render-receipt.v1",
            "status": "geometry_material_prototype_not_beauty_admitted",
            "scene_state": args.scene_state,
            "input": {"path": str(args.packet.resolve()), "sha256": sha256(args.packet)},
            "output": {"image": str(image_path.resolve()), "blend": None if args.skip_blend_save else str(blend_path.resolve()), "image_sha256": image_hash},
            "engine": beauty_engine,
            "render_backend": render_backend,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
            "samples_requested": args.samples,
            "adaptive_threshold": args.adaptive_threshold if beauty_engine == "CYCLES" else None,
            "appearance_profile": args.appearance_profile,
            "native_appearance_assets": native_appearance_assets,
            "camera": packet["camera"],
            "lighting": packet["lighting"],
            "assets": asset_receipt,
            "repaired_texture_images": repaired_textures,
            "counts": {"surfaces": len(packet["surfaces"]), "automatic_context_surfaces": len(packet.get("automatic_context_surfaces", [])), "curbs": len(packet["curbs"]), "road_markings": len(packet.get("road_markings", [])), "project_marking_surfaces_rendered": rendered_project_marking_surfaces, "project_marking_surfaces_hidden": hidden_project_marking_surfaces, "buildings": len(packet["buildings"]), "vegetation": len(packet["vegetation"]), "lawn_blade_clumps": lawn_blade_clumps, "facade_joint_proxies": facade_joint_count, "automatic_infrastructure_source": len(packet.get("automatic_infrastructure", [])), "automatic_infrastructure_rendered": rendered_infrastructure, "context_window_proxies": window_count, "retail_glazing_proxies": retail_glazing_count},
            "presentation_population_rendered": args.presentation_population,
            "presentation_population_counts": presentation_counts,
            "diagnostics_rendered": False,
            "neural_finish": packet["neural_finish"],
        }
        (args.output / "render-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return

    semantic_colors = {
        "context": (0.58, 0.14, 0.48),
        "road": (0.10, 0.11, 0.12),
        "parking_apron": (0.95, 0.62, 0.04),
        "sidewalk": (0.05, 0.65, 0.95),
        "lawn": (0.16, 0.70, 0.18),
        "lawn_detail_proxy": (0.16, 0.70, 0.18),
        "curb": (0.92, 0.92, 0.86),
        "building": (0.25, 0.38, 0.95),
        "building_detail_proxy": (0.10, 0.16, 0.38),
        "vegetation": (0.08, 0.72, 0.26),
        "road_marking": (0.95, 0.95, 0.82),
        "marking": (0.95, 0.95, 0.82),
        "context_road": (0.16, 0.17, 0.18),
        "context_sidewalk": (0.30, 0.70, 0.92),
        "context_wood": (0.08, 0.36, 0.10),
        "context_playground": (0.80, 0.42, 0.10),
        "context_pitch": (0.10, 0.56, 0.16),
        "context_green": (0.12, 0.46, 0.14),
        "street_furniture": (0.65, 0.24, 0.78),
        "barrier": (0.58, 0.36, 0.18),
        "sky_preset": (0.25, 0.55, 0.85),
        "presentation_pedestrian": (0.95, 0.18, 0.62),
        "presentation_vehicle": (0.92, 0.25, 0.16),
    }
    semantic_materials = {key: semantic_material(f"Semantic_{key}", color) for key, color in semantic_colors.items()}
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.data.materials:
            continue
        semantic = obj.get("semantic")
        if obj.name == "Context_grade":
            semantic = "context"
        if semantic in semantic_materials:
            obj.data.materials.clear()
            obj.data.materials.append(semantic_materials[semantic])
    semantic_path = args.output / "street-semantic-diagnostic.png"
    scene.render.filepath = str(semantic_path.resolve())
    scene.view_settings.look = "AgX - Medium Low Contrast"
    scene.view_settings.exposure = 0.0
    bpy.ops.render.render(write_still=True)
    mask_paths = {}
    white_mask = flat_emission_material("Mask white", (1.0, 1.0, 1.0))
    black_mask = flat_emission_material("Mask black", (0.0, 0.0, 0.0))
    # The beauty sky is linked into the Background color socket, so changing
    # its default value alone does nothing.  Disconnect it for mask passes.
    for link in list(background.inputs["Color"].links):
        links.remove(link)
    background.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    background.inputs["Strength"].default_value = 1.0
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0

    def render_class_mask(name: str, target_semantics: set[str]) -> None:
        for candidate in bpy.data.objects:
            if candidate.type != "MESH" or not candidate.data.materials:
                continue
            semantic = candidate.get("semantic")
            if candidate.name == "Context_grade":
                semantic = "context"
            candidate.data.materials.clear()
            candidate.data.materials.append(white_mask if semantic in target_semantics else black_mask)
        path = args.output / f"mask-{name}.png"
        scene.render.filepath = str(path.resolve())
        bpy.ops.render.render(write_still=True)
        # Hard-binarize after antialiasing.  The production compositor may
        # therefore prove that every protected pixel remains byte-identical.
        magick = shutil.which("magick")
        if not magick:
            raise RuntimeError("ImageMagick `magick` is required to normalize class masks")
        subprocess.run([
            magick, str(path), "-colorspace", "Gray", "-threshold", "50%",
            "-type", "Bilevel", "-strip",
            "-define", "png:exclude-chunks=date,time", str(path),
        ], check=True)
        mask_paths[name] = {"path": str(path.resolve()), "sha256": sha256(path)}

    render_class_mask("building", {"building", "building_detail_proxy"})
    render_class_mask("vegetation", {"vegetation"})
    receipt = {
        "schema": "green-atlas.nspd-street-render-receipt.v1",
        "status": "geometry_material_prototype_not_beauty_admitted",
        "scene_state": args.scene_state,
        "input": {"path": str(args.packet.resolve()), "sha256": sha256(args.packet)},
        "output": {"image": str(image_path.resolve()), "blend": str((args.output / "street-geometry-prototype.blend").resolve()), "image_sha256": image_hash, "semantic_image": str(semantic_path.resolve()), "semantic_image_sha256": sha256(semantic_path), "class_masks": mask_paths},
        "engine": beauty_engine,
        "render_backend": render_backend,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "samples_requested": args.samples,
        "adaptive_threshold": args.adaptive_threshold if beauty_engine == "CYCLES" else None,
        "appearance_profile": args.appearance_profile,
        "native_appearance_assets": native_appearance_assets,
        "camera": packet["camera"],
        "lighting": packet["lighting"],
        "assets": asset_receipt,
        "repaired_texture_images": repaired_textures,
        "counts": {"surfaces": len(packet["surfaces"]), "automatic_context_surfaces": len(packet.get("automatic_context_surfaces", [])), "curbs": len(packet["curbs"]), "road_markings": len(packet.get("road_markings", [])), "project_marking_surfaces_rendered": rendered_project_marking_surfaces, "project_marking_surfaces_hidden": hidden_project_marking_surfaces, "buildings": len(packet["buildings"]), "vegetation": len(packet["vegetation"]), "lawn_blade_clumps": lawn_blade_clumps, "facade_joint_proxies": facade_joint_count, "automatic_infrastructure_source": len(packet.get("automatic_infrastructure", [])), "automatic_infrastructure_rendered": rendered_infrastructure, "context_window_proxies": window_count, "retail_glazing_proxies": retail_glazing_count},
        "presentation_population": packet.get("presentation_population"),
        "presentation_population_rendered": args.presentation_population,
        "presentation_population_counts": presentation_counts,
        "presentation_assets": presentation_asset_receipt,
        "neural_finish": packet["neural_finish"],
    }
    (args.output / "render-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
