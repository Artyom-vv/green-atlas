"""Один локальный Blender worker, неизменяемые входы и восстанавливаемые результаты."""

import json
import math
import re
import shutil
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock
from time import monotonic
from typing import Any, cast
from urllib.parse import quote
from uuid import UUID

from app.projects.contracts import Project
from app.projects.ports import ProjectReader
from app.projects.resource_scope import (
    project_resource_scope,
    register_project_resource,
)
from app.rendering.assets import prepare_appearance
from app.rendering.blender_runtime import (
    blender_job_environment,
    discover_blender,
    launch_blender,
)
from app.rendering.building_heights import building_inputs, prepare_buildings
from app.rendering.contracts import (
    RenderCamera,
    RenderPreparationPreview,
    RenderPreparationSummary,
    SceneRenderJob,
    SceneRenderPreparation,
    SceneRenderPreparationRequest,
    SceneRenderRequest,
)
from app.rendering.environment import prepare_environment
from app.rendering.environment_inputs import environment_inputs
from app.rendering.packet import display_meshes, flat_building_surfaces
from app.rendering.preview_assets import prepare_preview_assets
from app.rendering.scene_view import SceneViewReader
from app.scene.application import SceneApplication
from app.scene.contracts import SceneSnapshot
from app.species.catalog import get_species

MAX_PACKET_BYTES = 256 * 1024 * 1024
LEGACY_RENDERER_SCRIPTS = ("blender_worker.py", "render_style.py")
RENDERER_SCRIPTS = (*LEGACY_RENDERER_SCRIPTS, "lawn_instances.py", "lawn_scatter.py")


def _renderer_digests(value: Any) -> dict[str, str]:
    """Only complete versioned script sets may be used as local file names."""
    if (
        not isinstance(value, dict)
        or frozenset(value)
        not in {frozenset(LEGACY_RENDERER_SCRIPTS), frozenset(RENDERER_SCRIPTS)}
        or any(
            not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in value.values()
        )
    ):
        raise ValueError("Код подготовленного рендера повреждён")
    return cast(dict[str, str], value)


def _verify_renderer(root: Path, expected: dict[str, str]) -> None:
    for name, digest in expected.items():
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > MAX_PACKET_BYTES
            or sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Код рендера изменился после принятия")


