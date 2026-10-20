"""Файлы и возможности поставленного CAD-работника, отдельно от запуска Core.

Mac сохраняет действующий контракт; Windows CRX остаётся кандидатом поставки.
"""

import json
import os
import plistlib
import stat
from dataclasses import dataclass
from hashlib import file_digest
from pathlib import Path

from app.native_query.transport.autocad_installation import (
    DesktopPlatform,
    desktop_platform,
)
from app.native_query.transport.process_contracts import valid_sha


@dataclass(frozen=True)
class WorkerLayout:
    platform: DesktopPlatform
    root: Path
    binary: Path
    metadata: Path
    version: str
    binary_sha256: str
    face_protocols: tuple[str, ...]
    topology_shapes: tuple[str, ...]
    identity: str | None


def resolve_worker_layout(
    *, platform: DesktopPlatform | None = None, root: Path | None = None
) -> WorkerLayout:
    """Читает явного работника; Mac default указывает только на текущий owned add-in."""
    selected = platform or desktop_platform()
    configured = os.environ.get("GREEN_ATLAS_CAD_RELEASE_WORKER")
    bundle = root if root is not None else Path(configured) if configured else None
    if bundle is None:
        if selected != "macos":
            raise ValueError("Укажите поставленный модуль выпуска AutoCAD")
        bundle = (
            Path.home()
            / "Library/Application Support/Autodesk/ApplicationAddins"
            / "GreenAtlasBridge.bundle/Contents/Workers/GreenAtlasQuery.bundle"
        )
        if not bundle.is_dir():
            raise ValueError(
                "Модуль выпуска AutoCAD не установлен. Обновите комплект Green Atlas"
            )
    bundle = bundle.absolute()
    if selected == "macos":
        metadata = bundle / "Contents/Info.plist"
        binary = bundle / "Contents/MacOS/GreenAtlasBridge"
        if not metadata.is_file() or not binary.is_file():
            raise ValueError(
                "Модуль выпуска AutoCAD повреждён. Переустановите Green Atlas"
            )
        info = plistlib.loads(metadata.read_bytes())
        with binary.open("rb") as stream:
            digest = file_digest(stream, "sha256").hexdigest()
        return WorkerLayout(
            selected,
            bundle,
            binary,
            metadata,
            info["CFBundleShortVersionString"],
            digest,
            tuple(info.get("GAFaceProtocols", ())),
            tuple(info.get("GATopologyShapes", ())),
            info.get("CFBundleIdentifier"),
        )
    if selected != "windows":
        raise ValueError("Платформа CAD-работника не поддерживается")
    metadata = bundle / "worker.json"
    binary = bundle / "GreenAtlasQuery.crx"
    for path in (metadata, binary):
        for child in (path, *path.parents):
            attributes = child.lstat()
            if (
                child.is_symlink()
                or getattr(attributes, "st_file_attributes", 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise ValueError("Модуль выпуска содержит ссылку или reparse point")
        if not path.is_file():
            raise ValueError(
                "Модуль выпуска AutoCAD повреждён. Переустановите Green Atlas"
            )
    with metadata.open("rb") as stream:
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("Недопустимый манифест модуля выпуска")
    info = json.loads(raw, object_pairs_hook=_unique_fields)
    required = {"schema", "target", "binary", "plugin_version", "sha256"}
    if (
        not isinstance(info, dict)
        or not required.issubset(info)
        or set(info) - required - {"face_protocols", "topology_shapes"}
    ):
        raise ValueError("Недопустимый манифест модуля выпуска")
    if (
        info["schema"] != "green-atlas.query-worker/1"
        or info["target"] != "windows-x86_64"
        or info["binary"] != "GreenAtlasQuery.crx"
    ):
        raise ValueError("Неверная идентичность CAD-работника Windows")
    if (
        not isinstance(info["plugin_version"], str)
        or not info["plugin_version"]
        or not isinstance(info["sha256"], str)
    ):
        raise ValueError("Недопустимая версия или SHA CAD-работника")
    valid_sha(info["sha256"])
    capabilities: list[tuple[str, ...]] = []
    for field in ("face_protocols", "topology_shapes"):
        values = info.get(field, [])
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)
        ):
            raise ValueError("Некорректные возможности CAD-работника")
        capabilities.append(tuple(values))
    with binary.open("rb") as stream:
        digest = file_digest(stream, "sha256").hexdigest()
    if digest != info["sha256"]:
        raise ValueError("SHA CAD-работника не совпадает с манифестом")
    return WorkerLayout(
        selected,
        bundle,
        binary,
        metadata,
        info["plugin_version"],
        digest,
        capabilities[0],
        capabilities[1],
        info["schema"],
    )


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Манифест CAD-работника содержит повторяющееся поле")
        result[key] = value
    return result
