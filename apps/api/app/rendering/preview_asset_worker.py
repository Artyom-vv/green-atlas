"""Blender-only conversion of frozen Botaniq collections to local browser LODs."""

import hashlib
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_style as style


def asset_id(cache_key, key):
    return hashlib.sha256((cache_key + ":" + key).encode()).hexdigest()


def bounds(objects, mesh_points=False):
    points = [
        obj.matrix_world @ Vector(point)
        for obj in objects
        for point in (
            [v.co for v in obj.data.vertices] if mesh_points else obj.bound_box
        )
    ]
    if not points:
        raise ValueError("Botaniq collection contains no mesh")
    return (
        Vector(tuple(min(p[i] for p in points) for i in range(3))),
        Vector(tuple(max(p[i] for p in points) for i in range(3))),
    )


def repair_images(appearance, maximum, output_directory):
    lookup = {Path(row["path"]).name: row["path"] for row in appearance["files"]}
    for image in bpy.data.images:
        if image.source != "FILE":
            continue
        name = Path(image.filepath.replace("\\", "/")).name
        if name in lookup:
            image.filepath = lookup[name]
            image.reload()
        elif (
            not image.packed_file
            and not Path(bpy.path.abspath(image.filepath)).is_file()
        ):
            raise ValueError("Botaniq material texture is missing: " + name)
        if "normal" in image.name.lower():
            image.colorspace_settings.name = "Non-Color"
        width, height = image.size
        if max(width, height) > maximum:
            scale = maximum / max(width, height)
            image.scale(max(1, round(width * scale)), max(1, round(height * scale)))
        # Exported normals may reuse the original encoded JPEG despite memory
        # resizing. Pack locally saved derivatives, never the frozen source.
        derivatives = Path(output_directory) / "model-textures"
        derivatives.mkdir(exist_ok=True)
        extension = ".png" if ".png" in image.name.lower() else ".jpg"
        image.filepath_raw = str(
            derivatives / (hashlib.sha256(image.name.encode()).hexdigest() + extension)
        )
        image.file_format = "PNG" if extension == ".png" else "JPEG"
        image.save()
        image.pack()


def browser_material(source):
    images = [
        n.image for n in source.node_tree.nodes if n.type == "TEX_IMAGE" and n.image
    ]
    diffuse = next(
        (
            i
            for i in images
            if "diffuse" in i.name.lower() or "diffuse" in i.filepath.lower()
        ),
        None,
    )
    normal = next(
        (
            i
            for i in images
            if "normal" in i.name.lower() or "normal" in i.filepath.lower()
        ),
        None,
    )
    leaf = "leaf" in source.name.lower() or "flower" in source.name.lower()
    target = bpy.data.materials.new(source.name + " browser")
    target.use_nodes = True
    target.use_backface_culling = False
    nodes, links = target.node_tree.nodes, target.node_tree.links
    shader = nodes.get("Principled BSDF")
    shader.inputs["Roughness"].default_value = 0.82 if leaf else 0.92
    shader.inputs["Base Color"].default_value = (
        (0.12, 0.20, 0.05, 1) if leaf else (0.20, 0.14, 0.09, 1)
    )
    if diffuse:
        node = nodes.new("ShaderNodeTexImage")
        node.image = diffuse
        links.new(node.outputs["Color"], shader.inputs["Base Color"])
        if leaf and diffuse.channels == 4:
            target.surface_render_method = "DITHERED"
            target.alpha_threshold = 0.42
            cutoff = nodes.new("ShaderNodeMath")
            cutoff.operation = "GREATER_THAN"
            cutoff.inputs[1].default_value = 0.42
            links.new(node.outputs["Alpha"], cutoff.inputs[0])
            links.new(cutoff.outputs["Value"], shader.inputs["Alpha"])
    if normal:
        node = nodes.new("ShaderNodeTexImage")
        node.image = normal
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.inputs["Strength"].default_value = 0.25
        links.new(node.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], shader.inputs["Normal"])
    return target