def packet_bytes(packet: dict[str, Any]) -> bytes:
    data = json.dumps(
        packet, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()
    if len(data) > MAX_PACKET_BYTES:
        raise ValueError("Сцена превышает допустимый размер рендера (256 МБ)")
    return data


def _dimensions(camera: RenderCamera, resolution: int) -> tuple[int, int]:
    return (
        (resolution, round(resolution / camera.aspect))
        if camera.aspect >= 1
        else (round(resolution * camera.aspect), resolution)
    )


def _preview(packet: dict[str, Any]) -> dict[str, Any]:
    """Expose composition geometry, never paths to private appearance files."""
    snapshot = packet["scene"]
    meshes = []
    for source, rows in (
        ("cad_building", packet.get("building_display_meshes", [])),
        ("cad_surface", packet.get("environment_surfaces", [])),
        ("map_building", packet.get("environment_buildings", [])),
        ("cad_surface", packet.get("environment_curb_meshes", [])),
        ("cad_building", packet.get("building_facade_meshes", [])),
    ):
        for index, row in enumerate(rows):
            meshes.append(
                {
                    "id": row.get("id", f"environment:surface:{index}"),
                    "kind": row["kind"],
                    "source": row.get("source", source),
                    "vertices": row["vertices"],
                    "faces": row["faces"],
                    "height_m": row.get("height_m"),
                    "height_status": row.get("height_status"),
                    "material_key": row.get("material_key"),
                }
            )
    terrain = []
    for primitive in snapshot.get("vertical_primitives", []):
        if (
            primitive["primitive_type"] == "surface_mesh"
            and primitive["terrain_mapping_status"] == "confirmed"
        ):
            for face in primitive["faces"]:
                for i in range(1, len(face) - 1):
                    terrain.append(
                        [
                            primitive["vertices"][index]
                            for index in (face[0], face[i], face[i + 1])
                        ]
                    )

    def ground(x: float, y: float) -> float:
        for a, b, c in terrain:
            denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(denominator) < 1e-12:
                continue
            u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
            v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
            if u >= -1e-8 and v >= -1e-8 and u + v <= 1 + 1e-8:
                return u * a[2] + v * b[2] + (1 - u - v) * c[2]
        return 0

    for mesh in meshes:
        mesh.setdefault("surface_geometry", None)
        mesh.setdefault("surface_elevation_m", 0.0)

    assignments = {
        row["object_id"]: row for row in packet["appearance"].get("context_plants", [])
    }
    preview_appearance = packet.get("preview_appearance", {})
    context_models = {
        row["object_id"]: row for row in preview_appearance.get("context_plants", [])
    }
    planned_models = {
        row["object_id"]: row for row in preview_appearance.get("plants", [])
    }

    def model_fields(identifier: str, models: dict[str, Any]) -> dict[str, Any]:
        row = models.get(identifier, {})
        return {
            "model_asset_id": row.get("model_asset_id"),
            "far_model_asset_id": row.get("far_model_asset_id"),
        }

    trees = []
    for row in packet.get("environment_trees", []):
        base = row.get("base_elevation_m")
        trees.append(
            {
                "object_id": row["object_id"],
                "local_x": row["local_x"],
                "local_y": row["local_y"],
                "base_elevation_m": ground(row["local_x"], row["local_y"])
                if base is None
                else base,
                "height_m": row["height_m"],
                "radius_m": row["radius_m"],
                "rotation_z": int(sha256(row["object_id"].encode()).hexdigest()[:8], 16)
                / 0xFFFFFFFF
                * math.tau,
                "asset_key": assignments.get(row["object_id"], {}).get("asset_key"),
                **model_fields(row["object_id"], context_models),
                "evidence": row.get("evidence", {}),
            }
        )
    suppressed = set(packet.get("environment_replaced_ids", []))
    if "building_display_meshes" in packet:
        suppressed.update(
            row["id"] for row in packet["meshes"] if row["kind"] == "building"
        )
    suppressed.update(
        row["id"]
        for row in packet["meshes"]
        if not row["faces"]
        and row["kind"] in {"utility", "restricted", "site_border", "existing_green"}
    )
    suppressed.update(
        identifier
        for row in packet.get("environment_trees", [])
        for identifier in row.get("source_feature_ids", [])
    )
    planned = []
    plant_assignments = {
        row["object_id"]: row for row in packet["appearance"].get("plants", [])
    }
    for row in snapshot["objects"] if "preview_appearance" in packet else []:
        radius = max(0.18, row["canopy_radius_max_m"])
        minimum, ratio = (1.8, 2.55) if row["kind"] == "tree" else (0.35, 1.25)
        planned.append(
            {
                "object_id": row["object_id"],
                "local_x": row["local_x"],
                "local_y": row["local_y"],
                "base_elevation_m": ground(row["local_x"], row["local_y"]),
                "height_m": max(
                    minimum,
                    row["height_max_m"]
                    if row["height_max_m"] is not None
                    else radius * ratio,
                ),
                "radius_m": radius,
                "rotation_z": int(sha256(row["object_id"].encode()).hexdigest()[:8], 16)
                / 0xFFFFFFFF
                * math.tau,
                "asset_key": plant_assignments.get(row["object_id"], {}).get(
                    "asset_key"
                ),
                **model_fields(row["object_id"], planned_models),
                "evidence": {},
            }
        )
    public_assets = []
    for row in preview_appearance.get("assets", []):
        public_assets.append(
            {
                **{
                    key: row.get(key)
                    for key in (
                        "id",
                        "sha256",
                        "size_bytes",
                        "mime_type",
                        "kind",
                        "lod",
                        "bounds",
                    )
                },
                "url": "/api/projects/"
                + quote(snapshot["project_id"], safe="")
                + "/scene-render-preparations/"
                + packet["preparation_id"]
                + "/assets/"
                + row["id"],
            }
        )
    return {
        "meshes": meshes,
        "curbs": [
            {
                "id": f"environment:curb:{index}",
                **{
                    key: row[key]
                    for key in (
                        "start_scene_xyz_m",
                        "end_scene_xyz_m",
                        "width_m",
                        "height_m",
                        "source_handle",
                    )
                },
            }
            for index, row in enumerate(
                []
                if "environment_curb_meshes" in packet
                else packet.get("environment_curbs", [])
            )
        ],
        "context_trees": trees,
        "suppressed_source_ids": sorted(suppressed),
        "planned_plants": planned,
        "assets": public_assets,
        "materials": preview_appearance.get("materials", []),
        "asset_version": 1 if "preview_appearance" in packet else 0,
    }


def _summary(packet: dict[str, Any]) -> dict[str, Any]:
    counts = packet["building_heights"]["counts"]
    osm_count = counts.get("osm_height", 0) + counts.get("osm_levels", 0)
    conditional_count = counts.get("render_class_proxy", 0)
    tree_count, neighbour_count = (
        len(packet.get("environment_trees", [])),
        len(packet.get("environment_buildings", [])),
    )
    message = f"Окружение подготовлено: деревья {tree_count}, соседние здания {neighbour_count}. Высоты из OSM {osm_count}, условные объёмы {conditional_count}."
    return {
        "message": message,
        "context_tree_count": tree_count,
        "map_building_count": neighbour_count,
        "map_status": packet["environment"]["map_buildings"]["status"],
        "conditional_building_count": conditional_count,
        "osm_building_count": osm_count,
        "terrain_status": packet["scene"]["terrain_status"],
    }


def _provenance(packet: dict[str, Any]) -> dict[str, Any]:
    environment = packet["environment"]
    heights = packet["building_heights"]
    map_evidence = environment["map_buildings"]
    # Explicit allowlists keep local sidecar evidence_refs, assets and exception
    # file names out of the browser DTO. Full evidence stays in the packet.
    return {
        "source_packet_sha256": packet["source_packet_sha256"],
        "source_sha256": packet["scene"].get("source_sha256"),
        "appearance_bundle_sha256": packet["appearance"].get("bundle_sha256"),
        "building_height_counts": heights["counts"],
        "building_osm_status": heights["osm"]["status"],
        "building_omitted_detail_ids": heights.get("ignored_detail_ids", []),
        "environment_omissions": environment.get("omissions", {}),
        "view_culling": environment.get("view_culling", {}),
        "map_provenance": {
            key: value
            for key, value in map_evidence.get("provenance", {}).items()
            if key
            in {
                "source_sha256",
                "query_sha256",
                "endpoint",
                "fetched_at",
                "cache_status",
                "attribution",
            }
        },
        "georeference": {
            key: value
            for key, value in map_evidence.get("georeference", {}).items()
            if key
            in {
                "status",
                "config_sha256",
                "basis",
                "rmse_m",
                "max_residual_m",
                "valid_extent_cad_xy",
            }
        },
        "map_excluded": [
            {key: row[key] for key in ("id", "reason", "duplicate_of") if key in row}
            for row in map_evidence.get("excluded", [])
        ],
        "data_gaps": packet["scene"]["data_gaps"],
        "scope": "visual composition; original source geometry and calculation evidence unchanged",
    }


def blender_binary() -> str:
    return str(discover_blender().executable)


class SceneRendering:
    def __init__(
        self,
        repository: ProjectReader,
        directory: Path,
        *,
        display_features: Callable[[Project], list[dict[str, Any]] | None]
        | None = None,
    ) -> None:
        self.scene = SceneApplication(repository, get_species)
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.preparation_lock = RLock()
        self.view = SceneViewReader(
            repository, self.directory, display_features=display_features
        )
        self.executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="ga-render"
        )
        self.cancellations: dict[str, Event] = {}
        self._job_projects: dict[str, str] = {}
        self.closed = False
        for path in self.directory.glob("*/job.json"):
            job = SceneRenderJob.model_validate_json(path.read_bytes())
            if job.status in {"queued", "rendering"}:
                self._save(
                    job.model_copy(
                        update={
                            "status": "failed",
                            "stage": "Остановлено",
                            "message": "Рендер прерван при закрытии приложения. Запустите его снова",
                        }
                    )
                )

    def _root(self, job_id: UUID) -> Path:
        return self.directory / str(job_id)

    def _save(self, job: SceneRenderJob) -> None:
        root = self._root(job.id)
        root.mkdir(exist_ok=True)
        temporary = root / "job.pending"
        temporary.write_text(job.model_dump_json(), encoding="utf-8")
        temporary.replace(root / "job.json")

    def get(self, project_id: str, job_id: UUID) -> SceneRenderJob:
        path = self._root(job_id) / "job.json"
        if not path.is_file():
            raise KeyError("Рендер не найден")
        job = SceneRenderJob.model_validate_json(path.read_bytes())
        if job.project_id != project_id:
            raise KeyError("Рендер не найден")
        return job

    def _packet(
        self,
        project: Project,
        snapshot: SceneSnapshot,
        camera: RenderCamera,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        meshes = display_meshes(snapshot)
        surroundings = environment_inputs(project, snapshot)
        return {
            "schema": "green-atlas.project-render/1",
            "scene": snapshot.model_dump(mode="json", exclude={"context_features"}),
            "camera": camera.model_dump(mode="json"),
            "width": width,
            "height": height,
            "meshes": meshes,
            "building_inputs": building_inputs(snapshot),
            "flat_building_surfaces": flat_building_surfaces(meshes),
            "environment_inputs": surroundings,
            "appearance": prepare_appearance(
                snapshot,
                self.directory / "assets",
                surroundings["existing_tree_markers"],
            ),
        }

    def _preparation_root(self, preparation_id: UUID) -> Path:
        return self.directory / "preparations" / str(preparation_id)

    def _georeference_sha256(self, project_id: str) -> str | None:
        """Observe only the optional sidecar, without changing accepted scenes."""
        if any(character in project_id for character in ("/", "\\")):
            raise ValueError("Недопустимый идентификатор проекта для привязки")
        path = self.directory / "georeferences" / f"{project_id}.json"
        try:
            with path.open("rb") as stream:
                digest = sha256()
                for block in iter(lambda: stream.read(64 * 1024), b""):
                    digest.update(block)
                return digest.hexdigest()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ValueError(
                "Не удалось прочитать привязку к карте. Повторите подготовку окружения"
            ) from error

    def _georeference_unchanged(self, project_id: str, expected: str | None) -> None:
        if self._georeference_sha256(project_id) != expected:
            raise ValueError(
                "Привязка к карте изменилась во время подготовки. Подготовьте окружение снова"
            )

    @staticmethod
    def _current_matches(project: Project, manifest: dict[str, Any]) -> None:
        source_sha = project.source_file.content_sha256 if project.source_file else None
        if (
            project.state_version != manifest["state_version"]
            or project.geometry_version != manifest["geometry_version"]
            or (project.plan.version if project.plan else 1) != manifest["plan_version"]
            or source_sha != manifest["source_sha256"]
        ):
            raise ValueError("Проект изменился. Подготовьте окружение снова")

    def _load_preparation(
        self, project_id: str, preparation_id: UUID, *, expected_sha: str | None = None
    ) -> tuple[SceneRenderPreparation, dict[str, Any], dict[str, Any]]:
        root = self._preparation_root(preparation_id)
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise KeyError("Подготовленная сцена не найдена")
        if manifest_path.stat().st_size > 1024 * 1024:
            raise ValueError("Подготовленная сцена повреждена")
        manifest = json.loads(manifest_path.read_bytes())
        if not isinstance(manifest, dict):
            raise ValueError("Подготовленная сцена повреждена")
        if manifest.get("project_id") != project_id:
            raise KeyError("Подготовленная сцена не найдена")
        if manifest.get("schema") != "green-atlas.render-preparation/1":
            raise ValueError("Неподдерживаемая подготовленная сцена")
        required = {
            "source_packet_sha256",
            "packet_sha256",
            "preview_sha256",
            "request_sha256",
            "renderer_sha256",
            "state_version",
            "geometry_version",
            "plan_version",
            "source_sha256",
        }
        if not required.issubset(manifest):
            raise ValueError("Подготовленная сцена повреждена")
        renderer_digests = _renderer_digests(manifest["renderer_sha256"])
        if expected_sha is not None and manifest.get("packet_sha256") != expected_sha:
            raise ValueError("SHA подготовленной сцены не совпадает")
        frozen = {}
        for name, digest in {
            "scene.json": manifest["source_packet_sha256"],
            "render-scene.json": manifest["packet_sha256"],
            "preview.json": manifest["preview_sha256"],
            "request.json": manifest["request_sha256"],
            **{f"renderer/{name}": digest for name, digest in renderer_digests.items()},
        }.items():
            path = root / name
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > MAX_PACKET_BYTES
            ):
                raise ValueError("Подготовленная сцена повреждена")
            data = path.read_bytes()
            if sha256(data).hexdigest() != digest:
                raise ValueError("Подготовленная сцена изменилась после принятия")
            if name in {"render-scene.json", "preview.json"}:
                frozen[name] = data
        packet = json.loads(frozen["render-scene.json"])
        if not isinstance(packet, dict) or not isinstance(packet.get("scene"), dict):
            raise ValueError("Подготовленная сцена повреждена")
        packet_renderers = packet.get("renderer_script_sha256")
        if packet_renderers is None:
            if set(renderer_digests) != set(LEGACY_RENDERER_SCRIPTS):
                raise ValueError("Код рендера не связан с подготовленной сценой")
        elif _renderer_digests(packet_renderers) != renderer_digests:
            raise ValueError("Код рендера не связан с подготовленной сценой")
        preview = SceneRenderPreparation.model_validate_json(frozen["preview.json"])
        if (
            str(preview.id) != str(preparation_id)
            or preview.project_id != project_id
            or preview.sha256 != manifest["packet_sha256"]
            or preview.scene_sha256 != packet["scene"]["content_sha256"]
            or preview.horizon_year != packet["scene"]["horizon_year"]
            or preview.preview.model_dump(mode="json") != _preview(packet)
            or preview.summary.model_dump(mode="json") != _summary(packet)
        ):
            raise ValueError("Предпросмотр не соответствует подготовленной сцене")
        return preview, packet, manifest

    def get_preparation(
        self, project_id: str, preparation_id: UUID
    ) -> SceneRenderPreparation:
        preview, _, manifest = self._load_preparation(project_id, preparation_id)
        self._current_matches(
            self.scene.repository.get(project_id, lightweight=True), manifest
        )
        return preview

    def preview_asset(
        self, project_id: str, preparation_id: UUID, asset_id: str
    ) -> tuple[Path, str, str]:
        """Read one immutable, project-scoped derivative, never an arbitrary path."""
        if re.fullmatch(r"[a-f0-9]{64}", asset_id) is None:
            raise KeyError("Модель предпросмотра не найдена")
        root = self._preparation_root(preparation_id)
        try:
            manifest = json.loads((root / "manifest.json").read_bytes())
            if manifest.get("project_id") != project_id:
                raise KeyError("Подготовленная сцена не найдена")
            preview_bytes = (root / "preview.json").read_bytes()
            if sha256(preview_bytes).hexdigest() != manifest.get("preview_sha256"):
                raise ValueError("Подготовленный предпросмотр изменился")
            public = json.loads(preview_bytes)["preview"].get("assets", [])
            descriptor = next((row for row in public if row["id"] == asset_id), None)
            record = next(
                (
                    row
                    for row in manifest.get("preview_assets", [])
                    if row["id"] == asset_id
                ),
                None,
            )
            if record is None or descriptor is None:
                raise KeyError("Модель предпросмотра не найдена")
            extension = {
                "model/gltf-binary": ".glb",
                "image/jpeg": ".jpg",
                "image/png": ".png",
            }.get(record.get("mime_type"))
            if (
                extension is None
                or record.get("relative_path")
                != f"preview-assets/{asset_id}{extension}"
            ):
                raise ValueError("Неверный ресурс предпросмотра")
            if any(
                record.get(key) != descriptor.get(key)
                for key in ("sha256", "size_bytes", "mime_type", "kind")
            ):
                raise ValueError(
                    "Ресурс не соответствует подготовленному предпросмотру"
                )
            path = root / record["relative_path"]
            if (
                not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())
                or not 0 < path.stat().st_size <= 64 * 1024 * 1024
                or path.stat().st_size != descriptor["size_bytes"]
                or sha256(path.read_bytes()).hexdigest() != descriptor["sha256"]
            ):
                raise ValueError("Ресурс предпросмотра изменился после подготовки")
            return path, descriptor["mime_type"], descriptor["sha256"]
        except FileNotFoundError as error:
            raise KeyError("Ресурс предпросмотра не найден") from error
        except (TypeError, json.JSONDecodeError, StopIteration) as error:
            raise ValueError("Подготовленный предпросмотр повреждён") from error

    def prepare(
        self, project_id: str, request: SceneRenderPreparationRequest
    ) -> SceneRenderPreparation:
        with project_resource_scope(self.scene.repository, project_id):
            register_project_resource("render-preparation", str(request.request_id))
            return self._prepare(project_id, request)

    def _prepare(
        self, project_id: str, request: SceneRenderPreparationRequest
    ) -> SceneRenderPreparation:
        # Long network work must not hold the cancellation/status lock.
        with self.preparation_lock:
            with self.lock:
                if self.closed:
                    raise ValueError("Сервис рендера закрывается")
            root = self._preparation_root(request.request_id)
            request_data = request.model_dump_json().encode()
            if (root / "manifest.json").is_file():
                preview = self.get_preparation(project_id, request.request_id)
                if (
                    SceneRenderPreparationRequest.model_validate_json(
                        (root / "request.json").read_bytes()
                    )
                    .model_dump_json()
                    .encode()
                    != request_data
                ):
                    raise ValueError(
                        "Этот идентификатор использован для другого окружения"
                    )
                return preview
            project = self.scene.repository.get(project_id)
            if project.id != project_id:
                raise KeyError("Проект не найден")
            snapshot = self.scene.from_project(project, request.horizon_year)
            scene_sha256 = snapshot.content_sha256
            if scene_sha256 is None:
                raise ValueError(
                    "Сцена изменилась. Обновите 3D перед подготовкой окружения"
                )
            view = (
                self.view.read(project_id, request.horizon_year)
                if request.preview_scene
                else None
            )
            expected_scene = view.scene["content_sha256"] if view else scene_sha256
            camera = request.camera
            if view:
                vx, vy = view.scene["coordinate_origin"]
                rx, ry = snapshot.coordinate_origin
                x, y, z = camera.position
                camera = camera.model_copy(
                    update={"position": (x + vx - rx, y, z + ry - vy)}
                )
            if expected_scene != request.scene_sha256:
                raise ValueError(
                    "Сцена изменилась. Обновите 3D перед подготовкой окружения"
                )
            if not snapshot.context_features and not snapshot.objects:
                raise ValueError("В сцене нет геометрии или посадок для рендера")
            georeference_sha256 = self._georeference_sha256(project_id)
            if (
                view
                and view.environment.provenance.get("georeference_sha256")
                != georeference_sha256
            ):
                raise ValueError("Привязка изменилась. Обновите 3D перед рендером")
            width, height = _dimensions(request.camera, 1280)
            original = packet_bytes(
                self._packet(project, snapshot, camera, width, height)
            )
            pending = root.with_name(root.name + ".pending")
            pending.parent.mkdir(exist_ok=True)
            if pending.exists():
                shutil.rmtree(pending)
            pending.mkdir()
            try:
                # Both enrichers read the same revision-bound sidecar separately.
                # A concurrent explicit location apply must not publish mixed frames.
                self._georeference_unchanged(project_id, georeference_sha256)
                prepared = prepare_environment(
                    prepare_buildings(json.loads(original), self.directory),
                    self.directory,
                )
                prepared["source_packet_sha256"] = sha256(original).hexdigest()
                prepared["preparation_id"] = str(request.request_id)
                prepared["preview_appearance"] = prepare_preview_assets(
                    prepared,
                    pending,
                    cache_directory=self.directory / "preview-cache",
                    blender=blender_binary(),
                )
                renderer_digests = {
                    name: sha256(
                        Path(__file__).with_name(name).read_bytes()
                    ).hexdigest()
                    for name in RENDERER_SCRIPTS
                }
                prepared["renderer_script_sha256"] = renderer_digests
                prepared_data = packet_bytes(prepared)
                digest = sha256(prepared_data).hexdigest()
                preview = SceneRenderPreparation(
                    id=request.request_id,
                    sha256=digest,
                    project_id=project_id,
                    scene_sha256=scene_sha256,
                    horizon_year=request.horizon_year,
                    geometry_version=snapshot.geometry_version,
                    plan_version=snapshot.plan_version,
                    camera=camera,
                    preview=RenderPreparationPreview.model_validate(_preview(prepared)),
                    summary=RenderPreparationSummary.model_validate(_summary(prepared)),
                    provenance=_provenance(prepared),
                )
                preview_data = preview.model_dump_json().encode()
                manifest: dict[str, Any] = {
                    "schema": "green-atlas.render-preparation/1",
                    "project_id": project_id,
                    "state_version": project.state_version,
                    "geometry_version": snapshot.geometry_version,
                    "plan_version": snapshot.plan_version,
                    "source_sha256": snapshot.source_sha256,
                    "georeference_sha256": georeference_sha256,
                    "source_packet_sha256": sha256(original).hexdigest(),
                    "packet_sha256": digest,
                    "preview_sha256": sha256(preview_data).hexdigest(),
                    "request_sha256": sha256(request_data).hexdigest(),
                    "preview_assets": prepared["preview_appearance"]["assets"],
                    "renderer_sha256": renderer_digests,
                }
                self._current_matches(
                    self.scene.repository.get(project_id, lightweight=True), manifest
                )
                (pending / "scene.json").write_bytes(original)
                (pending / "render-scene.json").write_bytes(prepared_data)
                (pending / "preview.json").write_bytes(preview_data)
                (pending / "request.json").write_bytes(request_data)
                (pending / "renderer").mkdir()
                for name in manifest["renderer_sha256"]:
                    shutil.copyfile(
                        Path(__file__).with_name(name), pending / "renderer" / name
                    )
                    if (
                        sha256((pending / "renderer" / name).read_bytes()).hexdigest()
                        != manifest["renderer_sha256"][name]
                    ):
                        raise ValueError("Код рендера изменился во время подготовки")
                (pending / "manifest.json").write_bytes(packet_bytes(manifest))
                with self.lock:
                    if self.closed:
                        raise ValueError("Сервис рендера закрывается")
                    self._georeference_unchanged(project_id, georeference_sha256)
                    pending.rename(root)
            finally:
                if pending.exists():
                    shutil.rmtree(pending)
            return preview

    def create(self, project_id: str, request: SceneRenderRequest) -> SceneRenderJob:
        with project_resource_scope(self.scene.repository, project_id):
            register_project_resource("render-job", str(request.request_id))
            return self._create(project_id, request)

    def _create(self, project_id: str, request: SceneRenderRequest) -> SceneRenderJob:
        with self.lock:
            if self.closed:
                raise ValueError("Сервис рендера закрывается")
            root = self._root(request.request_id)
            request_bytes = request.model_dump_json().encode()
            if (root / "job.json").exists():
                job = self.get(project_id, request.request_id)
                if (root / "request.json").read_bytes() != request_bytes:
                    raise ValueError(
                        "Этот идентификатор уже использован для другого ракурса"
                    )
                return job
            if len(self.cancellations) >= 4:
                raise ValueError(
                    "Очередь рендера заполнена. Дождитесь текущих изображений"
                )
            binary = blender_binary()
            project = self.scene.repository.get(project_id)
            if project.id != project_id:
                raise KeyError("Проект не найден")
            snapshot = self.scene.from_project(project, request.horizon_year)
            if snapshot.content_sha256 != request.scene_sha256:
                raise ValueError("Сцена изменилась. Обновите 3D перед запуском рендера")
            if not snapshot.context_features and not snapshot.objects:
                raise ValueError("В сцене нет геометрии или посадок для рендера")
            width, height = _dimensions(request.camera, request.resolution)
            renderer_source = Path(__file__).parent
            renderer_digests = None
            if request.preparation_id is not None:
                preview, packet, manifest = self._load_preparation(
                    project_id,
                    request.preparation_id,
                    expected_sha=request.preparation_sha256,
                )
                self._current_matches(project, manifest)
                if (
                    preview.scene_sha256 != request.scene_sha256
                    or preview.horizon_year != request.horizon_year
                ):
                    raise ValueError(
                        "Подготовленное окружение относится к другой сцене или горизонту"
                    )
                packet.update(
                    camera=request.camera.model_dump(mode="json"),
                    width=width,
                    height=height,
                )
                packet["preparation_sha256"] = request.preparation_sha256
                packet["preparation_source_packet_sha256"] = manifest[
                    "source_packet_sha256"
                ]
                renderer_source = (
                    self._preparation_root(request.preparation_id) / "renderer"
                )
                renderer_digests = manifest["renderer_sha256"]
            else:
                packet = self._packet(project, snapshot, request.camera, width, height)
            if renderer_digests is None:
                renderer_digests = {
                    name: sha256((renderer_source / name).read_bytes()).hexdigest()
                    for name in RENDERER_SCRIPTS
                }
            # The accepted packet SHA pins both composition and the actual
            # immutable worker set, including legacy two-script preparations.
            packet["renderer_script_sha256"] = renderer_digests
            accepted_data = packet_bytes(packet)
            root.mkdir(exist_ok=False)
            (root / "request.json").write_bytes(request_bytes)
            (root / "scene.json").write_bytes(accepted_data)
            renderer_directory = root / "renderer"
            renderer_directory.mkdir()
            for name in renderer_digests:
                shutil.copyfile(renderer_source / name, renderer_directory / name)
            _verify_renderer(renderer_directory, renderer_digests)
            job = SceneRenderJob(
                id=request.request_id,
                project_id=project_id,
                status="queued",
                stage="В очереди",
                scene_sha256=request.scene_sha256,
                width=width,
                height=height,
                preparation_id=request.preparation_id,
                preparation_sha256=request.preparation_sha256,
            )
            self._save(job)
            event = Event()
            self.cancellations[str(job.id)] = event
            self._job_projects[str(job.id)] = project_id
            self.executor.submit(
                self._run, job, binary, event, sha256(accepted_data).hexdigest()
            )
            return job

    def cancel(self, project_id: str, job_id: UUID) -> SceneRenderJob:
        with self.lock:
            job = self.get(project_id, job_id)
            if job.status in {"queued", "rendering"}:
                self.cancellations[str(job_id)].set()
                job = job.model_copy(
                    update={"status": "cancelled", "stage": "Отменено"}
                )
                self._save(job)
            return job

    def file(self, project_id: str, job_id: UUID, filename: str) -> Path:
        job = self.get(project_id, job_id)
        if job.status != "completed":
            raise ValueError("Результат рендера ещё не готов")
        if filename not in {"image.png", "receipt.json"}:
            raise KeyError("Файл не найден")
        return self._root(job_id) / filename

    def _run(
        self, job: SceneRenderJob, binary: str, event: Event, packet_digest: str
    ) -> None:
        try:
            with project_resource_scope(self.scene.repository, job.project_id):
                self._run_owned(job, binary, event, packet_digest)
        except KeyError:
            # Проект удалён до старта очереди. Не создаём заново каталог задания.
            event.set()
        finally:
            with self.lock:
                self.cancellations.pop(str(job.id), None)
                self._job_projects.pop(str(job.id), None)

    def _run_owned(
        self, job: SceneRenderJob, binary: str, event: Event, packet_digest: str
    ) -> None:
        root = self._root(job.id)
        process = None
        try:
            if event.is_set():
                return
            with self.lock:
                if event.is_set():
                    return
                self._save(
                    job.model_copy(
                        update={
                            "status": "rendering",
                            "stage": "Используем подготовленное окружение"
                            if job.preparation_id is not None
                            else "Получаем высоты зданий",
                        }
                    )
                )
            source_data = (root / "scene.json").read_bytes()
            if sha256(source_data).hexdigest() != packet_digest:
                raise ValueError("Исходный пакет рендера изменился после принятия")
            packet = json.loads(source_data)
            renderer_digests = packet.get("renderer_script_sha256")
            if job.preparation_id is not None:
                _, _, preparation_manifest = self._load_preparation(
                    job.project_id,
                    job.preparation_id,
                    expected_sha=job.preparation_sha256,
                )
                if (
                    packet.get("preparation_id") != str(job.preparation_id)
                    or packet.get("preparation_sha256") != job.preparation_sha256
                ):
                    raise ValueError("Рендер не связан с подготовленным окружением")
                if renderer_digests is None:
                    # Already accepted legacy jobs did not include this packet
                    # field; their preparation still pins the exact old worker.
                    renderer_digests = preparation_manifest["renderer_sha256"]
                elif renderer_digests != preparation_manifest["renderer_sha256"]:
                    raise ValueError("Код рендера не соответствует подготовке")
            else:
                georeference_sha256 = self._georeference_sha256(job.project_id)
                packet = prepare_buildings(packet, self.directory)
                with self.lock:
                    if event.is_set():
                        return
                    self._save(
                        job.model_copy(
                            update={
                                "status": "rendering",
                                "stage": "Собираем окружение",
                            }
                        )
                    )
                packet = prepare_environment(packet, self.directory)
                self._georeference_unchanged(job.project_id, georeference_sha256)
            if renderer_digests is None:
                # Pre-upgrade jobs without a preparation used exactly this set.
                renderer_digests = {
                    name: sha256((root / "renderer" / name).read_bytes()).hexdigest()
                    for name in LEGACY_RENDERER_SCRIPTS
                }
            renderer_digests = _renderer_digests(renderer_digests)
            _verify_renderer(root / "renderer", renderer_digests)
            packet["source_packet_sha256"] = packet_digest
            prepared_data = packet_bytes(packet)
            (root / "render-scene.json").write_bytes(prepared_data)
            packet_digest = sha256(prepared_data).hexdigest()
            with self.lock:
                if event.is_set():
                    return
                self._save(
                    job.model_copy(
                        update={"status": "rendering", "stage": "Собираем изображение"}
                    )
                )
            with (root / "blender.log").open("wb") as log:
                process = launch_blender(
                    [
                        binary,
                        "--background",
                        "--factory-startup",
                        "-t",
                        "4",
                        "--python-exit-code",
                        "1",
                        "--python",
                        str(root / "renderer" / "blender_worker.py"),
                        "--",
                        str(root / "render-scene.json"),
                        str(root / "image.png"),
                    ],
                    stdout=log,
                    environment=blender_job_environment(root),
                )
                deadline = monotonic() + 600
                while process.poll() is None:
                    if event.wait(0.25) or monotonic() > deadline:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        if event.is_set():
                            return
                        raise ValueError(
                            "Рендер превысил 10 минут. Попробуйте меньший размер изображения"
                        )
            if process.returncode != 0:
                raise ValueError(
                    "Blender не смог собрать изображение. Подробности сохранены в журнале рендера"
                )
            image = (root / "image.png").read_bytes()
            if (
                image[:8] != b"\x89PNG\r\n\x1a\n"
                or int.from_bytes(image[16:20], "big") != job.width
                or int.from_bytes(image[20:24], "big") != job.height
            ):
                raise ValueError("Blender вернул повреждённое изображение")
            receipt = json.loads(
                (root / "renderer-receipt.json").read_text(encoding="utf-8")
            )
            if receipt.get("packet_sha256") != packet_digest:
                raise ValueError("Изображение не связано с запрошенной сценой")
            if receipt.get("renderer_script_sha256") != renderer_digests:
                raise ValueError("Изображение собрано другим кодом рендера")
            _verify_renderer(root / "renderer", renderer_digests)
            receipt.update(
                project_id=job.project_id,
                scene_sha256=job.scene_sha256,
                image_sha256=sha256(image).hexdigest(),
            )
            if job.preparation_id is not None:
                receipt.update(
                    preparation_id=str(job.preparation_id),
                    preparation_sha256=job.preparation_sha256,
                    preparation_source_packet_sha256=packet.get(
                        "preparation_source_packet_sha256"
                    ),
                )
            height_counts = packet["building_heights"]["counts"]
            external_count = height_counts.get("osm_height", 0) + height_counts.get(
                "osm_levels", 0
            )
            proxy_count = height_counts.get("render_class_proxy", 0)
            height_message = (
                f"Высоты из OSM: {external_count}. Условные объёмы: {proxy_count}."
            )
            if packet["building_heights"]["osm"]["status"] != "loaded":
                height_message += " Данные OSM не применены; условные высоты используются только для изображения."
            tree_count = len(packet["environment_trees"])
            neighbour_count = len(packet["environment_buildings"])
            height_message += f" Окружение: {tree_count} условных деревьев, {neighbour_count} соседних зданий карты."
            (root / "receipt.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            with self.lock:
                if not event.is_set():
                    prefix = f"/api/projects/{job.project_id}/scene-renders/{job.id}"
                    self._save(
                        job.model_copy(
                            update={
                                "status": "completed",
                                "stage": "Готово",
                                "image_url": prefix + "/image",
                                "receipt_url": prefix + "/receipt",
                                "message": height_message,
                            }
                        )
                    )
        except Exception as error:  # noqa: BLE001 - isolate all worker failures from the editor
            with self.lock:
                if not event.is_set():
                    self._save(
                        job.model_copy(
                            update={
                                "status": "failed",
                                "stage": "Не удалось собрать",
                                "message": str(error),
                            }
                        )
                    )
        finally:
            with self.lock:
                self.cancellations.pop(str(job.id), None)

    def cancel_project(self, project_id: str) -> None:
        with self.lock:
            for identifier, event in self.cancellations.items():
                if self._job_projects.get(identifier) == project_id:
                    event.set()

    def close(self) -> None:
        with self.lock:
            self.closed = True
            for event in self.cancellations.values():
                event.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
