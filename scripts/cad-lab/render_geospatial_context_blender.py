"""Render a neutral street-context proof from OSM plus exact project surfaces.

This is an alignment/massing diagnostic, not a beauty render.  Buildings with
unknown heights are deliberately omitted instead of receiving invented floors.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import random
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[2]
AHORN_ASSET = ROOT/".runtime/assets/blenderkit/ahorn-tree/ahorn-tree-1k.blend"
AHORN_SHA256 = "609659602a9e31d9bb57360ef1abef0b7955f383624e31b08891f1e10d58983e"
BIRCH_ASSET = ROOT/".runtime/assets/blenderkit/silver-birch-tree/silver-birch-tree-1k.blend"
BIRCH_SHA256 = "d8edf007cd325056dfd33e3fb9d1ed61420d27b4b88fe71ce2cb616aaf04e84e"
FIR_ASSET = ROOT/".runtime/assets/blenderkit/fir-sapling-medium/fir-sapling-medium-1k.blend"
FIR_SHA256 = "fbdcdba883dfa88cd69d8fdfdcf84e8ce4e672adda07164f12183f84ff21c4a2"
PBR_ROOT = ROOT/".runtime/cad-vegetation-20260919/pbr"
TREE_COLLECTIONS: dict[str, list[tuple[bpy.types.Collection, float]]] = {}

TREE_ASSET_PROVENANCE = {
    "Клен": {
        "path": str(AHORN_ASSET),
        "sha256": AHORN_SHA256,
        "asset": "BlenderKit Ahorn tree 1K",
        "license": "cc_zero",
        "asset_base_id": "6b36e011-4ce4-4f3d-98c9-4a2fd6b62f66",
        "url": "https://www.blendkit.com/asset-gallery-detail/6b36e011-4ce4-4f3d-98c9-4a2fd6b62f66/",
    },
    "Береза": {
        "path": str(BIRCH_ASSET),
        "sha256": BIRCH_SHA256,
        "asset": "BlenderKit Silver Birch Tree 1K",
        "license": "royalty_free / free asset",
        "asset_id": "7b56cbe1-a3a4-46b3-ac63-3b2ec317de0c",
        "asset_base_id": "7dbc5c1a-23a3-45f0-b25c-f2c692431e7e",
        "url": "https://www.blendkit.com/asset-gallery-detail/7dbc5c1a-23a3-45f0-b25c-f2c692431e7e/",
    },
    "Ель": {
        "path": str(FIR_ASSET),
        "sha256": FIR_SHA256,
        "asset": "BlenderKit Fir Sapling Medium 1K",
        "license": "cc_zero",
        "asset_id": "6b8490ca-1079-4ee7-b913-a3b7cba7fe78",
        "asset_base_id": "1f4bcc68-8fb3-45b1-8e67-f89e72321fa9",
        "url": "https://www.blendkit.com/asset-gallery-detail/1f4bcc68-8fb3-45b1-8e67-f89e72321fa9/",
    },
}


def arguments() -> tuple[Path, Path, Path]:
    values = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    if len(values) != 3:
        raise SystemExit("usage: blender --background --python script -- context.json project-scene.json output")
    return Path(values[0]).resolve(), Path(values[1]).resolve(), Path(values[2]).resolve()


def clear() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in list(bpy.data.collections):
        if collection.name != "Collection":
            bpy.data.collections.remove(collection)


def material(name: str, color: tuple[float, float, float, float], roughness: float = 0.8) -> bpy.types.Material:
    value = bpy.data.materials.new(name)
    value.diffuse_color = color
    value.use_nodes = True
    shader = value.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = color
    shader.inputs["Roughness"].default_value = roughness
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asphalt_material() -> bpy.types.Material:
    value = bpy.data.materials.new("Asphalt / controlled PBR")
    value.use_nodes = True
    nodes = value.node_tree.nodes
    links = value.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (0.28, 0.28, 0.28)
    links.new(coordinate.outputs["Object"], mapping.inputs["Vector"])

    diffuse = nodes.new("ShaderNodeTexImage")
    diffuse.image = bpy.data.images.load(str(PBR_ROOT/"asphalt01_Diffuse.jpg"), check_existing=True)
    diffuse.projection = "BOX"
    diffuse.projection_blend = 0.25
    links.new(mapping.outputs["Vector"], diffuse.inputs["Vector"])
    hue = nodes.new("ShaderNodeHueSaturation")
    hue.inputs["Saturation"].default_value = 0.45
    hue.inputs["Value"].default_value = 0.58
    links.new(diffuse.outputs["Color"], hue.inputs["Color"])
    links.new(hue.outputs["Color"], shader.inputs["Base Color"])

    rough = nodes.new("ShaderNodeTexImage")
    rough.image = bpy.data.images.load(str(PBR_ROOT/"asphalt01_Rough.jpg"), check_existing=True)
    rough.image.colorspace_settings.name = "Non-Color"
    rough.projection = "BOX"
    links.new(mapping.outputs["Vector"], rough.inputs["Vector"])
    links.new(rough.outputs["Color"], shader.inputs["Roughness"])

    normal_texture = nodes.new("ShaderNodeTexImage")
    normal_texture.image = bpy.data.images.load(str(PBR_ROOT/"asphalt01_nor_gl.jpg"), check_existing=True)
    normal_texture.image.colorspace_settings.name = "Non-Color"
    normal_texture.projection = "BOX"
    links.new(mapping.outputs["Vector"], normal_texture.inputs["Vector"])
    normal = nodes.new("ShaderNodeNormalMap")
    normal.inputs["Strength"].default_value = 0.18
    links.new(normal_texture.outputs["Color"], normal.inputs["Color"])
    links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return value


def concrete_material() -> bpy.types.Material:
    value = bpy.data.materials.new("Concrete / controlled PBR")
    value.use_nodes = True
    nodes = value.node_tree.nodes
    links = value.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (0.42, 0.42, 0.42)
    links.new(coordinate.outputs["Object"], mapping.inputs["Vector"])
    diffuse = nodes.new("ShaderNodeTexImage")
    diffuse.image = bpy.data.images.load(str(PBR_ROOT/"concrete_Diffuse.jpg"), check_existing=True)
    diffuse.projection = "BOX"
    diffuse.projection_blend = 0.25
    links.new(mapping.outputs["Vector"], diffuse.inputs["Vector"])
    hue = nodes.new("ShaderNodeHueSaturation")
    hue.inputs["Saturation"].default_value = 0.28
    hue.inputs["Value"].default_value = 0.72
    links.new(diffuse.outputs["Color"], hue.inputs["Color"])
    links.new(hue.outputs["Color"], shader.inputs["Base Color"])
    rough = nodes.new("ShaderNodeTexImage")
    rough.image = bpy.data.images.load(str(PBR_ROOT/"concrete_Rough.jpg"), check_existing=True)
    rough.image.colorspace_settings.name = "Non-Color"
    rough.projection = "BOX"
    links.new(mapping.outputs["Vector"], rough.inputs["Vector"])
    links.new(rough.outputs["Color"], shader.inputs["Roughness"])
    normal_texture = nodes.new("ShaderNodeTexImage")
    normal_texture.image = bpy.data.images.load(str(PBR_ROOT/"concrete_nor_gl.jpg"), check_existing=True)
    normal_texture.image.colorspace_settings.name = "Non-Color"
    normal_texture.projection = "BOX"
    links.new(mapping.outputs["Vector"], normal_texture.inputs["Vector"])
    normal = nodes.new("ShaderNodeNormalMap")
    normal.inputs["Strength"].default_value = 0.14
    links.new(normal_texture.outputs["Color"], normal.inputs["Color"])
    links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return value


def lawn_material() -> bpy.types.Material:
    value = bpy.data.materials.new("Lawn / deterministic substrate")
    value.use_nodes = True
    nodes = value.node_tree.nodes
    links = value.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 5.0
    noise.inputs["Detail"].default_value = 5.0
    noise.inputs["Roughness"].default_value = 0.72
    links.new(coordinate.outputs["Object"], noise.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (0.012, 0.085, 0.004, 1.0)
    ramp.color_ramp.elements[1].color = (0.11, 0.42, 0.025, 1.0)
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
    shader.inputs["Roughness"].default_value = 0.93
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.035
    links.new(noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return value


def add_grass_clumps(plan_objects: list[dict], grade, mat: bpy.types.Material, density_m2: float = 8.0) -> int:
    rng = random.Random(20260920)
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    clumps = 0
    for row in plan_objects:
        if row["semantic_class"] != "lawn":
            continue
        source_vertices = row["mesh"]["vertices"]
        for triangle in row["mesh"]["triangles"]:
            a, b, c = [source_vertices[index] for index in triangle]
            area = abs((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))/2
            count = int(area*density_m2)
            for _ in range(count):
                r1, r2 = rng.random(), rng.random()
                root = math.sqrt(r1)
                wa, wb, wc = 1-root, root*(1-r2), root*r2
                x = wa*a[0]+wb*b[0]+wc*c[0]
                y = wa*a[1]+wb*b[1]+wc*c[1]
                z = grade(x, y)+0.18
                height = rng.uniform(0.07, 0.16)
                width = rng.uniform(0.012, 0.026)
                angle = rng.random()*math.tau
                for rotation in (angle, angle+math.pi/2):
                    dx, dy = math.cos(rotation)*width/2, math.sin(rotation)*width/2
                    start = len(vertices)
                    vertices.extend([
                        [x-dx, y-dy, z], [x+dx, y+dy, z],
                        [x+dx*0.35, y+dy*0.35, z+height], [x-dx*0.35, y-dy*0.35, z+height],
                    ])
                    faces.append([start, start+1, start+2, start+3])
                clumps += 1
    if faces:
        mesh_object("Deterministic grass clumps / confirmed lawn only", vertices, faces, mat)
    return clumps


def mesh_object(name: str, vertices: list[list[float]], faces: list[list[int]], mat: bpy.types.Material) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name+" mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def extrude_building(name: str, ring: list[list[float]], height: float, base_z: float,
                     mat: bpy.types.Material) -> bpy.types.Object | None:
    points = ring[:-1] if ring and ring[0] == ring[-1] else ring
    if len(points) < 3:
        return None
    vertices = [[x, y, base_z] for x, y in points] + [[x, y, height+base_z] for x, y in points]
    count = len(points)
    faces = [list(reversed(range(count))), list(range(count, count*2))]
    for index in range(count):
        next_index = (index+1) % count
        faces.append([index, next_index, count+next_index, count+index])
    obj = mesh_object(name, vertices, faces, mat)
    obj["height_source"] = "OSM height/levels from context packet"
    obj["facade_status"] = "neutral massing; openings unavailable in source data"
    return obj


def road_ribbon(name: str, coordinates: list[list[float]], width: float, mat: bpy.types.Material,
                grade, z_offset: float) -> bpy.types.Object | None:
    if len(coordinates) < 2:
        return None
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    half = width/2
    for (x1, y1), (x2, y2) in zip(coordinates, coordinates[1:]):
        dx, dy = x2-x1, y2-y1
        length = math.hypot(dx, dy)
        if length < 1e-6:
            continue
        nx, ny = -dy/length*half, dx/length*half
        start = len(vertices)
        vertices.extend([
            [x1+nx, y1+ny, grade(x1+nx, y1+ny)+z_offset],
            [x1-nx, y1-ny, grade(x1-nx, y1-ny)+z_offset],
            [x2-nx, y2-ny, grade(x2-nx, y2-ny)+z_offset],
            [x2+nx, y2+ny, grade(x2+nx, y2+ny)+z_offset],
        ])
        faces.append([start, start+1, start+2, start+3])
    return mesh_object(name, vertices, faces, mat) if faces else None


def curb_chain(name: str, samples: list[dict], origin_xyz: list[float], mat: bpy.types.Material,
               width: float = 0.22) -> bpy.types.Object | None:
    coordinates = [row["xy"] for row in samples]
    local = [[x-origin_xyz[0], y-origin_xyz[1]] for x, y in coordinates]
    if len(local) < 2:
        return None
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    half = width/2
    offsets = []
    for index, (x, y) in enumerate(local):
        before = local[max(0, index-1)]
        after = local[min(len(local)-1, index+1)]
        dx, dy = after[0]-before[0], after[1]-before[1]
        length = math.hypot(dx, dy)
        offsets.append((-dy/length*half, dx/length*half))
    for index, ((x, y), (nx, ny)) in enumerate(zip(local, offsets)):
        base_z = samples[index]["road_z"]-origin_xyz[2]+0.01
        top_z = samples[index]["curb_top_z"]-origin_xyz[2]+0.01
        vertices.extend([
            [x+nx, y+ny, base_z], [x-nx, y-ny, base_z],
            [x+nx, y+ny, top_z], [x-nx, y-ny, top_z],
        ])
    for index in range(len(local)-1):
        a, b = index*4, (index+1)*4
        faces.extend([
            [a+2, a+3, b+3, b+2],
            [a, a+2, b+2, b],
            [a+1, b+1, b+3, a+3],
        ])
    faces.extend([[0, 1, 3, 2], [len(vertices)-4, len(vertices)-2, len(vertices)-1, len(vertices)-3]])
    return mesh_object(name, vertices, faces, mat)


def isolated_object_collection(name: str, source: bpy.types.Object) -> bpy.types.Collection:
    """Make only the authored primary tree visible, retaining hidden GN dependencies."""
    value = bpy.data.collections.new(name)
    clone = source.copy()
    world = source.matrix_world.copy()
    clone.parent = None
    clone.matrix_world = world
    value.objects.link(clone)
    return value


def tree_asset_variants(species: str) -> list[tuple[bpy.types.Collection, float]]:
    if species in TREE_COLLECTIONS:
        return TREE_COLLECTIONS[species]
    provenance = TREE_ASSET_PROVENANCE.get(species)
    if not provenance:
        return []
    path = Path(provenance["path"])
    if not path.is_file() or sha256(path) != provenance["sha256"]:
        raise RuntimeError(f"{species} asset is missing or changed: {path}")

    if species == "Клен":
        with bpy.data.libraries.load(str(path), link=False) as (available, requested):
            required = ["Ahorn tree", "twigs"]
            missing = sorted(set(required)-set(available.collections))
            if missing:
                raise RuntimeError(f"Maple collections missing: {missing}")
            requested.collections = required
        variants = [(requested.collections[0], 10.56235790)]
    elif species == "Береза":
        with bpy.data.libraries.load(str(path), link=False) as (available, requested):
            required = "Silver Birch Tree"
            if required not in available.collections:
                raise RuntimeError(f"Birch collection missing: {required}")
            requested.collections = [required]
        source = next((obj for obj in requested.collections[0].all_objects if obj.name == "Silver birch"), None)
        if source is None:
            raise RuntimeError("Birch primary object missing: Silver birch")
        variants = [(isolated_object_collection("render asset / silver birch", source), 18.03141594)]
    elif species == "Ель":
        with bpy.data.libraries.load(str(path), link=False) as (available, requested):
            required = "Fir Sapling Medium"
            if required not in available.collections:
                raise RuntimeError(f"Fir collection missing: {required}")
            requested.collections = [required]
        source_collection = requested.collections[0]
        native_heights = {
            "fir_sapling_medium_a": 9.03894329,
            "fir_sapling_medium_b": 7.97271013,
            "fir_sapling_medium_c": 6.14127541,
        }
        objects = {obj.name: obj for obj in source_collection.all_objects}
        variants = []
        for name, native_height in native_heights.items():
            if name not in objects:
                raise RuntimeError(f"Fir primary object missing: {name}")
            collection = isolated_object_collection(f"render asset / {name}", objects[name])
            # The authored variants are positioned side by side in the source
            # collection; every isolated render variant must start at the origin.
            collection.objects[0].location.x = 0.0
            variants.append((collection, native_height))
    else:
        variants = []
    TREE_COLLECTIONS[species] = variants
    return variants


def load_tree_instance(species: str, position: list[float], height_m: float,
                       ground_z: float, seed: int) -> bpy.types.Object | None:
    variants = tree_asset_variants(species)
    if not variants:
        return None
    collection, native_collar_to_crown_m = variants[seed % len(variants)]
    instance = bpy.data.objects.new(f"source-backed {species}", None)
    instance.instance_type = "COLLECTION"
    instance.instance_collection = collection
    bpy.context.scene.collection.objects.link(instance)
    scale = height_m/native_collar_to_crown_m
    instance.scale = (scale, scale, scale)
    instance.location = (position[0], position[1], ground_z)
    instance.rotation_euler.z = (seed % 10000)/10000*math.tau
    instance["root_collar_local_z"] = 0.0
    instance["asset_species"] = species
    instance["asset_sha256"] = TREE_ASSET_PROVENANCE[species]["sha256"]
    return instance


def aim(camera: bpy.types.Object, target: tuple[float, float, float]) -> None:
    camera.rotation_euler = (Vector(target)-camera.location).to_track_quat("-Z", "Y").to_euler()


def main() -> None:
    context_path, project_path, output = arguments()
    output.mkdir(parents=True, exist_ok=True)
    context = json.loads(context_path.read_text())
    project = json.loads(project_path.read_text())
    elevated_path = ROOT/".runtime/compact-street-render-20260919/elevated-corridor.json"
    elevated = json.loads(elevated_path.read_text())
    source_origin = project["coordinates"]["local_origin_xyz"]
    road_vertices_local = np.asarray(elevated["road_mesh"]["vertices"], dtype=float)-np.asarray(source_origin)
    matrix = np.column_stack((road_vertices_local[:, 0], road_vertices_local[:, 1], np.ones(len(road_vertices_local))))
    plane, *_ = np.linalg.lstsq(matrix, road_vertices_local[:, 2], rcond=None)
    residual = road_vertices_local[:, 2]-matrix@plane

    def grade(x: float, y: float) -> float:
        return float(plane[0]*x+plane[1]*y+plane[2])

    clear()
    scene = bpy.context.scene
    requested_engine = os.environ.get("GA_CONTEXT_ENGINE", "eevee").lower()
    scene.render.engine = "CYCLES" if requested_engine == "cycles" else "BLENDER_EEVEE_NEXT"
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = int(os.environ.get("GA_CONTEXT_SAMPLES", "64"))
        scene.cycles.use_denoising = True
        scene.cycles.use_adaptive_sampling = True
        scene.cycles.adaptive_threshold = 0.03
        scene.cycles.max_bounces = 8
        scene.cycles.diffuse_bounces = 3
        scene.cycles.glossy_bounces = 3
        scene.cycles.transparent_max_bounces = 8
    scene.render.resolution_x = int(os.environ.get("GA_CONTEXT_WIDTH", "1536"))
    scene.render.resolution_y = int(os.environ.get("GA_CONTEXT_HEIGHT", "1024"))
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.film_transparent = False
    scene.render.image_settings.color_depth = "8"
    scene.view_settings.look = "AgX - Medium Low Contrast"
    scene.view_settings.exposure = -0.28

    ground_mat = material("Context ground", (0.12, 0.22, 0.055, 1.0), 0.92)
    road_mat = asphalt_material()
    concrete_mat = concrete_material()
    lawn_mat = lawn_material()
    path_mat = concrete_mat
    building_mat = material("OSM building massing / source geometry only", (0.76, 0.77, 0.735, 1.0), 0.82)
    curb_mat = material("Source curb", (0.58, 0.59, 0.57, 1.0), 0.84)
    project_materials = {
        "road": road_mat,
        "sidewalk": concrete_mat,
        "lawn": lawn_mat,
    }

    ground_extent = [(-450.0, -530.0), (450.0, -530.0), (450.0, 370.0), (-450.0, 370.0)]
    ground = mesh_object(
        "Broad context ground / road-grade continuation",
        [[x, y, grade(x, y)-0.08] for x, y in ground_extent],
        [[0, 1, 2, 3]], ground_mat,
    )
    ground["height_status"] = "least-squares continuation of source-derived road TIN"

    buildings_rendered = roads_rendered = 0
    for feature in context["features"]:
        props = feature["properties"]
        geometry = feature["geometry"]
        if props["kind"] == "building" and props.get("height_m"):
            if geometry["type"] != "Polygon":
                continue
            ring = geometry["coordinates"][0]
            centroid_x = sum(point[0] for point in ring[:-1])/max(1, len(ring)-1)
            centroid_y = sum(point[1] for point in ring[:-1])/max(1, len(ring)-1)
            if extrude_building(props["id"], ring, props["height_m"], grade(centroid_x, centroid_y), building_mat):
                buildings_rendered += 1
        elif props["kind"] == "transportation" and geometry["type"] == "LineString":
            highway = props.get("highway")
            mat = path_mat if highway in {"footway", "path", "steps"} else road_mat
            z_offset = 0.025 if mat == road_mat else 0.08
            if road_ribbon(props["id"], geometry["coordinates"], props["width_m"], mat, grade, z_offset):
                roads_rendered += 1

    # Exact project HATCH geometry visually overrides the estimated OSM ribbon
    # in the design window.  Z is schematic because this proof checks XY context.
    exact_road_vertices = [
        [x-source_origin[0], y-source_origin[1], z-source_origin[2]+0.02]
        for x, y, z in elevated["road_mesh"]["vertices"]
    ]
    exact_road = mesh_object(
        "project:source-derived elevated road corridor", exact_road_vertices,
        elevated["road_mesh"]["triangles"], project_materials["road"],
    )
    exact_road["height_status"] = "estimated_from_reviewed_paired_spot_elevations"
    exact_road["source"] = str(elevated_path)

    for row in project["geometry"]["plan_objects"]:
        # The source-derived TIN covers only the original compact control
        # window.  Render every expanded authored road HATCH on the fitted
        # grade plane, then let the exact TIN override its covered fragment.
        surface_offset = {"road": 0.012, "sidewalk": 0.205, "lawn": 0.18}[row["semantic_class"]]
        vertices = [[x, y, grade(x, y)+surface_offset] for x, y, _z in row["mesh"]["vertices"]]
        obj = mesh_object("project:"+row["id"], vertices, row["mesh"]["triangles"], project_materials[row["semantic_class"]])
        obj["source_handle"] = row["source"]["source_handle"]
        obj["height_status"] = "schematic_xy_context_proof"

    # Individual crossed cards read as black spikes at this camera distance.
    # Keep the lawn geometry/material deterministic and defer real blades until
    # a species-appropriate ground-cover asset is available.
    grass_clumps = add_grass_clumps(project["geometry"]["plan_objects"], grade, lawn_mat, density_m2=0.0)

    curbs_rendered = 0
    for index, chain in enumerate(elevated["chains"]):
        if curb_chain(f"curb:{chain['source_insert']}", chain["samples"], source_origin, curb_mat):
            curbs_rendered += 1

    tree_evidence_path = ROOT/".runtime/cad-vegetation-20260919/trees.json"
    tree_evidence = json.loads(tree_evidence_path.read_text())
    trees_rendered = 0
    trees_by_species: dict[str, int] = {}
    omitted_by_species: dict[str, int] = {}
    for tree in tree_evidence["matched"]:
        position = [tree["xy"][0]-source_origin[0], tree["xy"][1]-source_origin[1], None]
        if math.hypot(position[0], position[1]) > 180:
            continue
        species = tree["species"]
        if species not in TREE_ASSET_PROVENANCE:
            omitted_by_species[species] = omitted_by_species.get(species, 0)+1
            continue
        tree_ground_z = grade(position[0], position[1])+0.18
        seed = int(hashlib.sha256(str(tree["number"]).encode()).hexdigest()[:8], 16)
        instance = load_tree_instance(species, position, tree["height_m"], tree_ground_z, seed)
        if instance is None:
            omitted_by_species[species] = omitted_by_species.get(species, 0)+1
            continue
        instance.name = f"tree:{tree['number']} / source-backed {species}"
        # Variation affects crown width only; inventory height remains exact.
        xy_variation = 0.94+(tree["number"] % 13)/100
        instance.scale.x *= xy_variation
        instance.scale.y *= 2.0-xy_variation
        instance["source_inventory"] = tree["number"]
        instance["ground_status"] = "road-grade continuation; XY and height from inventory"
        trees_rendered += 1
        trees_by_species[species] = trees_by_species.get(species, 0)+1

    camera_data = bpy.data.cameras.new("Street context camera")
    camera = bpy.data.objects.new("Street context camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera_selection_path = Path(os.environ.get(
        "GA_CONTEXT_CAMERA_SELECTION",
        str(context_path.parent/"camera-selection.json"),
    )).resolve()
    camera_selection = json.loads(camera_selection_path.read_text())["selected"]
    camera.data.lens = camera_selection["lens_mm"]
    camera.data.sensor_width = 36
    camera.data.clip_start = 0.1
    camera.data.clip_end = 1200
    camera_x, camera_y, _camera_z = camera_selection["position_local"]
    target_x, target_y, _target_z = camera_selection["target_local"]
    camera.location = (camera_x, camera_y, grade(camera_x, camera_y)+1.62)
    target = (target_x, target_y, grade(target_x, target_y)+1.65)
    aim(camera, target)

    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 2.0
    sun_data.angle = math.radians(10.0)
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(52), 0, math.radians(-38))

    world = bpy.data.worlds.new("Soft daylight")
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()
    output_node = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Strength"].default_value = 0.22
    sky = nodes.new("ShaderNodeTexSky")
    sky.sky_type = "NISHITA"
    sky.sun_elevation = math.radians(38)
    sky.sun_rotation = math.radians(145)
    sky.altitude = 180
    sky.air_density = 1.0
    sky.dust_density = 1.4
    links.new(sky.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output_node.inputs["Surface"])
    scene.world = world

    scene.render.filepath = str(output/"context-massing.png")
    bpy.ops.wm.save_as_mainfile(filepath=str(output/"context-massing.blend"))
    bpy.ops.render.render(write_still=True)
    receipt = {
        "schema": "green-atlas.context-massing-render.v1",
        "status": "alignment_diagnostic_not_beauty",
        "context_packet": str(context_path),
        "project_scene": str(project_path),
        "buildings_rendered": buildings_rendered,
        "buildings_omitted_unknown_height": context["quality"]["building_count"]-buildings_rendered,
        "transportation_rendered": roads_rendered,
        "curb_chains_rendered": curbs_rendered,
        "source_backed_trees_rendered": trees_rendered,
        "source_backed_trees_by_species": trees_by_species,
        "trees_omitted_missing_asset_by_species": omitted_by_species,
        "tree_assets": [TREE_ASSET_PROVENANCE[name] for name in sorted(trees_by_species)],
        "deterministic_grass_clumps": grass_clumps,
        "render_engine": scene.render.engine,
        "render_samples": scene.cycles.samples if scene.render.engine == "CYCLES" else None,
        "camera": {
            "selection": str(camera_selection_path),
            "position_local": list(camera.location),
            "target_local": list(target),
            "lens_mm": camera_selection["lens_mm"],
            "framing_tree": camera_selection["framing_tree"],
            "road_margin_m": camera_selection["road_margin_m"],
            "visible_buildings": camera_selection["visible_buildings"],
        },
        "grade_plane": {
            "formula": "local_z = a*x + b*y + c",
            "coefficients": [float(value) for value in plane],
            "fit_vertices": len(road_vertices_local),
            "rmse_m": float(np.sqrt(np.mean(np.square(residual)))),
            "max_residual_m": float(np.max(np.abs(residual))),
            "exact_road_uses_tin": True,
        },
        "limitations": [
            "OSM alignment is candidate-only and has no independent survey checkpoints",
            "OSM road widths are mostly class/lanes estimates",
            "broad context ground follows a least-squares plane fitted to the source-derived road TIN",
            "exact road uses the source-derived TIN; sidewalk/lawn use its grade-plane continuation",
            "building footprints and heights are source-backed, but facades remain neutral because openings/materials are unavailable",
            "linden inventory points are omitted until a licensed species-specific asset is available",
        ],
    }
    (output/"receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
