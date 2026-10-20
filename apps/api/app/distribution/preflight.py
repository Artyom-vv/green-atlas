"""Чтение поставки и исходного seal без запуска приложения и изменения профиля.

Проверка собственных файлов отделена от обнаружения AutoCAD/Blender и готовности
пользовательских моделей. SHA состава не заменяет подпись и реальный запуск.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import stat
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit

from pydantic import Field

from app.distribution.contracts import (
    Capability,
    Component,
    ComponentEvidence,
    ComponentName,
    Contract,
    PackageLayout,
    PackageManifest,
    PayloadEntry,
    Platform,
    PreflightReport,
    QualificationEvidence,
    SourceSeal,
    relative_path,
)

MANIFEST_NAME = "distribution-manifest.json"
RUNTIME_RESOURCES = (
    "cad_bridge/probe_diagnostics.json",
    "dxf_import/layers/recognition_catalog.json",
    "regulations/placement_rules.json",
    "regulations/references.json",
    "shared/messages.ru.json",
    "shared/storage-config.json",
    "species/data/moscow-assortment.json",
    "species/data/source-profiles.json",
    "rendering/blender_worker.py",
    "rendering/render_style.py",
    "rendering/lawn_instances.py",
    "rendering/lawn_scatter.py",
    "rendering/preview_asset_worker.py",
    "rendering/native_style.json",
)


class SourceFile(Contract):
    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class SourceReceipt(Contract):
    files: tuple[SourceFile, ...] = Field(min_length=1)


def build_manifest(
    package: Path,
    layout: PackageLayout,
    *,
    source_root: Path,
    source_manifest: Path,
    qualification: QualificationEvidence | None = None,
) -> PackageManifest:
    """Связать весь собственный payload с проверенным источником и метаданными."""
    source_bytes = source_manifest.read_bytes()
    receipt = SourceReceipt.model_validate_json(source_bytes)
    source_rows = _verify_source(source_root, receipt)
    root = _root(package)
    entries = _inventory(root, layout)
    _verify_components(root, layout, entries)
    api = _component(layout, "api")
    for relative in RUNTIME_RESOURCES:
        expected = source_rows.get("apps/api/app/" + relative)
        if expected is None:
            raise ValueError("В source seal отсутствует runtime-ресурс: " + relative)
        target = _plain_file(root, api.root + "/_internal/app/" + relative)
        if _digest_file(target) != (expected.bytes, expected.sha256):
            raise ValueError("Runtime-ресурс отличается от source seal: " + relative)
    manifest = PackageManifest(
        status="qualified" if qualification is not None else "draft",
        layout=layout,
        source=SourceSeal(
            manifest_sha256=_sha(source_bytes), verified_files=len(source_rows)
        ),
        entries=entries,
        qualification=qualification,
    )
    _verify_qualification(manifest)
    return manifest


def check_package(
    package: Path,
    manifest: PackageManifest,
    *,
    discover_external: bool = False,
) -> PreflightReport:
    """Проверить неизменность и полноту без source checkout и developer окружения."""
    root = _root(package)
    current = _inventory(root, manifest.layout)
    if current != manifest.entries:
        expected = {entry.path: entry for entry in manifest.entries}
        observed = {entry.path: entry for entry in current}
        changed = sorted(
            path
            for path in expected.keys() | observed.keys()
            if expected.get(path) != observed.get(path)
        )
        raise ValueError("Изменён состав поставки: " + ", ".join(changed[:8]))
    _verify_components(root, manifest.layout, current)
    _verify_qualification(manifest)
    return PreflightReport(
        qualification="receipt_matches" if manifest.qualification else "not_claimed",
        package_sha256=manifest_identity(manifest),
        components=component_evidence(manifest),
        external=external_capabilities(manifest.layout.platform)
        if discover_external
        else (),
    )


def manifest_identity(manifest: PackageManifest) -> str:
    """Состав и source seal имеют отпечаток независимо от статуса квалификации."""
    return _json_sha(
        {
            "layout": manifest.layout.model_dump(mode="json"),
            "source": manifest.source.model_dump(mode="json"),
            "entries": [entry.model_dump(mode="json") for entry in manifest.entries],
        }
    )


def component_evidence(manifest: PackageManifest) -> tuple[ComponentEvidence, ...]:
    return tuple(
        ComponentEvidence(
            name=component.name,
            version=component.version,
            inventory_sha256=_json_sha(
                {
                    "component": component.model_dump(mode="json"),
                    "entries": [
                        entry.model_dump(mode="json")
                        for entry in manifest.entries
                        if entry.path.startswith(component.root + "/")
                    ],
                }
            ),
        )
        for component in manifest.layout.components
    )


def macos_layout(package: Path) -> PackageLayout:
    """Прочитать существующее Mac-расположение с независимыми версиями producer/worker."""
    root = _root(package)
    version = _package_version(root)
    desktop = "Contents/Applications/Green Atlas.app"
    resources = desktop + "/Contents/Resources"
    components = (
        ("desktop", desktop, "Contents/MacOS/GreenAtlasDesktop", "Contents/Info.plist"),
        ("api", resources + "/Runtime", "GreenAtlasRuntime", None),
        ("web", resources + "/Web", "index.html", None),
        (
            "gui_producer",
            "Contents/MacOS/GreenAtlasBridge.bundle",
            "Contents/MacOS/GreenAtlasBridge",
            "Contents/Info.plist",
        ),
        (
            "core_worker",
            "Contents/Workers/GreenAtlasQuery.bundle",
            "Contents/MacOS/GreenAtlasBridge",
            "Contents/Info.plist",
        ),
    )
    result = []
    for name, directory, entrypoint, metadata in components:
        info_path = directory + "/" + metadata if metadata else None
        component_version = _metadata_version(root, info_path) if info_path else version
        result.append(
            Component(
                name=cast(ComponentName, name),
                root=directory,
                entrypoint=directory + "/" + entrypoint,
                metadata=info_path,
                version=component_version,
                version_source="metadata" if info_path else "assembly",
            )
        )
    return PackageLayout(
        platform="macos",
        architecture="arm64",
        version=version,
        components=tuple(result),
    )


def external_capabilities(platform: Platform) -> tuple[Capability, ...]:
    """Обнаружить файлы внешних установок, не выполняя проверку CAD или рендера."""
    from app.native_query.transport.autocad_installation import (
        resolve_autocad_installation,
    )

    results = []
    try:
        installation = resolve_autocad_installation(platform=platform)
        results.append(
            Capability(
                name="autocad",
                status="installed_not_probed",
                detail=f"{installation.identity}: {installation.core_executable}",
            )
        )
    except (OSError, ValueError) as error:
        results.append(
            Capability(name="autocad", status="unavailable", detail=str(error))
        )
    if platform == ("windows" if os.name == "nt" else "macos"):
        from app.rendering.blender_runtime import discover_blender

        try:
            blender = discover_blender()
            results.append(
                Capability(
                    name="blender",
                    status="installed_not_probed",
                    detail=str(blender.executable),
                )
            )
        except (OSError, ValueError) as error:
            results.append(
                Capability(name="blender", status="unavailable", detail=str(error))
            )
    else:
        results.append(
            Capability(
                name="blender",
                status="unavailable",
                detail="Установка другой ОС на этом компьютере не проверяется",
            )
        )
    results.append(
        Capability(
            name="botaniq",
            status="user_supplied",
            detail="Пользовательские модели нужны рендеру; их наличие не блокирует core-поставку",
        )
    )
    return tuple(results)


def _root(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("Каталог поставки должен быть обычным каталогом")
    return path.resolve(strict=True)


def _plain_file(root: Path, relative: str) -> Path:
    relative_path(relative)
    path = root / relative
    for parent in (path, *path.parents):
        if parent == root:
            break
        attributes = parent.lstat()
        if parent.is_symlink() or getattr(attributes, "st_file_attributes", 0) & 0x400:
            raise ValueError(
                "Ожидается собственный файл, а не ссылка/reparse: " + relative
            )
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Ожидается обычный файл: " + relative)
    return path


def _inventory(root: Path, layout: PackageLayout) -> tuple[PayloadEntry, ...]:
    entries = []
    runtime_root = root / _component(layout, "api").root
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted([*directories, *files]):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            relative_path(relative)
            if relative == MANIFEST_NAME:
                continue
            if name in {".git", ".venv", "node_modules", ".runtime", "__pycache__"}:
                raise ValueError("Developer-данные не входят в поставку: " + relative)
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                link = os.readlink(path)
                if (
                    not path.is_relative_to(runtime_root)
                    or Path(link).is_absolute()
                    or "\\" in link
                    or ":" in link
                    or "\0" in link
                ):
                    raise ValueError("Недопустимая ссылка поставки: " + relative)
                try:
                    resolved = path.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise ValueError(
                        "Повреждена ссылка поставки: " + relative
                    ) from error
                if not resolved.is_relative_to(runtime_root.resolve(strict=True)):
                    raise ValueError("Ссылка выходит из runtime: " + relative)
                payload = link.encode("utf-8")
                entries.append(
                    PayloadEntry(
                        path=relative,
                        kind="symlink",
                        link_target=link,
                        size_bytes=len(payload),
                        sha256=_sha(payload),
                    )
                )
            elif getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("В поставке найден reparse point: " + relative)
            elif stat.S_ISREG(info.st_mode):
                if path.suffix.lower() in {
                    ".dwg",
                    ".dxf",
                    ".sqlite",
                    ".sqlite3",
                    ".db",
                }:
                    raise ValueError(
                        "Рабочие CAD/базы не входят в поставку: " + relative
                    )
                size, sha = _digest_file(path)
                entries.append(PayloadEntry(path=relative, size_bytes=size, sha256=sha))
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError("В поставке найден специальный файл: " + relative)
    return tuple(sorted(entries, key=lambda entry: entry.path))


def _verify_source(root: Path, receipt: SourceReceipt) -> dict[str, SourceFile]:
    root = _root(root)
    rows = {}
    for row in receipt.files:
        relative_path(row.path)
        if row.path in rows:
            raise ValueError("Повторён путь source seal: " + row.path)
        if _digest_file(_plain_file(root, row.path)) != (row.bytes, row.sha256):
            raise ValueError("Исходник не соответствует source seal: " + row.path)
        rows[row.path] = row
    return rows


def _verify_components(
    root: Path, layout: PackageLayout, entries: tuple[PayloadEntry, ...]
) -> None:
    files = {entry.path: entry for entry in entries if entry.kind == "file"}
    for component in layout.components:
        _plain_file(root, component.entrypoint)
        if component.entrypoint not in files:
            raise ValueError("Не найден вход компонента: " + component.name)
        if component.metadata is not None:
            if _metadata_version(root, component.metadata) != component.version:
                raise ValueError("Версия метаданных не совпадает: " + component.name)
        if component.version_source == "metadata" and component.metadata is None:
            raise ValueError("Для версии нужны метаданные компонента")
    api = _component(layout, "api")
    for relative in RUNTIME_RESOURCES:
        _plain_file(root, api.root + "/_internal/app/" + relative)
    web = _component(layout, "web")
    _verify_web_document_resources(root / web.root)
    if layout.platform == "macos":
        desktop = _component(layout, "desktop")
        info = plistlib.loads(_plain_file(root, desktop.metadata or "").read_bytes())
        if any(key.startswith("GADevelopment") for key in info):
            raise ValueError("Developer-профиль не входит в поставку")
        package_version = _package_version(root)
        if package_version != layout.version or desktop.version != layout.version:
            raise ValueError("Версия desktop/package не соответствует сборке")
    else:
        from app.distribution.windows_layout import validate_windows_payload

        validate_windows_payload(root, layout)
    from app.native_query.archive.archive_faces import (
        FACE_PROTOCOL,
        LEGACY_FACE_PROTOCOL,
        TOPOLOGY_FACE_PROTOCOL,
    )
    from app.native_query.capture.capture_contracts import compatible_archive_worker
    from app.native_query.transport.worker_layout import resolve_worker_layout

    worker = _component(layout, "core_worker")
    descriptor = resolve_worker_layout(
        platform=layout.platform, root=root / worker.root
    )
    if (
        descriptor.version != worker.version
        or descriptor.binary != root / worker.entrypoint
        or descriptor.metadata != root / (worker.metadata or "")
        or descriptor.binary_sha256 != files[worker.entrypoint].sha256
    ):
        raise ValueError("Descriptor Core worker не соответствует составу поставки")
    expected_identity = (
        "ru.green-atlas.native-query-worker"
        if layout.platform == "macos"
        else "green-atlas.query-worker/1"
    )
    if descriptor.identity != expected_identity:
        raise ValueError("В поставке находится чужой модуль Core worker")
    if any(
        not isinstance(value, str) or not value
        for value in (*descriptor.face_protocols, *descriptor.topology_shapes)
    ):
        raise ValueError("Core worker объявляет некорректные metadata-capabilities")
    # Это требования известных сборок, а не предположение по старшинству semver.
    protocols = {
        "0.1.46": {LEGACY_FACE_PROTOCOL},
        "0.1.47": {LEGACY_FACE_PROTOCOL, FACE_PROTOCOL},
        "0.1.48": {LEGACY_FACE_PROTOCOL, FACE_PROTOCOL},
        "0.1.49": {LEGACY_FACE_PROTOCOL, FACE_PROTOCOL, TOPOLOGY_FACE_PROTOCOL},
    }.get(worker.version)
    if protocols is None or not protocols.issubset(descriptor.face_protocols):
        raise ValueError("Core worker не объявляет протоколы известных сборок")
    if worker.version == "0.1.49" and not {"curve", "AcDbRegion", "AcDbHatch"}.issubset(
        descriptor.topology_shapes
    ):
        raise ValueError("Core worker не объявляет поддержку текущих native-границ")
    if not compatible_archive_worker(
        _component(layout, "gui_producer").version,
        _component(layout, "core_worker").version,
    ):
        raise ValueError("Producer и Core worker не образуют разрешённую пару")


class _WebResources(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: list[str] = []
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        source = (
            values.get("src")
            if tag == "script"
            else (values.get("href") if tag == "link" else None)
        )
        if source:
            self.paths.append(source)
            self.scripts += int(tag == "script")


def _verify_web_document_resources(web: Path) -> None:
    html = (web / "index.html").read_text(encoding="utf-8")
    parser = _WebResources()
    parser.feed(html)
    if not parser.scripts:
        raise ValueError("В документе отсутствует вход собранного Web")
    for resource in parser.paths:
        url = urlsplit(resource)
        if url.scheme or url.netloc:
            raise ValueError("Built Web ссылается на внешние ресурсы документа")
        relative = unquote(url.path).lstrip("/")
        _plain_file(web, relative)
    scripts = list(web.rglob("*.js")) + list(web.rglob("*.mjs"))
    if not scripts:
        raise ValueError("В поставке отсутствует собранный JavaScript Web")
    for script in scripts:
        payload = script.read_bytes()
        if any(
            address in payload
            for address in (
                b"http://127.0.0.1:8000",
                b"http://localhost:8000",
                b"http://localhost:5173",
                b"http://127.0.0.1:5173",
            )
        ):
            raise ValueError("Built Web содержит developer API origin")


def _verify_qualification(manifest: PackageManifest) -> None:
    evidence = manifest.qualification
    if evidence is None:
        return
    if (
        evidence.platform != manifest.layout.platform
        or evidence.source_manifest_sha256 != manifest.source.manifest_sha256
        or evidence.payload_manifest_sha256 != manifest_identity(manifest)
        or evidence.components != component_evidence(manifest)
    ):
        raise ValueError(
            "Квитанция не соответствует source seal и компонентам поставки"
        )


def _package_version(root: Path) -> str:
    try:
        version = ET.fromstring(
            _plain_file(root, "PackageContents.xml").read_bytes()
        ).attrib.get("AppVersion")
    except ET.ParseError as error:
        raise ValueError("Повреждён PackageContents.xml") from error
    if not isinstance(version, str) or not version:
        raise ValueError("В PackageContents.xml отсутствует версия продукта")
    return version


def _metadata_version(root: Path, relative: str) -> str:
    path = _plain_file(root, relative)
    if path.suffix.lower() == ".plist":
        data = plistlib.loads(path.read_bytes())
        field = "CFBundleShortVersionString"
    else:
        data = json.loads(path.read_bytes())
        field = (
            "plugin_version"
            if isinstance(data, dict) and "plugin_version" in data
            else "version"
        )
    if not isinstance(data, dict):
        raise ValueError("Метаданные компонента не являются объектом: " + relative)
    version = data.get(field)
    if not isinstance(version, str) or not version:
        raise ValueError("В метаданных отсутствует версия: " + relative)
    return version


def _component(layout: PackageLayout, name: ComponentName) -> Component:
    return next(component for component in layout.components if component.name == name)


def _digest_file(path: Path) -> tuple[int, str]:
    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise ValueError("Файл изменён во время проверки: " + str(path))
    return after.st_size, digest


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_sha(value: object) -> str:
    return _sha(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )
