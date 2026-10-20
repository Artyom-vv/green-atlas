"""Чтение PE imports и адресный реестр зависимостей без загрузки DLL."""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
from pathlib import Path

from app.distribution.windows_layout import verify_amd64_pe

CRT_CURATED = frozenset({
    "concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_threads.dll",
})


def pe_imports(path: Path) -> tuple[str, ...]:
    """Прочитать обычные и delay imports PE32+; повреждение не скрывается."""
    verify_amd64_pe(path)
    if path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("PE превышает сборочный предел 512 МиБ")
    data = path.read_bytes()

    def number(offset: int, fmt: str = "<I") -> int:
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError("PE содержит адрес вне файла")
        return int(struct.unpack_from(fmt, data, offset)[0])

    header = number(60)
    count, optional_size = number(header + 6, "<H"), number(header + 20, "<H")
    optional = header + 24
    if number(optional, "<H") != 0x20B or optional_size < 112 or count > 128:
        raise ValueError("Ожидался ограниченный PE32+ заголовок")
    table = optional + optional_size
    size_headers = number(optional + 60)

    def rva(value: int) -> int:
        if value < size_headers and value < len(data):
            return value
        for index in range(count):
            section = table + 40 * index
            virtual, address, raw_size, raw = (number(section + n) for n in (8, 12, 16, 20))
            if address <= value < address + max(virtual, raw_size):
                delta = value - address
                if delta >= raw_size or raw + delta >= len(data):
                    raise ValueError("PE RVA не относится к сохранённым байтам")
                return raw + delta
        raise ValueError("PE RVA не сопоставлен секции")

    names: set[str] = set()
    directory_count = number(optional + 108)
    for directory, descriptor_size, name_offset in ((1, 20, 12), (13, 32, 4)):
        if directory >= directory_count:
            continue
        if 112 + (directory + 1) * 8 > optional_size:
            raise ValueError("PE data directory выходит из optional header")
        address = number(optional + 112 + directory * 8)
        length = number(optional + 116 + directory * 8)
        if not address:
            continue
        start = rva(address)
        if length < descriptor_size or length > 1024 * 1024:
            raise ValueError("Некорректный размер PE import directory")
        for index in range(min(length // descriptor_size, 4096)):
            offset = start + index * descriptor_size
            descriptor = data[offset:offset + descriptor_size]
            if len(descriptor) != descriptor_size:
                raise ValueError("Обрезанный PE import descriptor")
            if not any(descriptor):
                break
            if directory == 13 and number(offset) != 1:
                raise ValueError("Delay imports должны использовать RVA")
            name_start = rva(number(offset + name_offset))
            end = data.find(b"\0", name_start, name_start + 261)
            if end < 0:
                raise ValueError("Имя DLL не ограничено")
            name = data[name_start:end].decode("ascii").lower()
            if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-." for ch in name):
                raise ValueError("PE содержит недопустимое имя DLL")
            names.add(name)
        else:
            raise ValueError("PE import directory не завершён")
    return tuple(sorted(names))


def dependency_inventory(
    package: Path, *, crt: Path, autocad: Path | None = None,
    objectarx_sdk: Path | None = None, objectarx_archive_sha256: str | None = None,
) -> dict[str, object]:
    """Копировать только импортируемые CRT; AutoCAD и системные DLL остаются внешними."""
    if os.name != "nt":
        raise RuntimeError("Реестр DLL разрешается на Windows машине сборки")
    windows = package / "Contents/Windows"
    system = Path(os.environ["SystemRoot"]) / "System32"
    from app.distribution.objectarx_imports import (
        LIBRARIES,
        load_catalog,
        validate_native_evidence,
    )

    if (autocad is None) == (objectarx_sdk is None) or (objectarx_sdk is None) != (objectarx_archive_sha256 is None):
        raise ValueError("Выберите installed AutoCAD root или explicit ObjectARX SDK root+archive SHA")
    catalog = load_catalog(objectarx_sdk, objectarx_archive_sha256) if objectarx_sdk is not None and objectarx_archive_sha256 is not None else None
    if catalog is not None:
        validate_native_evidence(windows, catalog)
    cad = {p.name.lower(): p for p in autocad.rglob("*") if p.suffix.lower() in {".dll", ".dbx", ".exe"} and p.is_file()} if autocad is not None else {}
    autodesk_modules = {pin.module for pin in LIBRARIES.values() if pin.module is not None}
    curated = {p.name.lower(): p for p in crt.iterdir() if p.name.lower() in CRT_CURATED and p.is_file()}
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        if name not in curated:
            raise ValueError("В локальном CRT redist отсутствует " + name)
    # Standalone host получает базовый CRT; worker копию проверяем отдельно реальным Core.
    copied: dict[str, str] = {}
    pending = [p for p in windows.rglob("*") if p.suffix.lower() in {".exe", ".arx", ".crx", ".dbx", ".dll", ".pyd"}]
    visited: set[Path] = set()
    records: list[dict[str, object]] = []
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        for directory in (windows, windows / "Worker"):
            destination = directory / name
            if not destination.exists():
                shutil.copy2(curated[name], destination)
                pending.append(destination)
                copied[destination.relative_to(package).as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
    while pending:
        binary = pending.pop()
        if binary in visited:
            continue
        if binary.name.lower() in autodesk_modules:
            raise ValueError("Autodesk runtime module не должен входить в payload: " + binary.name)
        visited.add(binary)
        rows: list[dict[str, object]] = []
        for name in pe_imports(binary):
            in_worker = binary.is_relative_to(windows / "Worker")
            in_api = binary.is_relative_to(windows / "Runtime")
            scope = windows / "Worker" if in_worker else windows / "Runtime" if in_api else windows
            search: tuple[Path, ...] = (binary.parent / name, scope / name)
            if in_api:
                search += (scope / "_internal" / name,)
            local = next((p for p in search if p.is_file()), None)
            loader = "module-or-scope-directory"
            if local is None and in_api and binary.suffix.lower() == ".pyd" and binary.is_relative_to(scope / "_internal"):
                relative = binary.relative_to(scope / "_internal")
                wheel = relative.parts[0]
                # Это адресные каталоги wheel hooks, а не произвольный DLL rglob.
                if wheel in {"numpy", "shapely", "pyproj"}:
                    candidate = scope / "_internal" / (wheel + ".libs") / name
                    if candidate.is_file():
                        local = candidate
                        loader = "pyinstaller-wheel-hook-candidate"
            if local is None and name in curated:
                local = binary.parent / name
                shutil.copy2(curated[name], local)
                pending.append(local)
                copied[local.relative_to(package).as_posix()] = hashlib.sha256(local.read_bytes()).hexdigest()
            if local is not None:
                rows.append({"dll": name, "provider": "payload", "path": local.relative_to(package).as_posix(), "loader": loader})
            elif name.startswith(("api-ms-win-", "ext-ms-win-")) or (system / name).is_file():
                rows.append({"dll": name, "provider": "windows"})
            elif catalog is not None and name in catalog.modules and binary in {windows / "GreenAtlasBridge.arx", windows / "Worker/GreenAtlasQuery.crx"}:
                rows.append(catalog.module_evidence(name, target="core" if in_worker else "gui"))
            elif name in cad and binary in {windows / "GreenAtlasBridge.arx", windows / "Worker/GreenAtlasQuery.crx"}:
                rows.append({"dll": name, "provider": "autocad2027", "sha256": hashlib.sha256(cad[name].read_bytes()).hexdigest()})
            else:
                raise ValueError(f"Не найдена DLL {name}, импортируемая {binary.name}")
        records.append({"binary": binary.relative_to(package).as_posix(), "imports": rows})
    result: dict[str, object] = {"schema": "green-atlas.windows-dependencies/1", "resolution": "objectarx-sdk-import-contract" if catalog is not None else "build-file-inventory", "native_execution_qualified": False, "copied_crt": copied, "binaries": sorted(records, key=lambda row: str(row["binary"]))}
    if catalog is not None:
        result["objectarx_sdk"] = catalog.report()
    return result
