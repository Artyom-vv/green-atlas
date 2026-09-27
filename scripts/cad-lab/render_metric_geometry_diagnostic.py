"""Render source-backed terrain and curb geometry without decorative assets.

Run with Blender:
  Blender --background --python scripts/cad-lab/render_metric_geometry_diagnostic.py -- \
    --terrain .runtime/annotation-constrained-terrain-20260920/terrain.json \
    --curb .runtime/source-curb-mesh-20260920/curb-mesh.json \
    --output .runtime/metric-geometry-diagnostic-20260920

The scene preserves metre scale. Absolute source Z is shifted only by a
recorded vertical origin. The PNGs are diagnostics, not presentation renders.
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


COLORS = {
    "road": (0.19, 0.23, 0.28, 1.0),
    "sidewalk": (0.72, 0.65, 0.53, 1.0),
    "lawn": (0.20, 0.47, 0.20, 1.0),
    "special_surface": (0.64, 0.31, 0.12, 1.0),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--terrain", type=Path, required=True)
    parser.add_argument("--curb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    values = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    return parser.parse_args(values)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for store in (
        bpy.data.meshes, bpy.data.curves, bpy.data.materials,
        bpy.data.cameras, bpy.data.lights, bpy.data.worlds,
    ):
        for item in list(store):
            store.remove(item)


def make_material(name: str, color, emission=False):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    nodes = result.node_tree.nodes
    links = result.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    if emission:
        shader = nodes.new("ShaderNodeEmission")
        shader.inputs["Color"].default_value = color
        shader.inputs["Strength"].default_value = 1.0
        links.new(shader.outputs["Emission"], output.inputs["Surface"])
    else:
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        shader.inputs["Base Color"].default_value = color
        shader.inputs["Roughness"].default_value = 0.82
        shader.inputs["Metallic"].default_value = 0.0
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    result.diffuse_color = color
    return result


def mesh_object(name: str, vertices, faces, mat, collection):
    mesh = bpy.data.meshes.new(name + ":mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.validate(clean_customdata=True)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.data.materials.append(mat)
    for polygon in mesh.polygons:
        polygon.use_smooth = False
    return obj


def add_polyline(name: str, points, radius: float, mat, collection):
    curve = bpy.data.curves.new(name + ":curve", type="CURVE")
    curve.dimensions = "3D"
    curve.resolution_u = 1
    curve.bevel_depth = radius
    curve.bevel_resolution = 0
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for target, source in zip(spline.points, points):
        target.co = (*source, 1.0)
    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)
    obj.data.materials.append(mat)
    return obj


def aim(camera, target: Vector) -> None:
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def render(scene, path: Path) -> None:
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def main() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    terrain = json.loads(args.terrain.read_text())
    curb = json.loads(args.curb.read_text())
    if terrain.get("schema") != "green-atlas.annotation-constrained-terrain.v1":
        raise ValueError("Unexpected terrain schema")
    if curb.get("schema") != "green-atlas.source-curb-mesh.v1":
        raise ValueError("Unexpected curb schema")

    all_xyz = [row["xyz"] for row in terrain["vertices"]] + [row["xyz"] for row in curb["vertices"]]
    bounds = [[min(p[i] for p in all_xyz), max(p[i] for p in all_xyz)] for i in range(3)]
    origin = [
        (bounds[0][0] + bounds[0][1]) / 2.0,
        (bounds[1][0] + bounds[1][1]) / 2.0,
        math.floor(bounds[2][0]),
    ]

    def local(point):
        return [float(point[i]) - origin[i] for i in range(3)]

    clear_scene()
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 1400
    scene.render.resolution_y = 1000
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.film_transparent = True
    scene.view_settings.look = "AgX - Medium High Contrast"
    world = bpy.data.worlds.new("Diagnostic neutral world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.72, 0.76, 0.80, 1.0)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.65
    scene.world = world
    collection = scene.collection

    pbr_materials = {
        semantic_class: make_material("surface:" + semantic_class, color)
        for semantic_class, color in COLORS.items()
    }
    plan_materials = {
        semantic_class: make_material("plan:" + semantic_class, color, emission=True)
        for semantic_class, color in COLORS.items()
    }
    curb_material = make_material("source curb riser", (0.88, 0.06, 0.14, 1.0))
    plan_curb_material = make_material("plan:curb", (0.95, 0.02, 0.12, 1.0), emission=True)
    overlay_material = make_material(
        "curb upper-edge diagnostic", (1.0, 0.04, 0.35, 1.0), emission=True
    )

    terrain_objects = []
    for semantic_class in COLORS:
        faces = [
            face for face, face_class in zip(terrain["triangles"], terrain["triangle_classes"])
            if face_class == semantic_class
        ]
        used = sorted({index for face in faces for index in face})
        remap = {old: new for new, old in enumerate(used)}
        vertices = [local(terrain["vertices"][index]["xyz"]) for index in used]
        local_faces = [[remap[index] for index in face] for face in faces]
        if local_faces:
            terrain_objects.append(mesh_object(
                "terrain:" + semantic_class, vertices, local_faces,
                pbr_materials[semantic_class], collection,
            ))

    curb_vertices = [local(row["xyz"]) for row in curb["vertices"]]
    curb_object = mesh_object(
        "curb:riser", curb_vertices, curb["faces"], curb_material, collection
    )

    # Display-only exact upper-edge overlay: sub-decimetre vertical faces would
    # disappear in an overview. This does not alter source geometry.
    upper_edges = set()
    for face in curb["faces"]:
        upper = [index for index in face if curb["vertices"][index]["curb_role"] == "upper"]
        if len(upper) != 2:
            raise ValueError(f"Curb face lacks two upper vertices: {face}")
        upper_edges.add(tuple(sorted(upper)))
    for index, (a, b) in enumerate(sorted(upper_edges)):
        add_polyline(
            f"curb:upper-edge:{index:04d}",
            [curb_vertices[a], curb_vertices[b]], 0.025,
            overlay_material, collection,
        )

    # Connected curb-face components let the renderer make a genuinely local
    # contact view instead of another unreadable whole-site overview.
    face_neighbors = {index: set() for index in range(len(curb["faces"]))}
    faces_by_vertex = {}
    for face_index, face in enumerate(curb["faces"]):
        for vertex_index in face:
            faces_by_vertex.setdefault(vertex_index, []).append(face_index)
    for incident in faces_by_vertex.values():
        for face_index in incident:
            face_neighbors[face_index].update(incident)
    components = []
    unseen = set(face_neighbors)
    while unseen:
        seed = min(unseen)
        stack = [seed]
        component = set()
        while stack:
            face_index = stack.pop()
            if face_index in component:
                continue
            component.add(face_index)
            unseen.discard(face_index)
            stack.extend(face_neighbors[face_index] - component)
        components.append(sorted(component))
    focus_component = max(components, key=lambda rows: (len(rows), -rows[0]))
    focus_vertex_indices = sorted({
        vertex_index
        for face_index in focus_component
        for vertex_index in curb["faces"][face_index]
    })

    camera_data = bpy.data.cameras.new("Diagnostic camera")
    camera_data.type = "ORTHO"
    camera_data.dof.use_dof = False
    camera = bpy.data.objects.new("Diagnostic camera", camera_data)
    collection.objects.link(camera)
    scene.camera = camera
    light_data = bpy.data.lights.new("Diagnostic sun", type="SUN")
    light_data.energy = 2.2
    light_data.angle = math.radians(18.0)
    sun = bpy.data.objects.new("Diagnostic sun", light_data)
    collection.objects.link(sun)
    sun.rotation_euler = (math.radians(35), math.radians(-25), math.radians(-32))

    extent_x = bounds[0][1] - bounds[0][0]
    extent_y = bounds[1][1] - bounds[1][0]
    max_extent = max(extent_x, extent_y)
    z_mid = (bounds[2][0] + bounds[2][1]) / 2.0 - origin[2]

    for obj in terrain_objects:
        semantic_class = obj.name.split(":", 1)[1]
        obj.data.materials.clear()
        obj.data.materials.append(plan_materials[semantic_class])
    curb_object.data.materials.clear()
    curb_object.data.materials.append(plan_curb_material)
    camera.location = (0.0, 0.0, max_extent * 1.7)
    camera.data.ortho_scale = max(extent_y * 1.12, extent_x * 1000 / 1400 * 1.12)
    aim(camera, Vector((0.0, 0.0, z_mid)))
    render(scene, args.output / "01-semantic-plan.png")

    for obj in terrain_objects:
        semantic_class = obj.name.split(":", 1)[1]
        obj.data.materials.clear()
        obj.data.materials.append(pbr_materials[semantic_class])
    curb_object.data.materials.clear()
    curb_object.data.materials.append(curb_material)
    camera.location = (max_extent * 0.62, -max_extent * 0.78, max_extent * 0.65)
    camera.data.ortho_scale = max_extent * 1.22
    aim(camera, Vector((0.0, 0.0, z_mid)))
    render(scene, args.output / "02-metric-oblique.png")

    curb_local = [local(row["xyz"]) for row in curb["vertices"]]
    curb_center_x = (min(p[0] for p in curb_local) + max(p[0] for p in curb_local)) / 2
    curb_center_y = (min(p[1] for p in curb_local) + max(p[1] for p in curb_local)) / 2
    camera.location = (
        curb_center_x + max_extent * 0.45,
        curb_center_y - max_extent * 0.52,
        z_mid + max_extent * 0.34,
    )
    camera.data.ortho_scale = max_extent * 0.82
    aim(camera, Vector((curb_center_x, curb_center_y, z_mid)))
    render(scene, args.output / "03-curb-contact-oblique.png")

    focus_points = [curb_vertices[index] for index in focus_vertex_indices]
    focus_bounds = [
        [min(point[axis] for point in focus_points), max(point[axis] for point in focus_points)]
        for axis in range(3)
    ]
    focus_center = Vector(tuple(
        (focus_bounds[axis][0] + focus_bounds[axis][1]) / 2.0 for axis in range(3)
    ))
    focus_extent = max(
        focus_bounds[0][1] - focus_bounds[0][0],
        focus_bounds[1][1] - focus_bounds[1][0],
        6.0,
    )
    camera.location = (
        focus_center.x + focus_extent * 0.62,
        focus_center.y - focus_extent * 0.72,
        focus_center.z + focus_extent * 0.50,
    )
    camera.data.ortho_scale = focus_extent * 1.35
    aim(camera, focus_center)
    render(scene, args.output / "04-curb-local-contact.png")

    blend_path = args.output / "metric-geometry-diagnostic.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend_path), check_existing=False)
    receipt = {
        "schema": "green-atlas.metric-geometry-diagnostic-receipt.v1",
        "status": "diagnostic_not_beauty_render",
        "inputs": {
            "terrain": {"path": str(args.terrain.resolve()), "sha256": sha256(args.terrain)},
            "curb": {"path": str(args.curb.resolve()), "sha256": sha256(args.curb)},
        },
        "coordinate_frame": {
            "source_unit": "metre",
            "local_origin_source_xyz": origin,
            "vertical_exaggeration": 1.0,
            "source_bounds_xyz": bounds,
        },
        "geometry": {
            "terrain_vertices": len(terrain["vertices"]),
            "terrain_triangles": len(terrain["triangles"]),
            "curb_vertices": len(curb["vertices"]),
            "curb_faces": len(curb["faces"]),
            "curb_upper_edge_overlays": len(upper_edges),
            "curb_face_components": len(components),
            "local_focus_face_indices": focus_component,
            "curb_covered_length_m": curb["quality"]["covered_curb_length_m"],
        },
        "render_contract": {
            "no_generated_geometry": True,
            "no_gap_fill": True,
            "no_textures": True,
            "no_neural_finish": True,
            "vertical_exaggeration": 1.0,
            "upper_edge_overlay_is_display_only": True,
        },
        "outputs": {},
    }
    for path in sorted(args.output.iterdir()):
        if path.name == "receipt.json" or not path.is_file():
            continue
        receipt["outputs"][path.name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
