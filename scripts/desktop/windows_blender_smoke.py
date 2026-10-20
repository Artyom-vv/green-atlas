"""Actual Blender PNG/GLB pipeline with generated assets, never Botaniq acceptance."""

from __future__ import annotations

import argparse
import json
import os
import struct
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "apps/api"))

from app.desktop.dependencies import save as save_dependencies
from app.geometry.contracts import GeometrySnapshot
from app.planning.contracts import Plan, PlanObject
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.rendering.application import SceneRendering
from app.rendering.assets import file_sha
from app.rendering.blender_runtime import (
    BlenderInstallation,
    blender_job_environment,
    discover_blender,
    launch_blender,
    probe_blender,
)
from app.rendering.contracts import RenderCamera, SceneRenderRequest
from app.rendering.preview_assets import prepare_preview_assets
from app.shared import tool_settings

SEED = r"""
import json, math, sys
from pathlib import Path
import bpy
from mathutils import Quaternion, Vector
root = Path(sys.argv[sys.argv.index('--') + 1])
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=2, location=(0,0,1.5))
obj = bpy.context.object
obj.name = 'Owned synthetic test plant'
obj.scale = (0.7,0.7,1.5)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
material = bpy.data.materials.new('Owned test green')
material.use_nodes = True
material.diffuse_color = (0.1,0.4,0.05,1)
material.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = material.diffuse_color
obj.data.materials.append(material)
bpy.ops.wm.save_as_mainfile(filepath=str(root/'assets'/'synthetic-tree.blend'), check_existing=False)
for kind, color in [('road',(0.2,0.2,0.2,1)),('sidewalk',(0.5,0.5,0.5,1)),('lawn',(0.1,0.3,0.05,1))]:
    for role in ('diffuse','roughness','normal_gl'):
        image = bpy.data.images.new(kind+'-'+role, width=16, height=16)
        pixel = color if role=='diffuse' else ((0.5,0.5,1,1) if role=='normal_gl' else (0.7,0.7,0.7,1))
        image.pixels = list(pixel)*256
        image.file_format = 'PNG'
        image.filepath_raw = str(root/'assets'/(kind+'-'+role+'.png'))
        image.save()
        bpy.data.images.remove(image)
position = Vector((15,-25,20))
rotation = (Vector((0,0,1))-position).to_track_quat('-Z','Y')
three = Quaternion((1,0,0),math.pi/2).inverted() @ rotation
(root/'camera.json').write_text(json.dumps({'position':[15,20,25], 'quaternion':[three.x,three.y,three.z,three.w], 'fov_y_deg':42,'aspect':1.5,'near':0.08,'far':200}), encoding='utf-8')
"""


def require(value, message):
    if not value:
        raise RuntimeError(message)


def appearance(root, snapshot):
    directory = root / "assets"
    files = [
        {
            "path": str(p),
            "relative_path": p.name,
            "sha256": file_sha(p),
            "size_bytes": p.stat().st_size,
        }
        for p in sorted(directory.iterdir())
    ]
    material = {
        kind: {
            "maps": {
                role: {
                    "path": str(directory / f"{kind}-{role}.png"),
                    "expected_sha256": file_sha(directory / f"{kind}-{role}.png"),
                }
                for role in ("diffuse", "roughness", "normal_gl")
            }
        }
        for kind in ("road", "sidewalk", "lawn")
    }
    return {
        "profile": "ci-synthetic",
        "lighting": "ci-synthetic-daylight",
        "bundle_sha256": sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "files": files,
        "materials": material,
        "grass_paths": [],
        "context_plants": [],
        "texture_root": str(directory),
        "plants": [
            {
                "object_id": row.object_id,
                "path": str(directory / "synthetic-tree.blend"),
                "relative_path": "synthetic-tree.blend",
                "asset_key": "OwnedTestMesh",
                "match_status": "synthetic_fixture",
            }
            for row in snapshot.objects
        ],
    }