def leaf_components(mesh):
    """Find source leaf islands; disconnected cards have a decimation floor."""
    neighbors = [[] for _ in mesh.vertices]
    for edge in mesh.edges:
        a, b = edge.vertices
        neighbors[a].append(b)
        neighbors[b].append(a)
    groups, component = [], [-1] * len(mesh.vertices)
    for index in range(len(mesh.vertices)):
        if component[index] != -1:
            continue
        group, stack = [], [index]
        identifier = len(groups)
        component[index] = identifier
        while stack:
            current = stack.pop()
            group.append(current)
            for neighbor in neighbors[current]:
                if component[neighbor] == -1:
                    component[neighbor] = identifier
                    stack.append(neighbor)
        groups.append({"vertices": group, "triangles": 0, "leaf": True})
    for polygon in mesh.polygons:
        group = groups[component[polygon.vertices[0]]]
        group["triangles"] += len(polygon.vertices) - 2
        material = mesh.materials[polygon.material_index]
        group["leaf"] = group["leaf"] and bool(
            material
            and ("leaf" in material.name.lower() or "flower" in material.name.lower())
        )
    return groups, component


def sampled_mesh(original, groups, component, budget):
    """Retain native branches and source leaf geometry distributed through crown.

    Uniform deterministic leaf-island sampling makes distant LODs genuinely
    cheaper; collapse alone cannot reduce disconnected leaf triangles. Crown
    extreme islands are retained and all native branch components stay.
    """
    leaves = [
        i for i, g in enumerate(groups) if g["leaf"] and len(g["vertices"]) <= 128
    ]
    twigs = [
        i for i, g in enumerate(groups) if not g["leaf"] and len(g["vertices"]) <= 128
    ]
    fixed = set(range(len(groups))) - set(leaves) - set(twigs)
    fixed_triangles = sum(groups[i]["triangles"] for i in fixed)
    leaf_triangles = sum(groups[i]["triangles"] for i in leaves)
    twig_triangles = sum(groups[i]["triangles"] for i in twigs)
    desired = max(1, round(budget * 0.8))
    twig_budget = max(1, budget - desired - min(fixed_triangles, budget // 5))
    fraction = min(1, desired / max(1, leaf_triangles))
    if fraction >= 1 and twig_triangles <= twig_budget:
        return original.data.copy(), 1.0
    # Preserve the six extreme source vertices, including the crown outline.
    critical = set()
    for axis in range(3):
        for maximum in (False, True):
            index = (max if maximum else min)(
                range(len(original.data.vertices)),
                key=lambda i: original.data.vertices[i].co[axis],
            )
            critical.add(component[index])
    selected = set(critical) | fixed
    for candidates, limit in ((leaves, desired), (twigs, twig_budget)):
        ordered = sorted(
            candidates,
            key=lambda i: hashlib.sha256(
                str(
                    tuple(
                        round(
                            float(
                                sum(
                                    original.data.vertices[v].co[a]
                                    for v in groups[i]["vertices"]
                                )
                                / len(groups[i]["vertices"])
                            ),
                            4,
                        )
                        for a in range(3)
                    )
                ).encode()
            ).digest(),
        )
        count = 0
        for identifier in ordered:
            if count >= limit:
                break
            selected.add(identifier)
            count += groups[identifier]["triangles"]
    mapping, vertices = {}, []
    # Preserve total projected foliage area when retaining fewer source cards.
    # Sampling without this compensation produces nearly bare poles at street
    # distances, despite retaining nominal crown extrema and many triangles.
    coverage_scale = min(8.0, math.sqrt(1.0 / max(fraction, 0.01)))
    centres = {
        i: sum((original.data.vertices[v].co for v in groups[i]["vertices"]), Vector())
        / len(groups[i]["vertices"])
        for i in selected
        if groups[i]["leaf"]
    }
    for index, vertex in enumerate(original.data.vertices):
        if component[index] in selected:
            mapping[index] = len(vertices)
            centre = centres.get(component[index])
            point = (
                vertex.co
                if centre is None
                else centre + (vertex.co - centre) * coverage_scale
            )
            vertices.append(tuple(point))
    faces, material_indices, loops = [], [], []
    for polygon in original.data.polygons:
        if component[polygon.vertices[0]] in selected:
            faces.append([mapping[i] for i in polygon.vertices])
            material_indices.append(polygon.material_index)
            loops.extend(polygon.loop_indices)
    mesh = bpy.data.meshes.new(original.name + " sampled native leaves")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    for material in original.data.materials:
        mesh.materials.append(material)
    for polygon, material_index in zip(mesh.polygons, material_indices):
        polygon.material_index = material_index
        polygon.use_smooth = True
    for source in original.data.uv_layers:
        target = mesh.uv_layers.new(name=source.name)
        for row, source_index in zip(target.data, loops):
            row.uv = source.data[source_index].uv
    return mesh, fraction


def export_model(relative, path, request, assets):
    collection, _height = style.load_collection_asset(
        Path(path), "Browser original", "vegetation"
    )
    originals = [o for o in collection.objects if o.type == "MESH"]
    lo, hi = bounds(originals)
    native_height = max(0.01, hi.z - lo.z)
    native_radius = max(0.01, hi.x - lo.x, hi.y - lo.y) / 2
    origin = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    repair_images(
        request["appearance"],
        request["settings"]["model_texture_pixels"],
        request["output_directory"],
    )
    materials = {}
    for obj in originals:
        for m in obj.data.materials:
            if m and m.name not in materials:
                materials[m.name] = browser_material(m)
    total_triangles = 0
    components = {}
    for obj in originals:
        obj.data.calc_loop_triangles()
        total_triangles += len(obj.data.loop_triangles)
        components[obj.name] = leaf_components(obj.data)
    for lod in ("near", "far"):
        bpy.ops.object.select_all(action="DESELECT")
        children = []
        budget = request["settings"][lod + "_triangles"]
        # Both tiers retain real crown volume and use identical PBR materials.
        # Camera-dependent whole-tree cards flatten aerial views, intersect at
        # street level, and bake a different colour/lighting from the near mesh.
        for original in originals:
            child = original.copy()
            original.data.calc_loop_triangles()
            object_budget = max(
                100, round(budget * len(original.data.loop_triangles) / total_triangles)
            )
            child.data, _retained_leaf_fraction = sampled_mesh(
                original, *components[original.name], object_budget
            )
            child.name = original.name + " " + lod
            bpy.context.collection.objects.link(child)
            # Bake the exact original matrix and instance-offset normalization.
            transform = (
                Matrix.Diagonal(
                    (1 / native_radius, 1 / native_radius, 1 / native_height, 1)
                )
                @ Matrix.Translation(-origin)
                @ original.matrix_world
            )
            child.data.transform(transform)
            child.parent = None
            child.matrix_world = Matrix.Identity(4)
            # Replacing slots preserves per-face leaf/bark indices. Clearing
            # the slot array resets every face to bark material zero.
            for index, material in enumerate(original.data.materials):
                if material:
                    child.data.materials[index] = materials[material.name]
            child.select_set(True)
            bpy.context.view_layer.objects.active = child
            child.data.calc_loop_triangles()
            child_triangles = len(child.data.loop_triangles)
            if child_triangles > object_budget:
                decimate = child.modifiers.new("Browser silhouette LOD", "DECIMATE")
                decimate.ratio = max(0.005, object_budget / child_triangles)
                decimate.use_collapse_triangulate = True
                bpy.ops.object.modifier_apply(modifier=decimate.name)
            # Collapse may slightly overshoot extents; keep the native envelope.
            dimensions = (native_radius, native_radius, native_height)
            lower = Vector(tuple((lo[a] - origin[a]) / dimensions[a] for a in range(3)))
            upper = Vector(tuple((hi[a] - origin[a]) / dimensions[a] for a in range(3)))
            for vertex in child.data.vertices:
                vertex.co = Vector(
                    tuple(max(lower[a], min(upper[a], vertex.co[a])) for a in range(3))
                )
            child.data.update()
            children.append(child)
        final_lo, final_hi = bounds(children, mesh_points=True)
        triangles = 0
        for child in children:
            child.data.calc_loop_triangles()
            triangles += len(child.data.loop_triangles)
        key = relative + ":" + lod
        identifier = asset_id(request["cache_key"], key)
        filename = identifier + ".glb"
        output = Path(request["output_directory"]) / filename
        bpy.ops.export_scene.gltf(
            filepath=str(output),
            export_format="GLB",
            use_selection=True,
            export_yup=True,
            export_apply=True,
            export_animations=False,
            export_cameras=False,
            export_lights=False,
            export_image_format="AUTO",
            export_image_quality=80,
        )
        assets.append(
            {
                "id": identifier,
                "key": key,
                "filename": filename,
                "kind": "model",
                "lod": lod,
                "mime_type": "model/gltf-binary",
                "size_bytes": output.stat().st_size,
                "sha256": style.sha256(output),
                "triangle_count": triangles,
                "representation": "sampled-native-mesh",
                "bounds": {
                    "min": [final_lo.x, final_lo.z, -final_hi.y],
                    "max": [final_hi.x, final_hi.z, -final_lo.y],
                },
                "native_height_m": native_height,
                "native_radius_m": native_radius,
                "normalised_height_m": 1.0,
                "normalised_radius_m": 1.0,
            }
        )
        for child in children:
            bpy.data.objects.remove(child, do_unlink=True)
    for obj in originals:
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.collections.remove(collection)


def surface_texture(kind, role, spec, request, assets):
    path = Path(spec["path"])
    if style.sha256(path) != spec["expected_sha256"]:
        raise ValueError("Frozen PBR texture changed")
    image = bpy.data.images.load(str(path), check_existing=False)
    if role != "diffuse":
        image.colorspace_settings.name = "Non-Color"
    maximum = request["settings"]["surface_texture_pixels"]
    width, height = image.size
    if max(width, height) > maximum:
        factor = maximum / max(width, height)
        image.scale(round(width * factor), round(height * factor))
    if role == "diffuse":
        bpy.data.images.remove(image)
        image = bake_surface_color(kind, request, maximum)
    elif kind == "road" and role == "roughness":
        values = np.asarray(image.pixels[:], dtype=np.float32).reshape(-1, 4)
        values[:, :3] = 0.76 + np.clip(values[:, :3], 0, 1) * 0.18
        image.pixels.foreach_set(values.ravel())
    key = kind + ":" + role
    identifier = asset_id(request["cache_key"], key)
    filename = identifier + ".jpg"
    output = Path(request["output_directory"]) / filename
    image.filepath_raw = str(output)
    image.file_format = "JPEG"
    image.save()
    assets.append(
        {
            "id": identifier,
            "key": key,
            "filename": filename,
            "kind": "texture",
            "material_role": kind,
            "map_type": role,
            "mime_type": "image/jpeg",
            "size_bytes": output.stat().st_size,
            "sha256": style.sha256(output),
            "width": image.size[0],
            "height": image.size[1],
        }
    )
    bpy.data.images.remove(image)


def bake_surface_color(kind, request, size):
    """Bake native color nodes to a metric tile instead of exposing raw scans."""
    spec = request["appearance"]["materials"][kind]
    if kind == "road":
        material = style.native_asphalt_material("Browser native asphalt", spec)
    elif kind == "lawn":
        material = style.texture_material(
            "Browser native lawn",
            spec,
            saturation=1.10,
            normal_strength=0.30,
            tile_width_m=2.2,
            neutral_color=(0.045, 0.115, 0.020),
            texture_weight=0.88,
            value=0.74,
            macro_strength=0.08,
            macro_scale=0.10,
        )
    else:
        material = style.texture_material(
            "Browser native sidewalk",
            spec,
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
    nodes, links = material.node_tree.nodes, material.node_tree.links
    shader = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    output = next(n for n in nodes if n.type == "OUTPUT_MATERIAL")
    color = shader.inputs["Base Color"].links[0].from_socket
    emission = nodes.new("ShaderNodeEmission")
    links.new(color, emission.inputs["Color"])
    links.new(emission.outputs["Emission"], output.inputs["Surface"])
    target = bpy.data.images.new(kind + " native color", size, size, alpha=False)
    target.colorspace_settings.name = "sRGB"
    image_node = nodes.new("ShaderNodeTexImage")
    image_node.image = target
    nodes.active = image_node
    tile = request["surfaces"][kind]["tile_width_m"]
    data = bpy.data.meshes.new("Metric material tile")
    data.from_pydata(
        [(0, 0, 0), (tile, 0, 0), (tile, tile, 0), (0, tile, 0)], [], [(0, 1, 2, 3)]
    )
    data.update()
    uv = data.uv_layers.new()
    for loop, point in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = point
    obj = bpy.data.objects.new("Metric material tile", data)
    obj.data.materials.append(material)
    bpy.context.collection.objects.link(obj)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.cycles.device = "CPU"
    bpy.context.scene.cycles.samples = 1
    bpy.ops.object.bake(type="EMIT", margin=2)
    bpy.data.objects.remove(obj, do_unlink=True)
    return target


def main():
    arguments = sys.argv[sys.argv.index("--") + 1 :]
    request = json.loads(Path(arguments[0]).read_bytes())
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    assets = []
    for relative, path in sorted(request["models"].items()):
        export_model(relative, path, request, assets)
    for kind, material in request["appearance"]["materials"].items():
        for role, spec in material["maps"].items():
            surface_texture(kind, role, spec, request, assets)
    manifest = {
        "cache_key": request["cache_key"],
        "identity": request["identity"],
        "assets": assets,
    }
    output = Path(request["output_directory"]) / "manifest.json"
    output.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("GA_BROWSER_ASSETS", len(assets), sum(a["size_bytes"] for a in assets))


if __name__ == "__main__":
    main()
