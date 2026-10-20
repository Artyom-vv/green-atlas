"""Blender-only worker: normalized GA scene → PNG and input-bound receipt."""

import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

# Raw sibling scripts are included in the frozen desktop runtime.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lawn_instances as lawn
import render_style as style

COLORS = {
    "building": (0.48, 0.56, 0.61, 1),
    "road": (0.55, 0.59, 0.60, 1),
    "water": (0.34, 0.62, 0.70, 1),
    "lawn": (0.19, 0.40, 0.24, 1),
    "existing_green": (0.19, 0.40, 0.24, 1),
    "utility": (0.65, 0.39, 0.16, 1),
    "site_border": (0.10, 0.25, 0.25, 1),
}


def lawn_surface_rows(packet):
    """Add stable aggregate addresses while sharing the original mesh arrays."""
    return [
        {**row, "id": row.get("id") or f"environment:lawn:{index}"}
        for index, row in enumerate(
            packet.get("environment_surfaces", packet["meshes"])
        )
        if row["kind"] == "lawn"
    ]


def material(name, color):
    result = bpy.data.materials.new(name)
    result.diffuse_color = color
    result.use_nodes = True
    shader = result.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = color
    shader.inputs["Roughness"].default_value = 1.05
    return result


def mesh(name, vertices, faces, surface, roof_surface=None):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, [], faces)
    data.update()
    # PBR normal maps require a tangent basis. Metric planar UVs are generated
    # per face without changing the source mesh or normalising each polygon.
    uv = data.uv_layers.new(name="Metric surface UV")
    for polygon in data.polygons:
        axis = max(range(3), key=lambda i: abs(polygon.normal[i]))
        axes = (1, 2) if axis == 0 else (0, 2) if axis == 1 else (0, 1)
        for loop_index in polygon.loop_indices:
            point = data.vertices[data.loops[loop_index].vertex_index].co
            uv.data[loop_index].uv = (point[axes[0]], point[axes[1]])
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(surface)
    if roof_surface is not None:
        obj.data.materials.append(roof_surface)
        for polygon in data.polygons:
            # Colour the accepted horizontal cap itself, without adding a
            # roof shell, changing the measured height or covering courtyards.
            if polygon.normal.z > 0.99:
                polygon.material_index = 1
    return obj


def line(name, rows, surface, width):
    if not rows:
        return
    data = bpy.data.curves.new(name, "CURVE")
    data.dimensions = "3D"
    data.bevel_depth = width
    data.bevel_resolution = 0
    for points in rows:
        if len(points) < 2:
            continue
        spline = data.splines.new("POLY")
        spline.points.add(len(points) - 1)
        for target, point in zip(spline.points, points):
            target.co = (*point, 1)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.data.materials.append(surface)


def native_materials(appearance):
    registry = appearance["materials"]
    surfaces = {key: material(key, value) for key, value in COLORS.items()}
    surfaces["road"] = style.native_asphalt_material(
        "Native bounded PBR asphalt / Poly Haven asphalt_01", registry["road"]
    )
    surfaces["building"] = style.native_facade_material(
        "Native occupied context facade", (0.42, 0.425, 0.405), 3.0
    )
    surfaces["building_roof"] = style.native_facade_material(
        "Shared matte roof finish", (0.245, 0.255, 0.25), 3.0
    )
    # The scan includes bare/dark patches. Keep it as restrained variation
    # over continuous turf, with fine detail rather than repeated bald islands.
    surfaces["lawn"] = style.texture_material(
        "PBR lawn / ambientCG Grass005",
        registry["lawn"],
        saturation=0.96,
        normal_strength=0.12,
        tile_width_m=2.2,
        neutral_color=(0.065, 0.135, 0.026),
        texture_weight=0.24,
        value=0.86,
        macro_strength=0.04,
        macro_scale=0.10,
        micro_color_strength=0.10,
        micro_scale=150.0,
        micro_bump_strength=0.12,
    )
    surfaces["sidewalk"] = style.texture_material(
        "PBR concrete / Poly Haven concrete_floor_02",
        registry["sidewalk"],
        saturation=0.22,
        normal_strength=0.10,
        tile_width_m=2.8,
        neutral_color=(0.285, 0.28, 0.265),
        texture_weight=0.46,
        value=0.82,
        macro_strength=0.16,
        macro_scale=0.15,
        micro_color_strength=0.04,
        micro_scale=28.0,
        micro_bump_strength=0.035,
    )
    surfaces["building_flat"] = surfaces["sidewalk"]
    surfaces["hard_surface"] = surfaces["sidewalk"]
    surfaces["equipment"] = surfaces["sidewalk"]
    surfaces["parking_apron"] = surfaces["road"]
    surfaces["curb"] = style.simple_material(
        "Native curb concrete", (0.46, 0.46, 0.43), 0.86
    )
    surfaces["marking"] = style.simple_material(
        "Road marking paint", (0.76, 0.77, 0.72), 0.68
    )
    return surfaces


def load_plant(source, loaded):
    if source in loaded:
        return loaded[source]
    collection, _height = style.load_collection_asset(
        Path(source), Path(source).stem, "vegetation"
    )
    points = [
        obj.matrix_world @ Vector(p)
        for obj in collection.objects
        if obj.type == "MESH"
        for p in obj.bound_box
    ]
    lo = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    hi = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    collection.instance_offset = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)
    height, radius = max(0.01, hi.z - lo.z), max(0.01, hi.x - lo.x, hi.y - lo.y) / 2
    loaded[source] = collection, height, radius
    return loaded[source]


def repair_textures(appearance):
    textures = {
        Path(r["path"]).name: r["path"]
        for r in appearance["files"]
        if r["relative_path"].startswith("botaniq/textures/")
    }
    for image in bpy.data.images:
        if image.source != "FILE" or image.packed_file:
            continue
        filename = Path(image.filepath.replace("\\", "/")).name
        if filename in textures:
            image.filepath = textures[filename]
            image.reload()
        elif not Path(bpy.path.abspath(image.filepath)).is_file():
            raise ValueError(f"Missing material texture: {filename}")


def building_details(rows, camera):
    windows = [
        style.glass_material(
            "Context window dark blue-grey",
            (0.018, 0.029, 0.033),
            (0.058, 0.074, 0.079),
        ),
        style.glass_material(
            "Context window muted grey", (0.030, 0.033, 0.034), (0.080, 0.083, 0.081)
        ),
    ]
    frame = style.simple_material("Window frame proxy", (0.20, 0.205, 0.20), 0.72, 0.03)
    joint = style.simple_material("Facade joint proxy", (0.20, 0.205, 0.195), 0.92)
    count = joints = 0
    for row in rows:
        if row["kind"] != "building" or not row.get("height_m"):
            continue
        # Appearance only, within the frozen display envelope. Height evidence
        # and any type-based estimate are recorded separately in the receipt.
        proxy = {
            "id": row["id"],
            "height_m": row["height_m"],
            "class": row.get("building_class", "apartments"),
            "rings_scene_xyz_m": row["footprint_rings"],
        }
        joints += style.add_building_joint_proxies(proxy, joint)
        if proxy["class"] == "retail":
            count += len(
                style.add_retail_glazing(
                    proxy,
                    windows[0],
                    camera_xy=(camera["position"][0], -camera["position"][2]),
                    frame_material=frame,
                    native_depth=True,
                )
            )
        count += style.add_context_windows(
            proxy,
            windows,
            camera_xy=(camera["position"][0], -camera["position"][2]),
            frame_material=frame,
            native_depth=True,
        )
    return {
        "windows": count,
        "facade_joints": joints,
        "status": "illustrative facade details within the display building envelope",
    }


def frozen_facade_details(packet):
    surfaces = {
        "facade_glass": style.glass_material(
            "Shared facade recessed glass", (0.075, 0.095, 0.108), (0.115, 0.135, 0.145)
        ),
        "facade_frame": style.simple_material(
            "Shared facade frame", (0.36, 0.37, 0.35), 0.72, 0.03
        ),
        "facade_joint": style.simple_material(
            "Shared facade joints", (0.20, 0.205, 0.195), 0.92
        ),
        "facade_plinth": style.native_facade_material(
            "Shared restrained base course", (0.285, 0.295, 0.285), 3.0
        ),
    }
    for row in packet["building_facade_meshes"]:
        if row["kind"] not in surfaces:
            raise ValueError("Unsupported frozen facade material")
        mesh(row["id"], row["vertices"], row["faces"], surfaces[row["kind"]])
    return packet["building_facade_details"]


def main():
    packet_path, image_path = map(Path, sys.argv[sys.argv.index("--") + 1 :])
    data = packet_path.read_bytes()
    packet = json.loads(data)
    if packet["schema"] != "green-atlas.project-render/1":
        raise ValueError("Unsupported render packet")
    snapshot = packet["scene"]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    appearance = packet["appearance"]
    for row in appearance["files"]:
        if style.sha256(Path(row["path"])) != row["sha256"]:
            raise ValueError(f"Appearance resource changed: {row['relative_path']}")
    surfaces = native_materials(appearance)
    bounds = []
    batches = {}
    excluded_linework_ids = []
    replaced_ids = set(packet.get("environment_replaced_ids", []))
    for row in packet["meshes"]:
        bounds.extend(row["vertices"])
        for points in row["lines"]:
            bounds.extend(points)
        if row["id"] in replaced_ids:
            continue
        batch_key = (
            "building_flat"
            if row["kind"] == "building" and not row.get("height_m")
            else row["kind"]
        )
        batch = batches.setdefault(
            batch_key, {"vertices": [], "faces": [], "lines": []}
        )
        offset = len(batch["vertices"])
        if batch_key != "building_flat" and not (
            row["kind"] == "building" and "building_display_meshes" in packet
        ):
            batch["vertices"].extend(row["vertices"])
            batch["faces"].extend([[i + offset for i in face] for face in row["faces"]])
        if row["kind"] not in {
            "utility",
            "restricted",
            "site_border",
            "existing_green",
            "building",
        }:
            batch["lines"].extend(row["lines"])
        elif row["lines"]:
            excluded_linework_ids.append(row["id"])
    for kind, batch in batches.items():
        surface = surfaces.get(kind, surfaces["site_border"])
        if batch["faces"]:
            mesh(f"context:{kind}", batch["vertices"], batch["faces"], surface)
        line(
            f"context:{kind}:lines",
            batch["lines"],
            surface,
            0.06 if kind == "site_border" else 0.025,
        )
    for index, surface in enumerate(packet.get("environment_surfaces", [])):
        if surface["faces"]:
            mesh(
                surface.get("id", f"environment:surface:{index}"),
                surface["vertices"],
                surface["faces"],
                surfaces.get(surface["kind"], surfaces["sidewalk"]),
            )
        bounds.extend(surface["vertices"])
    curb_stones = 0
    if "environment_curb_meshes" in packet:
        for row in packet["environment_curb_meshes"]:
            mesh(row["id"], row["vertices"], row["faces"], surfaces["curb"])
            bounds.extend(row["vertices"])
    else:
        curb_object = style.add_environment_curbs(
            packet.get("environment_curbs", []), surfaces["curb"]
        )
        curb_stones = curb_object["stone_count"] if curb_object is not None else 0
        if curb_object is not None:
            bounds.extend([list(vertex.co) for vertex in curb_object.data.vertices])
    display_buildings = [
        *packet.get("building_display_meshes", []),
        *packet.get("environment_buildings", []),
    ]
    for building in display_buildings:
        mesh(
            building["id"],
            building["vertices"],
            building["faces"],
            surfaces["building"],
            surfaces["building_roof"],
        )
        bounds.extend(building["vertices"])
    flat_surfaces = (
        [] if "building_display_meshes" in packet else packet["flat_building_surfaces"]
    )
    for index, row in enumerate(flat_surfaces):
        mesh(
            f"context:building_flat:{index}",
            row["vertices"],
            row["faces"],
            surfaces["building_flat"],
        )
    terrain = []
    for primitive in snapshot["vertical_primitives"]:
        if (
            primitive["primitive_type"] == "surface_mesh"
            and primitive["terrain_mapping_status"] == "confirmed"
        ):
            mesh(
                primitive["primitive_id"],
                primitive["vertices"],
                primitive["faces"],
                surfaces["lawn"],
            )
            for face in primitive["faces"]:
                for i in range(1, len(face) - 1):
                    terrain.append(
                        [
                            primitive["vertices"][index]
                            for index in (face[0], face[i], face[i + 1])
                        ]
                    )

    def ground(x, y):
        for a, b, c in terrain:
            denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(denominator) < 1e-12:
                continue
            u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
            v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
            if u >= -1e-8 and v >= -1e-8 and u + v <= 1 + 1e-8:
                return u * a[2] + v * b[2] + (1 - u - v) * c[2]
        return 0

    plant_ids, plant_instances = [], []
    loaded = {}
    assignments = {r["object_id"]: r for r in appearance["plants"]}
    for row in snapshot["objects"]:
        x, y = row["local_x"], row["local_y"]
        base = ground(x, y)
        assignment = assignments[row["object_id"]]
        collection, native_height, native_radius = load_plant(
            assignment["path"], loaded
        )
        radius = max(0.18, row["canopy_radius_max_m"])
        minimum, ratio = (1.8, 2.55) if row["kind"] == "tree" else (0.35, 1.25)
        height = max(
            minimum,
            row["height_max_m"] if row["height_max_m"] is not None else radius * ratio,
        )
        obj = bpy.data.objects.new(f"plant:{row['object_id']}", None)
        obj.instance_type = "COLLECTION"
        obj.instance_collection = collection
        obj.location = (x, y, base)
        obj.scale = (
            radius / native_radius,
            radius / native_radius,
            height / native_height,
        )
        obj.rotation_euler.z = (
            int(hashlib.sha256(row["object_id"].encode()).hexdigest()[:8], 16)
            / 0xFFFFFFFF
            * math.tau
        )
        obj["project_object_id"] = row["object_id"]
        bpy.context.collection.objects.link(obj)
        plant_ids.append(row["object_id"])
        plant_instances.append(
            {
                **assignment,
                "location": list(obj.location),
                "scale": list(obj.scale),
                "rotation_z": obj.rotation_euler.z,
                "height_m": height,
                "radius_m": radius,
                "material_names": sorted(
                    {
                        m.name
                        for child in collection.objects
                        if child.type == "MESH"
                        for m in child.data.materials
                        if m
                    }
                ),
            }
        )
        bounds.extend([[x - radius, y - radius, base], [x + radius, y + radius, base]])
    existing_tree_instances = []
    context_assignments = {
        r["object_id"]: r for r in appearance.get("context_plants", [])
    }
    for row in packet.get("environment_trees", []):
        x, y = row["local_x"], row["local_y"]
        base = row.get("base_elevation_m")
        if base is None:
            base = ground(x, y)
        assignment = context_assignments[row["object_id"]]
        collection, native_height, native_radius = load_plant(
            assignment["path"], loaded
        )
        height, radius = row["height_m"], row["radius_m"]
        obj = bpy.data.objects.new(f"existing-tree:{row['object_id']}", None)
        obj.instance_type = "COLLECTION"
        obj.instance_collection = collection
        obj.location = (x, y, base)
        obj.scale = (
            radius / native_radius,
            radius / native_radius,
            height / native_height,
        )
        obj.rotation_euler.z = (
            int(hashlib.sha256(row["object_id"].encode()).hexdigest()[:8], 16)
            / 0xFFFFFFFF
            * math.tau
        )
        obj["context_object_id"] = row["object_id"]
        obj["semantic"] = "existing_vegetation"
        obj["status"] = (
            "source-positioned tree; model and dimensions are appearance proxies"
        )
        bpy.context.collection.objects.link(obj)
        existing_tree_instances.append(
            {
                **row,
                **assignment,
                "location": list(obj.location),
                "scale": list(obj.scale),
                "rotation_z": obj.rotation_euler.z,
                "height_m": height,
                "radius_m": radius,
                "material_names": sorted(
                    {
                        m.name
                        for child in collection.objects
                        if child.type == "MESH"
                        for m in child.data.materials
                        if m
                    }
                ),
            }
        )
        bounds.extend([[x - radius, y - radius, base], [x + radius, y + radius, base]])
    # Reuse indexed surface geometry; never expand every lawn triangle into
    # another Python mesh. Sampling follows the exact exported camera.
    lawn_rows = lawn_surface_rows(packet)
    source = packet["camera"]
    x, y, z = source["position"]
    camera_position = Vector((x, -z, y))
    qx, qy, qz, qw = source["quaternion"]
    camera_rotation = Quaternion((math.sqrt(0.5), math.sqrt(0.5), 0, 0)) @ Quaternion(
        (qw, qx, qy, qz)
    )
    camera_forward = camera_rotation @ Vector((0, 0, -1))
    camera_up = camera_rotation @ Vector((0, 1, 0))
    grass_count = 0
    grass_coverage = {"instances": 0, "budget": 12000}
    if lawn_rows and appearance["grass_paths"]:
        grass_count, _, _, _, grass_coverage = lawn.add_lawn_instances(
            lawn_rows,
            [Path(p) for p in appearance["grass_paths"]],
            camera_position=tuple(camera_position),
            camera_target=tuple(camera_position + camera_forward),
            camera_up=tuple(camera_up),
            vertical_fov_radians=math.radians(source["fov_y_deg"]),
            aspect=packet["width"] / packet["height"],
            near_m=source["near"],
            far_m=source["far"],
            max_instances=12000,
        )
    repair_textures(appearance)
    leaf_tuning = style.tune_stable_leaf_materials()
    facade_details = (
        frozen_facade_details(packet)
        if "building_facade_meshes" in packet
        else building_details(
            [
                *packet.get("building_display_meshes", packet["meshes"]),
                *packet.get("environment_buildings", []),
            ],
            packet["camera"],
        )
    )

    if not terrain and bounds:
        xs, ys = [p[0] for p in bounds], [p[1] for p in bounds]
        padding = max(12, min(80, max(max(xs) - min(xs), max(ys) - min(ys)) * 0.045))
        lo_x, hi_x, lo_y, hi_y = (
            min(xs) - padding,
            max(xs) + padding,
            min(ys) - padding,
            max(ys) + padding,
        )
        mesh(
            "Unmeasured local datum",
            [
                [lo_x, lo_y, -0.01],
                [hi_x, lo_y, -0.01],
                [hi_x, hi_y, -0.01],
                [lo_x, hi_y, -0.01],
            ],
            [[0, 1, 2, 3]],
            style.procedural_material(
                "Unresolved context ground",
                (0.18, 0.21, 0.15),
                (0.23, 0.26, 0.18),
                0.16,
                0.94,
                0.015,
            ),
        )

    camera_data = bpy.data.cameras.new("GA selected camera")
    camera = bpy.data.objects.new("GA selected camera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = camera_position
    camera.rotation_mode = "QUATERNION"
    camera.rotation_quaternion = camera_rotation
    camera_data.sensor_fit = "VERTICAL"
    camera_data.sensor_height = 24
    camera_data.lens = 24 / (2 * math.tan(math.radians(source["fov_y_deg"]) / 2))
    camera_data.clip_start, camera_data.clip_end = source["near"], source["far"]
    scene.camera = camera
    forward = camera.rotation_quaternion @ Vector((0, 0, -1))
    if math.hypot(forward.x, forward.y) < 1e-6:
        forward = Vector((0, 1, 0))
    style.add_sky_backdrop(
        tuple(camera.location),
        tuple(camera.location + forward * 100),
        style.sky_backdrop_material("native_v1"),
        distance_m=min(520, source["far"] * 0.72),
    )
    world = bpy.data.worlds.new("GA daylight")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (
        0.83,
        0.90,
        1.0,
        1,
    )
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
    scene.world = world
    sun_data = bpy.data.lights.new("Daylight", "SUN")
    sun_data.energy = 2.0
    sun_data.angle = math.radians(2.5)
    sun = bpy.data.objects.new("Daylight", sun_data)
    bpy.context.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(38), 0, math.radians(215))
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.view_settings.exposure = 0.45
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 64
    scene.cycles.seed = 271828
    scene.cycles.adaptive_threshold = 0.012
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = (
        packet["width"],
        packet["height"],
    )
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(image_path)
    bpy.ops.render.render(write_still=True)
    receipt = {
        "schema": "green-atlas.project-render-receipt/1",
        "packet_sha256": hashlib.sha256(data).hexdigest(),
        "renderer": "Blender",
        "blender_version": bpy.app.version_string,
        "camera": source,
        "width": packet["width"],
        "height": packet["height"],
        "plan_version": snapshot["plan_version"],
        "geometry_version": snapshot["geometry_version"],
        "source_sha256": snapshot["source_sha256"],
        "horizon_year": snapshot["horizon_year"],
        "plant_ids": plant_ids,
        "context_ids": [row["id"] for row in packet["meshes"]],
        "terrain_status": snapshot["terrain_status"],
        "plant_representation": "botaniq_collection_instances",
        "appearance_profile": appearance["profile"],
        "lighting_profile": appearance["lighting"],
        "appearance_bundle_sha256": appearance["bundle_sha256"],
        "appearance_files": appearance["files"],
        "plant_instances": plant_instances,
        "lawn_instances": grass_count,
        "lawn_coverage": grass_coverage,
        "leaf_material_tuning": leaf_tuning,
        "building_details": facade_details,
        "building_heights": packet.get("building_heights"),
        "environment": {
            **packet.get("environment", {}),
            "curb_stones": curb_stones,
            "existing_tree_instances": existing_tree_instances,
        },
        "source_packet_sha256": packet.get("source_packet_sha256"),
        "sky_background": "historical native_v1 procedural clouds",
        "material_names": sorted(m.name for m in bpy.data.materials),
        "linework_policy": "beauty: technical contours stay in the packet, not in the image",
        "excluded_linework_ids": excluded_linework_ids,
        "renderer_script_sha256": {
            name: style.sha256(Path(__file__).with_name(name))
            for name in (
                "blender_worker.py",
                "render_style.py",
                "lawn_instances.py",
                "lawn_scatter.py",
            )
        },
        "lighting": {
            "world_color": [0.83, 0.90, 1.0],
            "world_strength": 1.0,
            "sun_energy": 2.0,
            "sun_angle_deg": 2.5,
            "sun_rotation_deg": [38, 0, 215],
            "exposure": 0.45,
            "hdri_used": False,
        },
        "data_gaps": snapshot["data_gaps"],
    }
    (image_path.parent / "renderer-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