def run(binary, output):
    version = probe_blender(BlenderInstallation(binary)).version
    require(version == (4, 5, 11), "Expected pinned Blender4.5.11")
    (output / "assets").mkdir()
    seed = output / "generate-owned-assets.py"
    seed.write_text(SEED, encoding="utf-8")
    with (output / "seed.log").open("wb") as log:
        child = launch_blender(
            [
                str(binary),
                "--background",
                "--factory-startup",
                "-t",
                "2",
                "--python-exit-code",
                "1",
                "--python",
                str(seed),
                "--",
                str(output),
            ],
            environment=blender_job_environment(output),
            stdout=log,
        )
        try:
            require(
                child.wait(timeout=120) == 0, "Blender did not generate owned fixtures"
            )
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    repository = SqliteProjectRepository(output / "projects.sqlite3")
    service = SceneRendering(repository, output / "renders")
    settings_context = patch(
        "app.shared.tool_settings.settings_path",
        return_value=output / "settings/external-tools.json",
    )
    settings_context.start()
    previous_blender = os.environ.pop("GREEN_ATLAS_BLENDER", None)
    try:
        settings = save_dependencies({**tool_settings.load(), "blender": str(binary)})
        require(
            settings["valid"], "Actual Blender was not accepted by component settings"
        )
        project = repository.create(
            Project(
                id="owned-blender-control",
                name="Own generated Blender fixture",
                geometry=GeometrySnapshot(
                    feature_collection={"type": "FeatureCollection", "features": []}
                ),
                plan=Plan(
                    objects=[
                        PlanObject(id="test-tree", kind="tree", x=0, y=0, radius=1)
                    ]
                ),
            )
        )
        original = repository.get(project.id).model_dump_json()
        snapshot = service.scene.from_project(project, 20)
        resources = appearance(output, snapshot)
        request = SceneRenderRequest(
            request_id=uuid4(),
            scene_sha256=snapshot.content_sha256,
            horizon_year=20,
            camera=RenderCamera(**json.loads((output / "camera.json").read_bytes())),
            resolution=512,
        )
        # Only the resource selector is a fixture. Renderer, process, receipt
        # validation, project store and preview converter execute production code.
        with patch(
            "app.rendering.application.prepare_appearance", return_value=resources
        ):
            job = service.create(project.id, request)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            job = service.get(project.id, job.id)
            if job.status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.2)
        require(
            job.status == "completed", "Actual render failed: " + job.model_dump_json()
        )
        png = service.file(project.id, job.id, "image.png")
        header = png.read_bytes()[:24]
        require(header[:8] == b"\x89PNG\r\n\x1a\n", "No actual PNG")
        dimensions = struct.unpack(">II", header[16:24])
        require(dimensions == (job.width, job.height), "PNG dimensions mismatch")
        render_receipt = json.loads(
            service.file(project.id, job.id, "receipt.json").read_bytes()
        )
        raw = json.loads(
            (service.directory / str(job.id) / "renderer-receipt.json").read_bytes()
        )
        require(
            raw["plant_ids"] == ["test-tree"]
            and raw["blender_version"].startswith("4.5.11"),
            "Wrong actual renderer receipt",
        )
        packet = json.loads(
            (service.directory / str(job.id) / "scene.json").read_bytes()
        )
        preview = prepare_preview_assets(
            packet,
            output / "preview-first",
            cache_directory=output / "preview-cache",
            blender=str(discover_blender().executable),
        )
        reused = prepare_preview_assets(
            packet,
            output / "preview-second",
            cache_directory=output / "preview-cache",
            blender=None,
        )
        require(preview == reused, "Warm preview cache differs")
        require(
            len([r for r in preview["assets"] if r["kind"] == "model"]) == 2,
            "Missing actual near/far GLB",
        )
        require(len(preview["assets"]) == 11, "Missing model or PBR derivative")
        require(
            repository.get(project.id).model_dump_json() == original,
            "Render changed project data",
        )
        require(
            all(
                file_sha(Path(row["path"])) == row["sha256"]
                for row in resources["files"]
            ),
            "Worker changed original fixture assets",
        )
        return {
            "passed": True,
            "platform": sys.platform,
            "blender_version": list(version),
            "production_renderer_executed": True,
            "production_preview_converter_executed": True,
            "png_dimensions": dimensions,
            "png_sha256": file_sha(png),
            "preview_assets": len(preview["assets"]),
            "preview_cache_reused": True,
            "input_assets_unchanged": True,
            "project_unchanged": True,
            "renderer_receipt_sha256": sha256(
                json.dumps(render_receipt, sort_keys=True).encode()
            ).hexdigest(),
            "synthetic_assets_only": True,
            "botaniq_used": False,
            "autocad_executed": False,
            "frozen_api_executed": False,
            "installed_product_executed": False,
            "selected_blender_from_saved_settings": True,
            "blender_env_override_used": False,
            "saved_settings_sha256": file_sha(tool_settings.settings_path()),
        }
    finally:
        service.close()
        repository.close()
        settings_context.stop()
        if previous_blender is not None:
            os.environ["GREEN_ATLAS_BLENDER"] = previous_blender


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    require(
        output.is_relative_to(REPOSITORY) and not output.exists(),
        "Use a fresh owned checkout output",
    )
    output.mkdir(parents=True)
    receipt = {
        "schema": "green-atlas.blender-native-control/1",
        "passed": False,
        "product_qualified": False,
        "synthetic_assets_only": True,
        "botaniq_used": False,
    }
    try:
        receipt.update(run(args.blender.resolve(), output))
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        TimeoutError,
        subprocess.TimeoutExpired,
    ) as error:
        receipt["error"] = type(error).__name__ + ": " + str(error)
    (output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # A redirected Windows console can still use cp1252. Keep diagnostics
    # readable without changing the UTF-8 receipt stored on disk.
    print(json.dumps(receipt, ensure_ascii=True))
    if not receipt["passed"]:
        logs = [output / "seed.log", *sorted(output.rglob("blender.log"))]
        for log in logs[:5]:
            if log.is_file():
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 16384))
                    tail = stream.read().decode("utf-8", errors="replace")
                print(
                    json.dumps(
                        {
                            "owned_worker_log": log.relative_to(output).as_posix(),
                            "tail": tail,
                        },
                        ensure_ascii=True,
                    )
                )
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
