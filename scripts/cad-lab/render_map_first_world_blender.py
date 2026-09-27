"""Render the map-first Kustanayskaya world as a 3D data diagnostic.

Run with Blender, passing arguments after ``--``.  The image is intentionally
semantic: unknown building heights stay as flat footprints and estimated road
widths remain visually distinguishable.  It is not admitted as a beauty render.
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def material(name: str, color: tuple[float, float, float, float], roughness: float = 0.7, metallic: float = 0.0) -> bpy.types.Material:
    value = bpy.data.materials.new(name)
    value.diffuse_color = color
    value.use_nodes = True
    principled = value.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = color
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["Metallic"].default_value = metallic
    return value


def polygon_rings(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry["type"] == "MultiPolygon":
        return [polygon[0] for polygon in geometry["coordinates"]]
    return []


def geometry_within_bounds(geometry: dict, bounds: list[float]) -> bool:
    points = [point for ring in polygon_rings(geometry) for point in ring]
    if not points:
        return False
    min_x, min_y, max_x, max_y = (float(value) for value in bounds)
    return all(
        min_x <= float(point[0]) <= max_x and min_y <= float(point[1]) <= max_y
        for point in points
    )


def make_surface(name: str, geometry: dict, z_at, z_offset: float, mat: bpy.types.Material) -> list[bpy.types.Object]:
    objects = []
    for index, ring in enumerate(polygon_rings(geometry)):
        points = ring[:-1] if len(ring) > 1 and ring[0][:2] == ring[-1][:2] else ring
        if len(points) < 3:
            continue
        vertices = [(float(point[0]), float(point[1]), z_at(float(point[0]), float(point[1])) + z_offset) for point in points]
        mesh = bpy.data.meshes.new(f"{name}_{index}_mesh")
        mesh.from_pydata(vertices, [], [list(range(len(vertices)))])
        mesh.materials.append(mat)
        mesh.update()
        obj = bpy.data.objects.new(f"{name}_{index}", mesh)
        bpy.context.collection.objects.link(obj)
        objects.append(obj)
    return objects


def make_building(
    name: str,
    geometry: dict,
    base_z: float,
    height: float,
    mat: bpy.types.Material,
    foundation_z: float | None = None,
) -> list[bpy.types.Object]:
    objects = []
    for index, ring in enumerate(polygon_rings(geometry)):
        points = ring[:-1] if len(ring) > 1 and ring[0][:2] == ring[-1][:2] else ring
        if len(points) < 3:
            continue
        count = len(points)
        lower_z = min(base_z, foundation_z if foundation_z is not None else base_z)
        vertices = [(float(p[0]), float(p[1]), lower_z) for p in points]
        vertices += [(float(p[0]), float(p[1]), base_z + height) for p in points]
        faces = [list(reversed(range(count))), list(range(count, count * 2))]
        for vertex in range(count):
            nxt = (vertex + 1) % count
            faces.append([vertex, nxt, count + nxt, count + vertex])
        mesh = bpy.data.meshes.new(f"{name}_{index}_mesh")
        mesh.from_pydata(vertices, [], faces)
        mesh.materials.append(mat)
        mesh.update()
        obj = bpy.data.objects.new(f"{name}_{index}", mesh)
        bpy.context.collection.objects.link(obj)
        objects.append(obj)
    return objects


def make_triangle_layer(name: str, triangles: list, mat: bpy.types.Material) -> bpy.types.Object | None:
    if not triangles:
        return None
    vertices = []
    faces = []
    for triangle in triangles:
        offset = len(vertices)
        vertices.extend(tuple(float(value) for value in point) for point in triangle)
        faces.append([offset, offset + 1, offset + 2])
    mesh = bpy.data.meshes.new(f"{name}_mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(mat)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def add_cube(name: str, location: tuple[float, float, float], scale: tuple[float, float, float], mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = (scale[0] / 2.0, scale[1] / 2.0, scale[2] / 2.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.data.materials.append(mat)
    return obj


def add_cylinder(name: str, location: tuple[float, float, float], radius: float, depth: float, mat: bpy.types.Material) -> bpy.types.Object:
    bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=radius, depth=depth, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(mat)
    return obj


def look_at(obj: bpy.types.Object, target: tuple[float, float, float]) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def point_along_polyline(points: list[list[float]], distance: float) -> tuple[float, float, float, float]:
    remaining = max(0.0, distance)
    for start, end in zip(points, points[1:]):
        dx, dy = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        if remaining <= length:
            ratio = remaining / length
            return float(start[0]) + dx * ratio, float(start[1]) + dy * ratio, dx / length, dy / length
        remaining -= length
    start, end = points[-2], points[-1]
    dx, dy = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
    length = max(math.hypot(dx, dy), 1e-9)
    return float(end[0]), float(end[1]), dx / length, dy / length


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--world", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--samples", type=int, default=96)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else [])
    args.output.mkdir(parents=True, exist_ok=True)
    packet = json.loads(args.world.read_text())
    bounds = packet["scope"]["local_bounds_m"]
    center_x = (float(bounds[0]) + float(bounds[2])) * 0.5
    center_y = (float(bounds[1]) + float(bounds[3])) * 0.5
    extent_x = float(bounds[2]) - float(bounds[0])
    extent_y = float(bounds[3]) - float(bounds[1])

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.render.filepath = str((args.output / "map-first-3d-diagnostic.png").resolve())
    scene.render.image_settings.color_depth = "8"
    scene.render.resolution_percentage = 100
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.use_file_extension = True
    scene.render.film_transparent = False
    try:
        scene.eevee.taa_render_samples = args.samples
    except AttributeError:
        pass
    scene.view_settings.look = "AgX - Medium Low Contrast"
    scene.view_settings.exposure = 0.1

    terrain = packet["terrain"]
    rows, columns = int(terrain["rows"]), int(terrain["columns"])
    terrain_vertices = [tuple(item["xyz_local_m"]) for item in terrain["vertices"]]
    xs = [terrain_vertices[column][0] for column in range(columns)]
    ys = [terrain_vertices[row * columns][1] for row in range(rows)]

    def z_at(x: float, y: float) -> float:
        fx = (x - xs[0]) / (xs[-1] - xs[0]) * (columns - 1)
        fy = (y - ys[0]) / (ys[-1] - ys[0]) * (rows - 1)
        x0 = min(max(math.floor(fx), 0), columns - 2)
        y0 = min(max(math.floor(fy), 0), rows - 2)
        tx = min(max(fx - x0, 0.0), 1.0)
        ty = min(max(fy - y0, 0.0), 1.0)
        a = terrain_vertices[y0 * columns + x0][2]
        b = terrain_vertices[y0 * columns + x0 + 1][2]
        c = terrain_vertices[(y0 + 1) * columns + x0][2]
        d = terrain_vertices[(y0 + 1) * columns + x0 + 1][2]
        return (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty

    mats = {
        "terrain": material("Source topography terrain", (0.42, 0.50, 0.39, 1), 0.95),
        "road_estimate": material("Road width estimated", (0.165, 0.18, 0.19, 1), 0.83),
        "road_explicit": material("Road width explicit", (0.11, 0.12, 0.13, 1), 0.82),
        "road_corroborated": material("Road width satellite corroborated", (0.105, 0.115, 0.12, 1), 0.82),
        "grass": material("Mapped grass", (0.24, 0.42, 0.18, 1), 0.92),
        "wood": material("Mapped woodland", (0.12, 0.30, 0.10, 1), 0.95),
        "playground": material("Mapped playground", (0.46, 0.32, 0.18, 1), 0.82),
        "building_explicit": material("Building explicit height", (0.79, 0.78, 0.74, 1), 0.72),
        "building_floors": material("Building estimated from floors", (0.68, 0.68, 0.65, 1), 0.75),
        "building_retail": material("Retail appearance from KartaView", (0.12, 0.14, 0.15, 1), 0.54, 0.08),
        "building_unknown": material("Building height unknown", (0.72, 0.38, 0.17, 1), 0.8),
        "furniture": material("Mapped furniture proxy", (0.13, 0.23, 0.25, 1), 0.62, 0.1),
        "bench": material("Mapped bench proxy", (0.28, 0.12, 0.045, 1), 0.72),
        "crossing": material("Mapped crossing point", (0.9, 0.84, 0.43, 1), 0.7),
    }

    terrain_mesh = bpy.data.meshes.new("terrain_mesh")
    terrain_mesh.from_pydata(terrain_vertices, [], terrain["triangles"])
    terrain_mesh.materials.append(mats["terrain"])
    terrain_mesh.update()
    terrain_obj = bpy.data.objects.new("Source_topography_ground", terrain_mesh)
    bpy.context.collection.objects.link(terrain_obj)

    layer_materials = {
        "woodland": mats["wood"],
        "grass": mats["grass"],
        "recreation": mats["playground"],
        "road_explicit": mats["road_explicit"],
        "road_corroborated": mats["road_corroborated"],
        "road_estimated": mats["road_estimate"],
    }
    for layer in packet["render_layers"]:
        make_triangle_layer(layer["id"], layer["triangles_xyz_local_m"], layer_materials[layer["semantic"]])

    extruded = unknown = 0
    edge_culled_buildings = 0
    foundation_skirts = 0
    maximum_foundation_skirt_m = 0.0
    for feature in packet["buildings"]:
        if not geometry_within_bounds(feature["geometry_local"], bounds):
            edge_culled_buildings += 1
            continue
        props = feature["properties"]
        ground = float(props["ground_z_local_m"])
        height = props["height_m"]
        if height is None:
            make_surface(
                feature["id"].replace(":", "_"),
                feature["geometry_local"],
                z_at,
                0.12,
                mats["building_unknown"],
            )
            unknown += 1
        else:
            if props.get("class") == "retail" and props.get("height_evidence"):
                mat = mats["building_retail"]
            else:
                mat = mats["building_explicit"] if props["height_status"] == "cartographic_explicit" else mats["building_floors"]
            foundation_z = float(props.get("ground_z_min_local_m", ground)) - 0.15
            make_building(
                feature["id"].replace(":", "_"),
                feature["geometry_local"],
                ground,
                float(height),
                mat,
                foundation_z,
            )
            foundation_skirts += 1
            maximum_foundation_skirt_m = max(
                maximum_foundation_skirt_m, ground - foundation_z
            )
            extruded += 1

    proxy_count = 0
    for feature in packet["infrastructure"]:
        geometry = feature["geometry_local"]
        if geometry["type"] != "Point":
            continue
        cls = feature["properties"]["class"]
        x, y = geometry["coordinates"][:2]
        ground = float(feature["properties"]["ground_z_local_m"])
        safe_name = feature["id"].replace(":", "_")
        if cls == "bench":
            add_cube(safe_name, (x, y, ground + 0.30), (1.55, 0.48, 0.60), mats["bench"])
        elif cls in {"waste_basket", "waste_disposal", "recycling"}:
            add_cylinder(safe_name, (x, y, ground + 0.42), 0.25, 0.84, mats["furniture"])
        elif cls in {"crossing", "stop_position"}:
            add_cylinder(safe_name, (x, y, ground + 0.13), 0.48, 0.20, mats["crossing"])
        elif cls in {"bus_stop", "entrance", "gate", "atm"}:
            add_cube(safe_name, (x, y, ground + 0.95), (0.32, 0.32, 1.9), mats["furniture"])
        else:
            continue
        proxy_count += 1

    inventory_tree_records = inventory_shrub_records = inventory_stumps = 0
    inventory_missing_height = 0
    inventory_outside_render_window = 0
    asset_family_counts: dict[str, int] = {}
    inventory = packet.get("inventory_vegetation") or []
    if inventory:
        # Reuse the already verified free BlenderKit assets. The inventory's
        # original species remains on every object; the three-family mapping is
        # only a visual prototype.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from render_geospatial_context_blender import load_tree_instance

        shrub_mat = material("Inventory shrub groups", (0.11, 0.28, 0.07, 1), 0.86)
        stump_mat = material("Inventory stumps", (0.22, 0.10, 0.035, 1), 0.88)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=1.0, location=(0, 0, -10000))
        shrub_template = bpy.context.object
        shrub_template.name = "inventory shrub shared mesh template"
        shrub_template.data.materials.append(shrub_mat)
        shrub_template.hide_render = True
        shrub_template.hide_viewport = True

        def visual_family(species: str) -> str:
            value = species.casefold().replace("ё", "е")
            if "берез" in value:
                return "Береза"
            if any(token in value for token in ("ель", "сосн", "пихт", "туя", "можжев", "листвен")):
                return "Ель"
            return "Клен"

        for row in inventory:
            x, y, ground = (float(value) for value in row["position_local_xyz_m"])
            # The full 1278-record corridor is already present in the world and
            # fast SVG audit. Loading every high-detail foliage asset at once is
            # not useful for an evening prototype, so the 3D frame uses a fixed
            # local LOD window around the corridor midpoint.
            if math.hypot(x - center_x, y - center_y) > 120.0:
                inventory_outside_render_window += 1
                continue
            height = row.get("height_m")
            if height is None or float(height) <= 0:
                inventory_missing_height += 1
                continue
            height = float(height)
            if row["kind"] == "tree":
                family = visual_family(row["species"])
                instance = load_tree_instance(family, [x, y], height, ground, int(row["inventory_id"]))
                if instance is None:
                    inventory_missing_height += 1
                    continue
                instance.name = f"inventory {row['inventory_id']} / {row['species']}"
                instance["inventory_id"] = int(row["inventory_id"])
                instance["inventory_species"] = row["species"]
                instance["inventory_count"] = int(row["count"])
                instance["position_status"] = row["position_status"]
                inventory_tree_records += 1
                asset_family_counts[family] = asset_family_counts.get(family, 0) + 1
            elif row["kind"] == "shrub_group":
                count = max(int(row["count"]), 1)
                radius = min(max(0.55 + math.sqrt(count) * 0.16, 0.65), 4.5)
                obj = bpy.data.objects.new(f"inventory {row['inventory_id']} / {row['species']}", shrub_template.data)
                obj.location = (x, y, ground + height * 0.5)
                obj.scale = (radius, radius * 0.78, max(height * 0.5, 0.12))
                obj.rotation_euler.z = (int(row["inventory_id"]) * 2.399963229728653) % math.tau
                obj["inventory_id"] = int(row["inventory_id"])
                obj["inventory_species"] = row["species"]
                obj["inventory_count"] = count
                obj["position_status"] = row["position_status"]
                bpy.context.collection.objects.link(obj)
                inventory_shrub_records += 1
            else:
                add_cylinder(
                    f"inventory {row['inventory_id']} / stump",
                    (x, y, ground + max(height, 0.2) * 0.5),
                    max(float(row.get("diameter_cm") or 20.0) / 200.0, 0.08),
                    max(height, 0.2),
                    stump_mat,
                )
                inventory_stumps += 1

    world = bpy.data.worlds.new("neutral daylight")
    scene.world = world
    world.use_nodes = True
    background = world.node_tree.nodes.get("Background")
    background.inputs["Color"].default_value = (0.55, 0.65, 0.78, 1)
    background.inputs["Strength"].default_value = 0.55

    bpy.ops.object.light_add(type="SUN", location=(-100, -130, 240))
    sun = bpy.context.object
    sun.name = "Sun"
    sun.data.energy = 2.1
    sun.data.angle = math.radians(4.0)
    sun.rotation_euler = (math.radians(28), math.radians(-22), math.radians(-38))
    bpy.ops.object.light_add(type="AREA", location=(-80, -80, 180))
    area = bpy.context.object
    area.name = "Sky fill"
    area.data.energy = 1800
    area.data.shape = "DISK"
    area.data.size = 160
    look_at(area, (0, 0, 0))

    bpy.ops.object.camera_add(location=(center_x + extent_x * 0.68, center_y - extent_y * 0.58, max(extent_x, extent_y) * 0.72))
    camera = bpy.context.object
    camera.name = "Oblique data audit camera"
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = max(extent_y * 1.13, extent_x * 1.48)
    camera.data.lens = 52
    look_at(camera, (center_x, center_y, 5))
    scene.camera = camera

    aerial_path = args.output / "map-first-3d-diagnostic.png"
    scene.render.filepath = str(aerial_path.resolve())
    bpy.ops.render.render(write_still=True)

    street_path = args.output / "map-first-street-diagnostic.png"
    street_axis = None
    candidates = [
        row for row in packet.get("named_reference_axes", [])
        if row.get("name") == "Кустанайская улица" and row.get("project_geometry_local")
    ]
    if candidates:
        street_axis = max(candidates, key=lambda row: float(row.get("length_in_project_m") or 0.0))
        points = street_axis["project_geometry_local"]["coordinates"]
        total = sum(math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1])) for a, b in zip(points, points[1:]))
        # The midpoint matches the fixed high-detail vegetation LOD window.
        forward = point_along_polyline(points, total * 0.48)
        reverse_raw = point_along_polyline(points, total * 0.52)
        reverse = (reverse_raw[0], reverse_raw[1], -reverse_raw[2], -reverse_raw[3])

        known_building_centers = []
        for building in packet["buildings"]:
            height = building["properties"].get("height_m")
            rings = polygon_rings(building["geometry_local"])
            if height is None or not rings:
                continue
            ring = rings[0][:-1] if rings[0][0][:2] == rings[0][-1][:2] else rings[0]
            known_building_centers.append((
                sum(float(point[0]) for point in ring) / len(ring),
                sum(float(point[1]) for point in ring) / len(ring),
                float(height),
            ))

        def view_score(candidate):
            x, y, dx, dy = candidate
            score = 0.0
            for center_x, center_y, height in known_building_centers:
                vx, vy = center_x - x, center_y - y
                distance = math.hypot(vx, vy)
                if distance <= 1.0 or distance > 280.0:
                    continue
                cosine = (vx * dx + vy * dy) / distance
                if cosine > 0.25:
                    score += cosine * min(height, 40.0) / distance
            return score

        camera_x, camera_y, direction_x, direction_y = max((forward, reverse), key=view_score)
        target_x = camera_x + direction_x * 95.0
        target_y = camera_y + direction_y * 95.0
        # A small lane offset keeps the view out of the exact centerline.  It is
        # camera placement only and never enters the world geometry.
        camera_x += -direction_y * 2.1
        camera_y += direction_x * 2.1
        camera.location = (camera_x, camera_y, z_at(camera_x, camera_y) + 1.72)
        camera.data.type = "PERSP"
        camera.data.lens = 29
        camera.data.sensor_width = 36
        look_at(camera, (target_x, target_y, z_at(target_x, target_y) + 1.65))
        scene.render.filepath = str(street_path.resolve())
        bpy.ops.render.render(write_still=True)

    evidence_path = args.output / "map-first-kartaview-camera-diagnostic.png"
    evidence_camera = None
    if args.evidence and args.evidence.exists():
        evidence = json.loads(args.evidence.read_text())
        evidence_buildings = [row for row in packet["buildings"] if row["properties"].get("height_evidence")]
        if evidence_buildings and evidence.get("frames"):
            rings = polygon_rings(evidence_buildings[0]["geometry_local"])
            ring = rings[0][:-1] if rings[0][0][:2] == rings[0][-1][:2] else rings[0]
            building_x = sum(float(point[0]) for point in ring) / len(ring)
            building_y = sum(float(point[1]) for point in ring) / len(ring)

            def evidence_score(frame):
                x, y = frame["camera"]["local_xy_m"]
                dx, dy = building_x - float(x), building_y - float(y)
                bearing = math.degrees(math.atan2(dx, dy)) % 360.0
                heading = float(frame["camera"]["heading_deg"]) % 360.0
                difference = abs((bearing - heading + 180.0) % 360.0 - 180.0)
                return difference + math.hypot(dx, dy) * 0.02

            evidence_camera = min(evidence["frames"], key=evidence_score)
            camera_x, camera_y = (float(value) for value in evidence_camera["camera"]["local_xy_m"])
            heading = math.radians(float(evidence_camera["camera"]["heading_deg"]))
            direction_x, direction_y = math.sin(heading), math.cos(heading)
            target_x, target_y = camera_x + direction_x * 65.0, camera_y + direction_y * 65.0
            camera.location = (camera_x, camera_y, z_at(camera_x, camera_y) + 1.65)
            camera.data.type = "PERSP"
            # KartaView metadata reports fieldOfView=0 for this GoPro sequence.
            # 18 mm is a review-only wide-angle proxy, recorded in the receipt.
            camera.data.lens = 18
            camera.data.sensor_width = 36
            look_at(camera, (target_x, target_y, z_at(target_x, target_y) + 1.65))
            scene.render.filepath = str(evidence_path.resolve())
            bpy.ops.render.render(write_still=True)

    bpy.ops.wm.save_as_mainfile(filepath=str((args.output / "map-first-world.blend").resolve()))

    receipt = {
        "schema": "green-atlas.map-first-render-receipt.v1",
        "input": {"path": str(args.world.resolve()), "sha256": sha256(args.world)},
        "output": {
            "aerial_image": str(aerial_path.resolve()),
            "street_image": str(street_path.resolve()) if street_axis is not None else None,
            "kartaview_camera_image": str(evidence_path.resolve()) if evidence_camera is not None else None,
            "blend": str((args.output / "map-first-world.blend").resolve()),
        },
        "engine": "BLENDER_EEVEE_NEXT",
        "resolution": [1280, 900],
        "extruded_buildings": extruded,
        "unknown_height_footprints": unknown,
        "edge_culled_buildings": edge_culled_buildings,
        "building_grounding": {
            "foundation_skirts": foundation_skirts,
            "maximum_skirt_depth_m": round(maximum_foundation_skirt_m, 6),
            "unknown_height_footprints": "draped to the active terrain surface",
        },
        "infrastructure_proxies": proxy_count,
        "inventory": {
            "source_records": len(inventory),
            "tree_records_rendered": inventory_tree_records,
            "shrub_group_records_rendered": inventory_shrub_records,
            "stumps_rendered": inventory_stumps,
            "missing_or_invalid_height": inventory_missing_height,
            "outside_120m_render_window": inventory_outside_render_window,
            "visual_asset_family_counts": asset_family_counts,
            "asset_mapping_status": "three-family visual prototype; original species and measured height preserved on objects",
        },
        "street_axis": street_axis,
        "kartaview_camera": {
            "photo_id": evidence_camera["photo_id"],
            "sequence_index": evidence_camera["sequence_index"],
            "local_xy_m": evidence_camera["camera"]["local_xy_m"],
            "heading_deg": evidence_camera["camera"]["heading_deg"],
            "camera_height_m": 1.65,
            "lens_mm_proxy": 18,
            "status": "position_and_heading_source_backed; height_and_lens_review_proxies",
        } if evidence_camera is not None else None,
        "status": "3d_data_diagnostic_not_beauty",
    }
    (args.output / "render-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
