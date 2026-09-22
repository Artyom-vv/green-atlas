"""Render a 3D data-confidence scene from terrain, OSM context, and DXF Z.

This is a spatial assembly diagnostic. It intentionally uses flat materials:
the purpose is to verify real placement, dimensions, and vertical layering
before photoreal materials or neural finishing are admitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TREE_ASSETS = {
    "Клен": {
        "path": ROOT/".runtime/assets/blenderkit/ahorn-tree/ahorn-tree-1k.blend",
        "sha256": "609659602a9e31d9bb57360ef1abef0b7955f383624e31b08891f1e10d58983e",
    },
    "Ель": {
        "path": ROOT/".runtime/assets/blenderkit/fir-sapling-medium/fir-sapling-medium-1k.blend",
        "sha256": "fbdcdba883dfa88cd69d8fdfdcf84e8ce4e672adda07164f12183f84ff21c4a2",
    },
}
TREE_VARIANTS = {}

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--terrain", type=Path, required=True)
    parser.add_argument("--source-terrain", type=Path, required=True)
    parser.add_argument("--curb", type=Path, required=True)
    parser.add_argument("--scene-packet", type=Path, required=True)
    parser.add_argument("--elevation-controls", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    values = sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else []
    return parser.parse_args(values)


def clear():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def mat(name, color, roughness=.82):
    value = bpy.data.materials.new(name)
    value.use_nodes = True
    shader = value.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = color
    shader.inputs["Roughness"].default_value = roughness
    value.diffuse_color = color
    return value


def texture_material(name, spec, saturation, normal_strength, tile_width_m):
    for row in spec["maps"].values():
        path = Path(row["path"])
        if not path.is_file() or sha256(path) != row["expected_sha256"]:
            raise RuntimeError(f"Material input missing or changed: {path}")
    value = bpy.data.materials.new(name)
    value.use_nodes = True
    nodes, links = value.node_tree.nodes, value.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    scale = 1.0/tile_width_m
    mapping.inputs["Scale"].default_value = (scale, scale, scale)
    links.new(coordinate.outputs["Object"], mapping.inputs["Vector"])
    textures = {}
    for role, row in spec["maps"].items():
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(row["path"], check_existing=True)
        texture.extension = "REPEAT"
        texture.projection = "BOX"
        texture.projection_blend = .25
        if role != "diffuse":
            texture.image.colorspace_settings.name = "Non-Color"
        links.new(mapping.outputs["Vector"], texture.inputs["Vector"])
        textures[role] = texture
    hue = nodes.new("ShaderNodeHueSaturation")
    hue.inputs["Saturation"].default_value = saturation
    hue.inputs["Value"].default_value = .78
    links.new(textures["diffuse"].outputs["Color"], hue.inputs["Color"])
    links.new(hue.outputs["Color"], shader.inputs["Base Color"])
    links.new(textures["roughness"].outputs["Color"], shader.inputs["Roughness"])
    normal = nodes.new("ShaderNodeNormalMap")
    normal.inputs["Strength"].default_value = normal_strength
    links.new(textures["normal_gl"].outputs["Color"], normal.inputs["Color"])
    links.new(normal.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return value


def lawn_material():
    value = bpy.data.materials.new("Project lawn / low-frequency procedural")
    value.use_nodes = True
    nodes, links = value.node_tree.nodes, value.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Roughness"].default_value = .92
    coordinate = nodes.new("ShaderNodeTexCoord")
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = .38
    noise.inputs["Detail"].default_value = 2.0
    noise.inputs["Roughness"].default_value = .55
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (.022, .070, .012, 1)
    ramp.color_ramp.elements[1].color = (.085, .205, .038, 1)
    micro = nodes.new("ShaderNodeTexNoise")
    micro.inputs["Scale"].default_value = 18.0
    micro.inputs["Detail"].default_value = 3.0
    micro.inputs["Roughness"].default_value = .72
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = .22
    bump.inputs["Distance"].default_value = .007
    links.new(coordinate.outputs["Object"], noise.inputs["Vector"])
    links.new(coordinate.outputs["Object"], micro.inputs["Vector"])
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], shader.inputs["Base Color"])
    links.new(micro.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], shader.inputs["Normal"])
    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return value


def configure_cycles(scene):
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 64
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = .02
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 8
    scene.cycles.diffuse_bounces = 3
    scene.cycles.glossy_bounces = 3
    scene.cycles.transparent_max_bounces = 16
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
    return device


def save_render_result(scene, path, file_format, color_mode="RGBA", color_depth="8"):
    settings = scene.render.image_settings
    previous = (settings.file_format, settings.color_mode, settings.color_depth)
    settings.file_format = file_format
    settings.color_mode = color_mode
    settings.color_depth = color_depth
    bpy.data.images["Render Result"].save_render(filepath=str(path), scene=scene)
    settings.file_format, settings.color_mode, settings.color_depth = previous


def natural_daylight_world():
    world = bpy.data.worlds.new("Deterministic natural daylight")
    world.use_nodes = True
    nodes, links = world.node_tree.nodes, world.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    background.inputs["Strength"].default_value = .32
    sky = nodes.new("ShaderNodeTexSky")
    sky.sky_type = "NISHITA"
    sky.sun_elevation = math.radians(38.0)
    sky.sun_rotation = math.radians(132.0)
    sky.altitude = .16
    sky.air_density = 1.0
    sky.dust_density = 1.25
    sky.ozone_density = 1.0
    links.new(sky.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])
    return world


def mesh(name, vertices, faces, material):
    data = bpy.data.meshes.new(name + ":mesh")
    data.from_pydata(vertices, [], faces)
    data.validate(clean_customdata=True)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(material)
    return obj


def building(name, ring, base_z, height, material):
    points = ring[:-1] if ring and ring[0] == ring[-1] else ring
    curve = bpy.data.curves.new(name + ":curve", type="CURVE")
    curve.dimensions = "2D"
    curve.resolution_u = 1
    curve.fill_mode = "BOTH"
    curve.extrude = height / 2.0
    curve.resolution_v = 0
    spline = curve.splines.new("POLY")
    spline.points.add(len(points)-1)
    for target, point in zip(spline.points, points):
        target.co = (point[0], point[1], 0.0, 1.0)
    spline.use_cyclic_u = True
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    obj.location.z = base_z
    obj.data.materials.append(material)
    return obj


def isolated_collection(name, source):
    collection = bpy.data.collections.new(name)
    clone = source.copy()
    clone.parent = None
    clone.matrix_world = source.matrix_world.copy()
    collection.objects.link(clone)
    return collection


def tree_variants(species):
    if species in TREE_VARIANTS:
        return TREE_VARIANTS[species]
    spec = TREE_ASSETS.get(species)
    if not spec or not spec["path"].is_file() or sha256(spec["path"]) != spec["sha256"]:
        return []
    if species == "Клен":
        with bpy.data.libraries.load(str(spec["path"]), link=False) as (available, requested):
            required = ["Ahorn tree", "twigs"]
            if any(name not in available.collections for name in required):
                return []
            requested.collections = required
        variants = [(requested.collections[0], 10.56235790)]
    else:
        with bpy.data.libraries.load(str(spec["path"]), link=False) as (available, requested):
            if "Fir Sapling Medium" not in available.collections:
                return []
            requested.collections = ["Fir Sapling Medium"]
        objects = {obj.name: obj for obj in requested.collections[0].all_objects}
        native = {
            "fir_sapling_medium_a": 9.03894329,
            "fir_sapling_medium_b": 7.97271013,
            "fir_sapling_medium_c": 6.14127541,
        }
        variants = []
        for name, height in native.items():
            if name not in objects:
                continue
            collection = isolated_collection("render asset / "+name, objects[name])
            collection.objects[0].location.x = 0.0
            variants.append((collection, height))
    TREE_VARIANTS[species] = variants
    return variants


def add_tree(candidate, position_xy, ground_z):
    variants = tree_variants(candidate["species"])
    if not variants:
        return None
    variant_index = int(candidate["id"].split(":")[-1]) % len(variants)
    collection, native_height = variants[variant_index]
    instance = bpy.data.objects.new(candidate["id"], None)
    instance.instance_type = "COLLECTION"
    instance.instance_collection = collection
    bpy.context.scene.collection.objects.link(instance)
    scale = float(candidate["height_m"])/native_height
    instance.scale = (scale, scale, scale)
    instance.location = (
        position_xy[0], position_xy[1], ground_z+.06
    )
    instance.rotation_euler.z = candidate["rotation_z_rad"]
    return instance


def aim(camera, target):
    camera.rotation_euler = (Vector(target)-camera.location).to_track_quat("-Z", "Y").to_euler()


def main():
    options = args()
    options.output.mkdir(parents=True, exist_ok=True)
    context = json.loads(options.context.read_text())
    broad = json.loads(options.terrain.read_text())
    source = json.loads(options.source_terrain.read_text())
    curb = json.loads(options.curb.read_text())
    scene_packet = json.loads(options.scene_packet.read_text())
    elevation_controls = json.loads(options.elevation_controls.read_text())
    origin = broad["coordinate_frame"]["local_origin_dxf_xyz"]
    vegetation_origin = scene_packet["coordinates"]["local_origin_xyz"]
    z_origin = 150.0
    clear()

    broad_mat = mat("Copernicus context terrain / candidate", (.26, .32, .27, 1))
    surface_materials = {
        "road": mat("project road / full surface terrain", (.18, .20, .22, 1)),
        "sidewalk": mat("project sidewalk / full surface terrain", (.62, .57, .48, 1)),
        "lawn": mat("project lawn / full surface terrain", (.22, .43, .20, 1)),
        "special_surface": mat("project special surface / full surface terrain", (.58, .31, .15, 1)),
    }
    road_est_mat = mat("OSM road width estimate", (.12, .13, .14, 1))
    road_explicit_mat = mat("OSM road explicit width", (.08, .16, .23, 1))
    building_mat = mat("OSM building with vertical evidence", (.72, .72, .69, 1))
    curb_mat = mat("DXF source curb riser", (.82, .06, .08, 1))

    grade_plane = source["rules"]["grade_plane"]
    plane_origin = grade_plane["origin_xy"]
    plane_coefficients = grade_plane["coefficients_z_intercept_dx_dy"]
    def plane_z_absolute(x, y):
        return (
            plane_coefficients[0]
            + plane_coefficients[1]*(x-plane_origin[0])
            + plane_coefficients[2]*(y-plane_origin[1])
        )

    source_x = [row["xyz"][0]-origin[0] for row in source["vertices"]]
    source_y = [row["xyz"][1]-origin[1] for row in source["vertices"]]
    source_bounds = (min(source_x), min(source_y), max(source_x), max(source_y))
    def distance_to_source_bounds(x, y):
        dx = max(source_bounds[0]-x, 0.0, x-source_bounds[2])
        dy = max(source_bounds[1]-y, 0.0, y-source_bounds[3])
        return math.hypot(dx, dy)

    broad_vertices = []
    for row in broad["vertices"]:
        local_x, local_y = row["xyz"][:2]
        absolute_x, absolute_y = local_x+origin[0], local_y+origin[1]
        distance = distance_to_source_bounds(local_x, local_y)
        blend = min(1.0, max(0.0, (distance-30.0)/150.0))
        blend = blend*blend*(3.0-2.0*blend)
        project_z = plane_z_absolute(absolute_x, absolute_y)
        z = project_z*(1.0-blend)+row["xyz"][2]*blend
        broad_vertices.append([local_x, local_y, z-z_origin])
    mesh("terrain:copernicus-context", broad_vertices, broad["triangles"], broad_mat)

    # Regular-grid height sampler for contextual objects.
    grid = broad["grid"]
    count = grid["columns"]
    half = grid["half_extent_m"]
    step = grid["step_m"]
    def grade(x, y):
        fx = min(count-1-1e-9, max(0.0, (x+half)/step))
        fy = min(count-1-1e-9, max(0.0, (y+half)/step))
        x0, y0 = int(fx), int(fy)
        dx, dy = fx-x0, fy-y0
        z = [
            broad_vertices[y0*count+x0][2],
            broad_vertices[y0*count+x0+1][2],
            broad_vertices[(y0+1)*count+x0][2],
            broad_vertices[(y0+1)*count+x0+1][2],
        ]
        return (z[0]*(1-dx)+z[1]*dx)*(1-dy)+(z[2]*(1-dx)+z[3]*dx)*dy

    # Spatial hash of the exact authored surface triangles. External road
    # estimates are suppressed only where this real coverage exists.
    surface_xy = [
        (row["xyz"][0]-origin[0], row["xyz"][1]-origin[1])
        for row in source["vertices"]
    ]
    surface_grid = {}
    surface_grid_step = 4.0
    for triangle_index, face in enumerate(source["triangles"]):
        points = [surface_xy[index] for index in face]
        min_x, max_x = min(p[0] for p in points), max(p[0] for p in points)
        min_y, max_y = min(p[1] for p in points), max(p[1] for p in points)
        for gx in range(math.floor(min_x/surface_grid_step), math.floor(max_x/surface_grid_step)+1):
            for gy in range(math.floor(min_y/surface_grid_step), math.floor(max_y/surface_grid_step)+1):
                surface_grid.setdefault((gx, gy), []).append(triangle_index)

    def inside_triangle(point, triangle):
        (x, y), ((x1, y1), (x2, y2), (x3, y3)) = point, triangle
        denominator = (y2-y3)*(x1-x3)+(x3-x2)*(y1-y3)
        if abs(denominator) < 1e-12:
            return False
        a = ((y2-y3)*(x-x3)+(x3-x2)*(y-y3))/denominator
        b = ((y3-y1)*(x-x3)+(x1-x3)*(y-y3))/denominator
        c = 1-a-b
        return min(a, b, c) >= -1e-8

    def inside_project_surface(x, y):
        cell = (math.floor(x/surface_grid_step), math.floor(y/surface_grid_step))
        return any(
            inside_triangle((x, y), [surface_xy[index] for index in source["triangles"][face_index]])
            for face_index in surface_grid.get(cell, [])
        )

    def inside_project_class(x, y, semantic_class):
        cell = (math.floor(x/surface_grid_step), math.floor(y/surface_grid_step))
        return any(
            source["triangle_classes"][face_index] == semantic_class
            and inside_triangle(
                (x, y),
                [surface_xy[index] for index in source["triangles"][face_index]],
            )
            for face_index in surface_grid.get(cell, [])
        )

    def project_grade(x, y):
        cell = (math.floor(x/surface_grid_step), math.floor(y/surface_grid_step))
        for face_index in surface_grid.get(cell, []):
            face = source["triangles"][face_index]
            points = [surface_xy[index] for index in face]
            (x1, y1), (x2, y2), (x3, y3) = points
            denominator = (y2-y3)*(x1-x3)+(x3-x2)*(y1-y3)
            if abs(denominator) < 1e-12:
                continue
            a = ((y2-y3)*(x-x3)+(x3-x2)*(y-y3))/denominator
            b = ((y3-y1)*(x-x3)+(x1-x3)*(y-y3))/denominator
            c = 1-a-b
            if min(a, b, c) >= -1e-8:
                z = sum(
                    weight*(source["vertices"][index]["xyz"][2]-z_origin)
                    for weight, index in zip((a, b, c), face)
                )
                return z, "project_surface_terrain"
        return grade(x, y), "continuous_context_terrain"

    def tree_ground(x, y):
        value, source_name = project_grade(x, y)
        if source_name == "project_surface_terrain":
            return value, source_name, []
        source_x, source_y = x+origin[0], y+origin[1]
        grouped = {}
        for row in elevation_controls["controls"]:
            key = tuple(round(value, 6) for value in row["xy"])
            current = grouped.get(key)
            if current is None or row["z_m"] > current["z_m"]:
                grouped[key] = row
        nearest = sorted(
            (
                (math.dist((source_x, source_y), row["xy"]), row)
                for row in grouped.values()
            ),
            key=lambda item: (item[0], item[1]["id"]),
        )
        supports = [item for item in nearest[:5] if item[0] <= 18.0]
        if len(supports) >= 2:
            weights = [1.0/max(distance, .5)**2 for distance, _ in supports]
            z = sum(
                weight*row["z_m"] for weight, (_, row) in zip(weights, supports)
            )/sum(weights)
            evidence = [{
                "id": row["id"], "distance_m": round(distance, 6),
                "z_m": row["z_m"],
            } for distance, row in supports]
            return z-z_origin, "local_topographic_controls_idw", evidence
        return value, source_name, []

    # Roads remain visibly data-derived: every ribbon uses its stored width and
    # is colored by whether that width was explicit or estimated.
    road_vertices = {"explicit": [], "estimated": []}
    road_faces = {"explicit": [], "estimated": []}
    road_features = 0
    for feature in context["features"]:
        props = feature["properties"]
        geometry = feature["geometry"]
        if props.get("kind") != "transportation" or geometry["type"] != "LineString":
            continue
        width = float(props["width_m"])
        bucket = "explicit" if props["width_confidence"] == "cartographic_explicit" else "estimated"
        coords = geometry["coordinates"]
        for a, b in zip(coords, coords[1:]):
            dx, dy = b[0]-a[0], b[1]-a[1]
            length = math.hypot(dx, dy)
            if length < .05:
                continue
            nx, ny = -dy/length*width/2, dx/length*width/2
            subdivisions = max(1, math.ceil(length/3.0))
            for index in range(subdivisions):
                t0, t1 = index/subdivisions, (index+1)/subdivisions
                p0 = (a[0]+dx*t0, a[1]+dy*t0)
                p1 = (a[0]+dx*t1, a[1]+dy*t1)
                corners = [
                    (p0[0]+nx, p0[1]+ny), (p0[0]-nx, p0[1]-ny),
                    (p1[0]-nx, p1[1]-ny), (p1[0]+nx, p1[1]+ny),
                ]
                center = ((p0[0]+p1[0])/2.0, (p0[1]+p1[1])/2.0)
                if inside_project_surface(*center):
                    continue
                start = len(road_vertices[bucket])
                road_vertices[bucket].extend([
                    [x, y, grade(x, y)+.04] for x, y in corners
                ])
                road_faces[bucket].append([start, start+1, start+2, start+3])
        road_features += 1
    if road_faces["estimated"]:
        mesh("roads:estimated-width", road_vertices["estimated"], road_faces["estimated"], road_est_mat)
    if road_faces["explicit"]:
        mesh("roads:explicit-width", road_vertices["explicit"], road_faces["explicit"], road_explicit_mat)

    building_count = 0
    suppressed_buildings = []
    rendered_building_rings = []
    def vegetation_world_xy(candidate):
        return [
            candidate["position_local"][0]+vegetation_origin[0]-origin[0],
            candidate["position_local"][1]+vegetation_origin[1]-origin[1],
        ]

    confirmed_tree_points = [
        (candidate["id"], vegetation_world_xy(candidate))
        for candidate in scene_packet["vegetation_candidates"]
    ]
    for feature in context["features"]:
        props, geometry = feature["properties"], feature["geometry"]
        if props.get("kind") != "building" or geometry["type"] != "Polygon":
            continue
        height = props.get("height_m")
        if height is None:
            continue
        ring = geometry["coordinates"][0]
        conflicts = []
        for tree_id, point in confirmed_tree_points:
            # Deterministic point-in-polygon ray crossing, with a conservative
            # half-metre tolerance handled by segment distance below.
            inside = False
            for left, right in zip(ring, ring[1:]):
                if ((left[1] > point[1]) != (right[1] > point[1])):
                    crossing_x = (
                        (right[0]-left[0])*(point[1]-left[1])
                        / (right[1]-left[1])+left[0]
                    )
                    if point[0] < crossing_x:
                        inside = not inside
            if inside:
                conflicts.append(tree_id)
        if conflicts:
            suppressed_buildings.append({
                "id": props["id"],
                "reason": "external_building_contains_confirmed_dxf_trees",
                "tree_ids": conflicts,
            })
            continue
        cx = sum(point[0] for point in ring[:-1]) / max(1, len(ring)-1)
        cy = sum(point[1] for point in ring[:-1]) / max(1, len(ring)-1)
        building(props["id"], ring, grade(cx, cy), float(height), building_mat)
        rendered_building_rings.append(ring)
        building_count += 1

    source_objects = []
    rejected_surface_faces = {"count": 0, "area_m2": 0.0, "reasons": {}}
    for semantic_class, surface_material in surface_materials.items():
        faces = []
        fallback_vertices = []
        fallback_faces = []
        for face, face_class in zip(source["triangles"], source["triangle_classes"]):
            if face_class != semantic_class:
                continue
            points = [source["vertices"][index]["xyz"] for index in face]
            denominator = (
                (points[1][0]-points[0][0])*(points[2][1]-points[0][1])
                -(points[2][0]-points[0][0])*(points[1][1]-points[0][1])
            )
            area = abs(denominator)/2.0
            reason = None
            if abs(denominator) > 1e-12:
                dzdx = (
                    (points[1][2]-points[0][2])*(points[2][1]-points[0][1])
                    -(points[2][2]-points[0][2])*(points[1][1]-points[0][1])
                )/denominator
                dzdy = (
                    (points[1][0]-points[0][0])*(points[2][2]-points[0][2])
                    -(points[2][0]-points[0][0])*(points[1][2]-points[0][2])
                )/denominator
                if math.hypot(dzdx, dzdy) > .5:
                    reason = "surface_slope_above_50_percent"
            if reason:
                rejected_surface_faces["count"] += 1
                rejected_surface_faces["area_m2"] += area
                rejected_surface_faces["reasons"][reason] = (
                    rejected_surface_faces["reasons"].get(reason, 0)+1
                )
                fallback_face = []
                for point in points:
                    x, y = point[0]-origin[0], point[1]-origin[1]
                    fallback_face.append(len(fallback_vertices))
                    fallback_vertices.append([x, y, grade(x, y)+.06])
                fallback_faces.append(fallback_face)
                continue
            faces.append(face)
        used = sorted({index for face in faces for index in face})
        remap = {old: new for new, old in enumerate(used)}
        source_vertices = [
            [
                source["vertices"][index]["xyz"][0]-origin[0],
                source["vertices"][index]["xyz"][1]-origin[1],
                source["vertices"][index]["xyz"][2]-z_origin+.06,
            ]
            for index in used
        ]
        if faces:
            source_objects.append(mesh(
                f"terrain:project:{semantic_class}",
                source_vertices,
                [[remap[index] for index in face] for face in faces],
                surface_material,
            ))
        if fallback_faces:
            source_objects.append(mesh(
                f"terrain:project:{semantic_class}:context-fallback",
                fallback_vertices, fallback_faces, surface_material,
            ))
    curb_lower_z = {}
    for row in curb["vertices"]:
        key = tuple(round(value, 6) for value in row["xyz"][:2])
        curb_lower_z[key] = min(curb_lower_z.get(key, math.inf), row["xyz"][2])
    curb_vertices = []
    for row in curb["vertices"]:
        x, y, source_z = row["xyz"]
        key = (round(x, 6), round(y, 6))
        curb_vertices.append([
            x-origin[0], y-origin[1],
            plane_z_absolute(x, y)-z_origin+(source_z-curb_lower_z[key]),
        ])
    curb_object = mesh("curb:dxf-source-risers", curb_vertices, curb["faces"], curb_mat)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.view_settings.look = "AgX - Medium High Contrast"
    world = bpy.data.worlds.new("Neutral daylight")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (.55, .62, .69, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = .45
    scene.world = world
    sun_data = bpy.data.lights.new("Sun", type="SUN")
    sun_data.energy = 3.0
    sun_data.angle = math.radians(12)
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(42), math.radians(-20), math.radians(-35))
    camera_data = bpy.data.cameras.new("Spatial diagnostic camera")
    camera = bpy.data.objects.new("Spatial diagnostic camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.location = (185, -230, 150)
    camera_data.lens = 54
    aim(camera, (0, 0, 8))
    for source_object in source_objects:
        source_object.hide_render = True
    curb_object.hide_render = True
    scene.render.filepath = str(options.output / "spatial-base-context.png")
    bpy.ops.render.render(write_still=True)
    for source_object in source_objects:
        source_object.hide_render = False
    curb_object.hide_render = False
    scene.render.filepath = str(options.output / "spatial-base-confidence.png")
    bpy.ops.render.render(write_still=True)
    tree_instances = []
    placed_trees = []
    omitted_trees = []
    for candidate in scene_packet["vegetation_candidates"]:
        if candidate["species"] not in TREE_ASSETS:
            omitted_trees.append({
                "id": candidate["id"], "species": candidate["species"],
                "reason": "no_verified_species_asset",
            })
            continue
        position_xy = vegetation_world_xy(candidate)
        ground_z, ground_source, ground_supports = tree_ground(*position_xy)
        instance = add_tree(candidate, position_xy, ground_z)
        if instance is None:
            omitted_trees.append({
                "id": candidate["id"], "species": candidate["species"],
                "reason": "asset_missing_or_hash_mismatch",
            })
            continue
        instance["ground_source"] = ground_source
        tree_instances.append(instance)
        placed_trees.append({
            "id": candidate["id"],
            "species": candidate["species"],
            "height_m": candidate["height_m"],
            "position_world_local_xyz": [
                position_xy[0],
                position_xy[1],
                round(ground_z, 6),
            ],
            "source_position_local_xy": candidate["position_local"][:2],
            "source_local_origin_xyz": vegetation_origin,
            "world_local_origin_xyz": origin,
            "ground_source": ground_source,
            "ground_supports": ground_supports,
            "asset_sha256": TREE_ASSETS[candidate["species"]]["sha256"],
            "root_collar_local_z": 0.0,
        })
    scene.render.filepath = str(options.output / "spatial-base-confirmed-vegetation.png")
    bpy.ops.render.render(write_still=True)
    for instance in tree_instances:
        instance.hide_render = True
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 195.0
    camera.location = (0.0, 0.0, 300.0)
    aim(camera, (0.0, 0.0, 0.0))
    sun.hide_render = True
    scene.render.filepath = str(options.output / "spatial-base-semantic-plan.png")
    bpy.ops.render.render(write_still=True)

    # Beauty-pass admission: this is the first perspective render intended to
    # read as a street rather than as a data audit. Geometry and placement stay
    # exactly the same; only materials, daylight and a deterministic road-level
    # camera change.
    for instance in tree_instances:
        instance.hide_render = False
    pbr_materials = {
        "road": texture_material(
            "PBR asphalt / Poly Haven asphalt_01",
            scene_packet["material_registry"]["road"],
            saturation=.42, normal_strength=.11, tile_width_m=4.2,
        ),
        "sidewalk": texture_material(
            "PBR concrete / Poly Haven concrete_floor_02",
            scene_packet["material_registry"]["sidewalk"],
            saturation=.35, normal_strength=.07, tile_width_m=3.5,
        ),
        "lawn": lawn_material(),
        "special_surface": mat(
            "Project special surface / muted PBR", (.34, .16, .075, 1), .88
        ),
    }
    for source_object in source_objects:
        for semantic_class, material in pbr_materials.items():
            if f":{semantic_class}" in source_object.name:
                source_object.data.materials[0] = material
                break
    curb_object.data.materials[0] = pbr_materials["sidewalk"]
    curb_object.location.z = .06
    for road_name in ("roads:estimated-width", "roads:explicit-width"):
        road_object = bpy.data.objects.get(road_name)
        if road_object is not None:
            road_object.data.materials[0] = pbr_materials["road"]
    context_object = bpy.data.objects.get("terrain:copernicus-context")
    if context_object is not None:
        context_object.data.materials[0] = mat(
            "Context terrain / quiet neutral ground", (.105, .155, .075, 1), .94
        )

    road_camera_candidates = []
    for face, semantic_class in zip(source["triangles"], source["triangle_classes"]):
        if semantic_class != "road":
            continue
        points = [source["vertices"][index]["xyz"] for index in face]
        road_camera_candidates.append([
            sum(point[0]-origin[0] for point in points)/3.0,
            sum(point[1]-origin[1] for point in points)/3.0,
            sum(point[2]-z_origin for point in points)/3.0,
        ])
    camera_grid = {}
    for point in road_camera_candidates:
        camera_grid.setdefault(
            (round(point[0]/4.0), round(point[1]/4.0)), point
        )
    camera_samples = list(camera_grid.values())

    def inside_building(point):
        for ring in rendered_building_rings:
            inside = False
            for left, right in zip(ring, ring[1:]):
                if ((left[1] > point[1]) != (right[1] > point[1])):
                    crossing_x = (
                        (right[0]-left[0])*(point[1]-left[1])
                        /(right[1]-left[1])+left[0]
                    )
                    if point[0] < crossing_x:
                        inside = not inside
            if inside:
                return True
        return False

    def clear_road_segment(left, right):
        return all(
            inside_project_class(
                left[0]+(right[0]-left[0])*step_index/20.0,
                left[1]+(right[1]-left[1])*step_index/20.0,
                "road",
            )
            and not inside_building((
                left[0]+(right[0]-left[0])*step_index/20.0,
                left[1]+(right[1]-left[1])*step_index/20.0,
            ))
            for step_index in range(21)
        )

    road_segments = []
    for left_index, left in enumerate(camera_samples):
        for right in camera_samples[left_index+1:]:
            distance = math.dist(left[:2], right[:2])
            if not 25.0 < distance < 65.0 or not clear_road_segment(left, right):
                continue
            midpoint = ((left[0]+right[0])/2.0, (left[1]+right[1])/2.0)
            east_west = abs(right[0]-left[0])/distance
            score = distance+15.0*east_west-.02*math.hypot(*midpoint)
            road_segments.append((score, left, right))
    if not road_segments:
        raise RuntimeError("No unobstructed project-road camera segment")
    _, first_endpoint, second_endpoint = max(
        road_segments,
        key=lambda row: (row[0], row[1][0], row[1][1], row[2][0], row[2][1]),
    )
    camera_point, target_point = sorted(
        (first_endpoint, second_endpoint), key=lambda point: point[0], reverse=True
    )
    target_ground, target_ground_source = project_grade(*target_point[:2])
    camera.data.type = "PERSP"
    camera.data.lens = 45.0
    camera.data.sensor_width = 36.0
    camera.location = (
        camera_point[0], camera_point[1], camera_point[2]+1.65+.06
    )
    camera_target = (target_point[0], target_point[1], target_ground+1.2+.06)
    aim(camera, camera_target)
    scene.world = natural_daylight_world()
    sun.hide_render = False
    sun.data.energy = 2.0
    sun.data.angle = math.radians(7.5)
    sun.rotation_euler = (
        math.radians(38.0), math.radians(-18.0), math.radians(132.0)
    )
    scene.view_settings.look = "AgX - Medium Low Contrast"
    scene.view_settings.exposure = .45
    scene.render.resolution_x = 1600
    scene.render.resolution_y = 900
    scene.render.filepath = str(options.output / "spatial-base-street-pbr.png")
    bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(
        filepath=str(options.output / "spatial-base.blend"), check_existing=False
    )
    receipt = {
        "schema": "green-atlas.spatial-base-diagnostic-receipt.v1",
        "status": "assembled_3d_data_layers_not_beauty",
        "inputs": {
            key: {"path": str(path.resolve()), "sha256": sha256(path)}
            for key, path in {
                "context": options.context, "continuous_terrain": options.terrain,
                "source_terrain": options.source_terrain, "curb": options.curb,
                "scene_packet": options.scene_packet,
                "elevation_controls": options.elevation_controls,
            }.items()
        },
        "objects": {
            "context_buildings_with_vertical_evidence": building_count,
            "suppressed_external_buildings": suppressed_buildings,
            "context_transportation_features": road_features,
            "project_surface_terrain_triangles": len(source["triangles"]),
            "render_admitted_project_triangles": (
                len(source["triangles"])-rejected_surface_faces["count"]
            ),
            "source_curb_faces": len(curb["faces"]),
            "confirmed_tree_instances": len(tree_instances),
            "placed_trees": placed_trees,
            "omitted_trees": omitted_trees,
        },
        "known_accuracy_limits": {
            "horizontal_alignment": context["coordinate_contract"]["alignment_status"],
            "horizontal_fit_rmse_m": context["coordinate_contract"]["fit_rmse_m"],
            "independent_survey_checkpoints": context["coordinate_contract"]["independent_survey_checkpoints"],
            "context_terrain_vertical_max_abs_residual_m": broad["coordinate_frame"]["vertical_max_abs_residual_m"],
            "estimated_transport_widths": context["quality"]["transportation_width_estimates"],
        },
        "render_contract": {
            "textures": True, "neural_finish": False, "vertical_exaggeration": 1.0,
            "project_surface_display_offset_m": .06,
            "street_pbr": {
                "camera_derivation": "longest sampled unobstructed east-west project-road segment, excluding rendered building footprints",
                "camera_location_world_local_xyz": [round(value, 6) for value in camera.location],
                "camera_target_world_local_xyz": [round(value, 6) for value in camera_target],
                "camera_target_ground_source": target_ground_source,
                "lens_mm": camera.data.lens,
                "eye_height_m": 1.65,
                "context_grade_blend": "source grade plane inside 30 m of project bounds; smooth transition to Copernicus by 180 m",
                "curb_vertical_model": "source top-minus-bottom riser preserved over shared source grade plane",
                "road_material": {
                    "id": scene_packet["material_registry"]["road"]["id"],
                    "tile_width_m": 4.2, "normal_strength": .11,
                },
                "sidewalk_material": {
                    "id": scene_packet["material_registry"]["sidewalk"]["id"],
                    "tile_width_m": 3.5, "normal_strength": .07,
                },
                "daylight": {
                    "sky": "Nishita", "sun_elevation_deg": 38.0,
                    "sun_rotation_deg": 132.0, "sun_energy": 2.0,
                    "sun_angle_deg": 7.5,
                },
            },
            "rejected_surface_faces": {
                **rejected_surface_faces,
                "area_m2": round(rejected_surface_faces["area_m2"], 6),
                "render_behavior": (
                    "all source triangles admitted; no fallback"
                    if rejected_surface_faces["count"] == 0 else
                    "same XY/class filled from continuous context DEM"
                ),
            },
        },
    }
    receipt["outputs"] = {
        name: {"sha256": sha256(options.output/name), "bytes": (options.output/name).stat().st_size}
        for name in (
            "spatial-base-context.png", "spatial-base-confidence.png",
            "spatial-base-confirmed-vegetation.png",
            "spatial-base-semantic-plan.png", "spatial-base-street-pbr.png",
            "spatial-base.blend",
        )
    }
    (options.output/"receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True)+"\n")


if __name__ == "__main__":
    main()
