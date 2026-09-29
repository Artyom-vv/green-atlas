"""Render two viewport-style stages from the preserved Blender scene.

Run with Blender --background <scene.blend> --python this_file -- <output-dir>.
No geometry, camera, or source .blend data are saved or altered.
"""
import sys
import re
from pathlib import Path

import bpy


def category(name):
    if name.startswith("Sky_"):
        return "sky"
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", name):
        return "building"
    if name.startswith(("Botaniq_lawn", "project_lawn")):
        return "lawn"
    if name.startswith("project_road-marking"):
        return "marking"
    if name.startswith("project_road"):
        return "road"
    if name.startswith("project_sidewalk"):
        return "sidewalk"
    if name.startswith(("inventory:", "Botaniq_")):
        return "vegetation"
    if name.startswith(("Context_window", "Retail_")):
        return "building"
    if name.startswith("Context_"):
        if any(part in name.lower() for part in ("curb", "kerb", "border")):
            return "curb"
        if any(part in name.lower() for part in ("building", "roof", "facade")):
            return "building"
        return "terrain"
    if name.startswith("project-maf"):
        return "furniture"
    return "unknown"


COLORS = {
    "road": (0.56, 0.57, 0.58, 1),
    "sidewalk": (0.72, 0.72, 0.72, 1),
    "lawn": (0.11, 0.79, 0.29, 1),
    "curb": (0.72, 0.72, 0.72, 1),
    "building": (0.80, 0.80, 0.80, 1),
    "vegetation": (0.06, 0.48, 0.18, 1),
    "marking": (0.86, 0.86, 0.86, 1),
    "furniture": (0.65, 0.65, 0.65, 1),
    "sky": (0.70, 0.72, 0.74, 1),
    "terrain": (0.62, 0.62, 0.62, 1),
    "unknown": (0.61, 0.61, 0.61, 1),
}


def render(path, semantic):
    scene = bpy.context.scene
    shading = scene.display.shading
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 720
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    shading.light = "STUDIO"
    shading.color_type = "OBJECT" if semantic else "SINGLE"
    shading.single_color = (0.88, 0.89, 0.90)
    shading.background_type = "WORLD"
    shading.background_color = (0.74, 0.82, 0.88)
    shading.show_cavity = True
    shading.show_shadows = False
    if semantic:
        for obj in scene.objects:
            obj.color = COLORS[category(obj.name)]
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


out = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
out.mkdir(parents=True, exist_ok=True)
render(out / "01-clay.png", False)
render(out / "02-green-mask-highlight.png", True)
print(f"DEMO_STAGES_WRITTEN {out}")
