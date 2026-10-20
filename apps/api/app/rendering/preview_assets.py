"""Content-addressed browser derivatives of the frozen native appearance bundle.

Licensed source assets stay in local render storage. Public descriptors contain
only opaque IDs and preparation-relative files, never workstation paths.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import threading
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.projects.resource_scope import register_project_resource
from app.rendering.assets import file_sha
from app.rendering.blender_runtime import blender_job_environment, launch_blender

_LOCK = threading.Lock()
_SETTINGS = {
    "schema": "green-atlas.browser-appearance/1",
    "near_triangles": 56000,
    "far_triangles": 5000,
    "vegetation_representation": "sampled-native-mesh",
    "model_texture_pixels": 512,
    "surface_texture_pixels": 1024,
    "axis": "Y-up; ground centred; native height and radius normalised to one",
}
_SURFACES = {
    "road": {"tile_width_m": 1.85, "normal_strength": 0.34},
    "sidewalk": {"tile_width_m": 2.8, "normal_strength": 0.10},
    "lawn": {"tile_width_m": 2.2, "normal_strength": 0.30},
}
_MAX_FILE_BYTES = 24 * 1024 * 1024
_MAX_TOTAL_BYTES = 160 * 1024 * 1024


def _model_assignment(
    row: dict[str, Any], assets: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    native_path = row["relative_path"]
    near, far = assets[native_path + ":near"], assets[native_path + ":far"]
    return {
        "object_id": row["object_id"],
        "model_asset_id": near["id"],
        "far_model_asset_id": far["id"],
        "rotation_z": int(sha256(row["object_id"].encode()).hexdigest()[:8], 16)
        / 0xFFFFFFFF
        * math.tau,
    }


def _read_glb(path: Path) -> dict[str, Any]:
    """Reject truncated glTF and external dependencies before exposing bytes."""
    with path.open("rb") as stream:
        header = stream.read(20)
        if len(header) != 20:
            raise ValueError("Неполная GLB-модель предпросмотра")
        magic, version, length, chunk_size, chunk_type = struct.unpack(
            "<4sIIII", header
        )
        if (
            magic != b"glTF"
            or version != 2
            or length != path.stat().st_size
            or length > _MAX_FILE_BYTES
            or chunk_type != 0x4E4F534A
            or chunk_size > length - 20
            or chunk_size % 4
        ):
            raise ValueError("Некорректная GLB-модель предпросмотра")
        value = json.loads(stream.read(chunk_size))
        binary_header = stream.read(8)
        if len(binary_header) != 8:
            raise ValueError("GLB-модель не содержит двоичных данных")
        binary_size, binary_type = struct.unpack("<II", binary_header)
        if (
            binary_type != 0x004E4942
            or binary_size % 4
            or length != 28 + chunk_size + binary_size
        ):
            raise ValueError("Некорректные двоичные данные GLB-модели")
    if not isinstance(value, dict) or not value.get("meshes"):
        raise ValueError("GLB-модель предпросмотра не содержит сетку")
    if any("uri" in row for key in ("buffers", "images") for row in value.get(key, [])):
        raise ValueError("GLB-модель предпросмотра содержит внешние зависимости")
    buffers = value.get("buffers", [])
    if len(buffers) != 1 or not 0 < buffers[0]["byteLength"] <= binary_size:
        raise ValueError("Некорректный размер буфера GLB-модели")
    for view in value.get("bufferViews", []):
        if (
            view.get("buffer", 0) != 0
            or view.get("byteOffset", 0) < 0
            or view["byteLength"] <= 0
            or view.get("byteOffset", 0) + view["byteLength"] > buffers[0]["byteLength"]
        ):
            raise ValueError("Некорректная область буфера GLB-модели")
    return value


def _valid_manifest(directory: Path, cache_key: str) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_bytes())
    if manifest.get("cache_key") != cache_key or not isinstance(
        manifest.get("assets"), list
    ):
        raise ValueError("Не совпадает основание ресурсов предпросмотра")
    total = 0
    ids = set()
    for row in manifest["assets"]:
        name = row["filename"]
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or row["id"] != Path(name).stem
            or len(row["id"]) != 64
            or any(c not in "0123456789abcdef" for c in row["id"])
            or row["id"] in ids
            or row["kind"] not in ("model", "texture")
            or (row["kind"] == "model" and not name.endswith(".glb"))
            or (row["kind"] == "texture" and not name.endswith(".jpg"))
        ):
            raise ValueError("Некорректный адрес ресурса предпросмотра")
        ids.add(row["id"])
        path = directory / name
        size = path.stat().st_size
        total += size
        if (
            size != row["size_bytes"]
            or size > _MAX_FILE_BYTES
            or file_sha(path) != row["sha256"]
        ):
            raise ValueError("Ресурс предпросмотра изменён или повреждён")
        if row["kind"] == "model":
            _read_glb(path)
            for corner in ("min", "max"):
                values = row["bounds"][corner]
                if len(values) != 3 or any(
                    not math.isfinite(v) or abs(v) > 1.001 for v in values
                ):
                    raise ValueError("Некорректные границы модели предпросмотра")
        else:
            with path.open("rb") as stream:
                if stream.read(3) != b"\xff\xd8\xff":
                    raise ValueError("Некорректная текстура предпросмотра")
    if total > _MAX_TOTAL_BYTES or len(ids) > 256:
        raise ValueError("Превышен объём ресурсов предпросмотра")
    identity = manifest.get("identity")
    if (
        not isinstance(identity, dict)
        or sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        != cache_key
    ):
        raise ValueError("Повреждено основание кеша предпросмотра")
    for name, expected in (
        ("preview_asset_worker.py", identity["worker_sha256"]),
        ("render_style.py", identity["style_sha256"]),
    ):
        if file_sha(directory / "renderer" / name) != expected:
            raise ValueError("Изменён конвертер ресурсов предпросмотра")
    return manifest


def prepare_preview_assets(
    packet: dict[str, Any],
    preparation_directory: Path,
    *,
    cache_directory: Path | None = None,
    blender: str | None = None,
) -> dict[str, Any]:
    """Convert the exact native variants once, then freeze checked local copies.

    The native packet and its appearance are never changed. The caller stores
    this returned descriptor as ``preview_appearance`` in its frozen packet.
    ``blender`` is supplied by the application binary resolver.
    """
    appearance = packet["appearance"]
    worker = Path(__file__).with_name("preview_asset_worker.py")
    source_style = Path(__file__).with_name("render_style.py")
    worker_data, style_data = worker.read_bytes(), source_style.read_bytes()
    assignments = [
        row for key in ("plants", "context_plants") for row in appearance.get(key, [])
    ]
    models = {row["relative_path"]: row["path"] for row in assignments}
    if len(models) > 48:
        raise ValueError("Слишком много вариантов моделей для предпросмотра")
    files = {row["path"]: row for row in appearance["files"]}
    for row in assignments:
        if row["path"] not in files or models[row["relative_path"]] != row["path"]:
            raise ValueError("Модель не принадлежит замороженному оформлению")
    # Source bundle integrity is checked even when derivatives already exist.
    for path, row in files.items():
        if not Path(path).is_file() or file_sha(Path(path)) != row["sha256"]:
            raise ValueError("Исходный ресурс предпросмотра изменён")
    identity = {
        "appearance_bundle_sha256": appearance["bundle_sha256"],
        "worker_sha256": sha256(worker_data).hexdigest(),
        "style_sha256": sha256(style_data).hexdigest(),
        "models": sorted(models),
        "settings": _SETTINGS,
    }
    cache_key = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = cache_directory or preparation_directory.parent.parent / "preview-cache"
    directory = cache / cache_key
    register_project_resource("preview-cache", cache_key)
    with _LOCK:
        try:
            manifest = _valid_manifest(directory, cache_key)
        except (OSError, ValueError, TypeError, KeyError):
            if not blender:
                raise ValueError(
                    "Blender не найден для подготовки моделей предпросмотра"
                ) from None
            cache.mkdir(parents=True, exist_ok=True)
            # Экспорт glTF в Blender может отвергнуть путь длиннее MAX_PATH.
            # Короткое временное имя не меняет привязку manifest к ключу кеша.
            pending = cache / (".pending-" + uuid4().hex)
            pending.mkdir()
            renderer = pending / "renderer"
            renderer.mkdir()
            frozen_worker = renderer / worker.name
            frozen_worker.write_bytes(worker_data)
            (renderer / source_style.name).write_bytes(style_data)
            request = {
                "cache_key": cache_key,
                "identity": identity,
                "settings": _SETTINGS,
                "models": models,
                "appearance": appearance,
                "surfaces": _SURFACES,
                "output_directory": str(pending),
            }
            request_path = pending / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            try:
                with (pending / "blender.log").open("wb") as log:
                    child = launch_blender(
                        [
                            blender,
                            "--background",
                            "--factory-startup",
                            "-t",
                            "2",
                            "--python-exit-code",
                            "1",
                            "--python",
                            str(frozen_worker),
                            "--",
                            str(request_path),
                        ],
                        stdout=log,
                        environment=blender_job_environment(pending),
                    )
                    try:
                        returncode = child.wait(timeout=240)
                    finally:
                        if child.poll() is None:
                            child.kill()
                            child.wait()
                if returncode:
                    raise ValueError("Не удалось подготовить Botaniq-модели для 3D")
                manifest = _valid_manifest(pending, cache_key)
                if directory.exists():
                    # Keep invalid cache bytes as diagnostic data, never reuse.
                    directory.rename(cache / (cache_key + ".invalid-" + str(uuid4())))
                pending.rename(directory)
            except subprocess.TimeoutExpired:
                raise ValueError(
                    "Истекло время подготовки моделей предпросмотра"
                ) from None
            except OSError:
                raise ValueError(
                    "Не удалось запустить Blender для предпросмотра"
                ) from None
    asset_directory = preparation_directory / "preview-assets"
    asset_directory.mkdir(parents=True, exist_ok=True)
    public_assets, by_key = [], {}
    for row in manifest["assets"]:
        source, target = directory / row["filename"], asset_directory / row["filename"]
        if target.exists():
            if file_sha(target) != row["sha256"]:
                raise ValueError("Замороженный ресурс предпросмотра изменён")
        else:
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
        descriptor = {
            key: value for key, value in row.items() if key not in ("filename", "key")
        }
        descriptor["relative_path"] = "preview-assets/" + row["filename"]
        public_assets.append(descriptor)
        by_key[row["key"]] = row
    materials = [
        {
            "kind": kind,
            **settings,
            "maps": {role: by_key[kind + ":" + role]["id"] for role in roles["maps"]},
        }
        for kind, settings in _SURFACES.items()
        for roles in [appearance["materials"][kind]]
    ]
    return {
        "schema": _SETTINGS["schema"],
        "cache_key": cache_key,
        "source_bundle_sha256": appearance["bundle_sha256"],
        "converter_sha256": identity["worker_sha256"],
        "assets": public_assets,
        "materials": materials,
        "plants": [
            _model_assignment(row, by_key) for row in appearance.get("plants", [])
        ],
        "context_plants": [
            _model_assignment(row, by_key)
            for row in appearance.get("context_plants", [])
        ],
    }
