"""Закреплённый import-контракт ObjectARX 2027 без поставки Autodesk модулей.

Имена внешних DLL/DBX читаются из AMD64 COFF import libraries официального
архива. Это сборочное доказательство, а не проверка установленного AutoCAD.
Формат: https://learn.microsoft.com/windows/win32/debug/pe-format#import-library-format
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARCHIVE_SHA256 = "cf4c54b1b440c4e40d418e730f306f18185d026d917dc3c864e60973a981e072"


@dataclass(frozen=True)
class LibraryPin:
    size: int
    sha256: str
    module: str | None


# SHA получены из официального архива выше и сверены с native CI build receipts.
LIBRARIES = {
    "lib-x64/accore.lib": LibraryPin(3049348, "01ab678e5a87fb1669fe41ed3939502f1d78b936be9c50ef059d3f658a8ead0f", "accore.dll"),
    "lib-x64/acdb26.lib": LibraryPin(9002740, "0fab6ef71b51202321b21991cb1bce8014f9dcbdb501bd4ba64078bbfecb5708", "acdb26.dll"),
    "lib-x64/acge26.lib": LibraryPin(1271748, "13ae1b6729e9d05ded1248d2b5f0334a669f401de69aaa37c77518a87d4de67c", "acge26.dll"),
    "lib-x64/rxapi.lib": LibraryPin(478270, "12381c5651912292bbeccf17da3b4d5fc347fd9663c081d2abb394a0aaf0651d", None),
    "lib-x64/acpal.lib": LibraryPin(642516, "c78bc24f5795f7c02a45ecc357a1e72799ad1fd22cc74ad48da313cfa6bffbcf", "acpal.dll"),
    "lib-x64/acgeoment.lib": LibraryPin(1655982, "501be0a236c440d2bf0754ad2b0b0763effd934aec83c19a9f27643925e48875", "acgeomentobj.dbx"),
    "lib-x64/acad.lib": LibraryPin(233652, "8a1b4d86f69c2f485ee2859e0fee876680e5b06357fc103d825992d29778a815", "acad.exe"),
    "utils/brep/lib-x64/acbr26.lib": LibraryPin(243190, "b82f7137f9fddac130cb640d4bbb60ba43844e469cfdce19a67ce35a5e60e104", "acbr26.dbx"),
    "utils/brep/lib-x64/acgex26.lib": LibraryPin(332766, "0cd17e44487155b5054330408d277c95c39a071ce0392527cd3841429162d12e", "acgex26.dll"),
}


def import_modules(data: bytes) -> tuple[str, ...]:
    """Проверить ограниченный COFF archive и прочитать имена short imports."""
    if not data.startswith(b"!<arch>\n") or len(data) > 32 * 1024**2:
        raise ValueError("Ожидалась ограниченная COFF import library")
    offset, members = 8, 0
    modules: set[str] = set()
    while offset < len(data):
        header = data[offset:offset + 60]
        if len(header) != 60 or header[-2:] != b"`\n" or not header[48:58].strip().isdigit():
            raise ValueError("Повреждён COFF archive member header")
        size = int(header[48:58])
        member = data[offset + 60:offset + 60 + size]
        if len(member) != size or members >= 65536:
            raise ValueError("COFF archive member выходит за границы")
        name = header[:16].strip()
        if name not in {b"/", b"//"}:
            if len(member) < 20:
                raise ValueError("Обрезанный COFF object")
            if member[:4] == b"\0\0\xff\xff":
                version, machine = struct.unpack_from("<HH", member, 4)
                if version not in {0, 2} or machine != 0x8664:
                    raise ValueError("COFF import/bigobj должен быть AMD64")
                if version == 2 and len(member) < 56:
                    raise ValueError("Обрезанный AMD64 bigobj header")
                if version == 0:
                    length = struct.unpack_from("<I", member, 12)[0]
                    kind = struct.unpack_from("<H", member, 18)[0]
                    strings = member[20:].split(b"\0")
                    if (length != len(member) - 20 or kind >> 5 or kind & 3 > 2
                            or (kind >> 2) & 7 > 4 or len(strings) < 3
                            or not strings[0] or len(strings[0]) > 4096 or strings[-1]):
                        raise ValueError("Повреждён short import header/string")
                    module = strings[1].decode("ascii").lower()
                    if not re.fullmatch(r"[a-z0-9_-][a-z0-9_.-]*\.(?:dll|dbx|exe)", module):
                        raise ValueError("Import library содержит недопустимое имя модуля")
                    modules.add(module)
            elif struct.unpack_from("<H", member)[0] != 0x8664:
                raise ValueError("COFF object должен быть AMD64")
        members += 1
        offset += 60 + size + size % 2
    if offset != len(data) or not members:
        raise ValueError("Нарушена граница COFF archive")
    return tuple(sorted(modules))


def _plain_file(root: Path, relative: str) -> Path:
    path = root / relative
    for current in (path, *path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("SDK/evidence не допускает ссылки/reparse")
        if current == root:
            break
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("SDK/evidence ожидает обычный файл")
    return path


@dataclass(frozen=True)
class SdkCatalog:
    libraries: dict[str, LibraryPin]
    modules: dict[str, str]

    def module_evidence(self, module: str, *, target: str) -> dict[str, object]:
        library = self.modules[module]
        if target not in {"gui", "core"} or (target == "core" and library == "lib-x64/acad.lib"):
            raise ValueError("Этот ObjectARX module не входит в import contract цели")
        pin = self.libraries[library]
        return {"dll": module, "provider": "autocad2027", "evidence": "sdk-import-library",
                "import_library": library, "import_library_sha256": pin.sha256,
                "runtime_presence_checked": False}

    def report(self) -> dict[str, object]:
        return {"archive_sha256": ARCHIVE_SHA256, "archive_sha256_verification": "caller_asserted",
                "library_hashes_verified": True, "runtime_presence_checked": False,
                "libraries": [{"path": path, "size": pin.size, "sha256": pin.sha256,
                               "modules": [pin.module] if pin.module else []}
                              for path, pin in sorted(self.libraries.items())]}


def load_catalog(root: Path, archive_sha256: str) -> SdkCatalog:
    """Сверить реальные SDK bytes с закреплённым официальным архивом 2027."""
    if archive_sha256.lower() != ARCHIVE_SHA256:
        raise ValueError("Нужен закреплённый официальный ObjectARX 2027 archive SHA256")
    modules: dict[str, str] = {}
    root = root.absolute()
    for relative, pin in LIBRARIES.items():
        path = _plain_file(root, relative)
        if path.stat().st_size != pin.size:
            raise ValueError("Размер ObjectARX import library не совпал: " + relative)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != pin.sha256:
            raise ValueError("SHA ObjectARX import library не совпал: " + relative)
        found = import_modules(data)
        if found != ((pin.module,) if pin.module else ()):
            raise ValueError("Модули ObjectARX import library не совпали: " + relative)
        for module in found:
            if module in modules:
                raise ValueError("Неоднозначный ObjectARX provider: " + module)
            modules[module] = relative
    return SdkCatalog(dict(LIBRARIES), modules)


def validate_native_evidence(windows: Path, catalog: SdkCatalog) -> None:
    """Связать каждый разрешённый native PE с теми же SDK library bytes."""
    for target, binary in (("gui", "GreenAtlasBridge.arx"), ("core", "Worker/GreenAtlasQuery.crx")):
        evidence_path = _plain_file(windows, "BuildEvidence/" + target + ".json")
        if evidence_path.stat().st_size > 2 * 1024**2:
            raise ValueError("Native evidence превышает размер")
        evidence: Any = json.loads(evidence_path.read_bytes())
        if (not isinstance(evidence, dict) or evidence.get("schema") != "green-atlas.packaged-native-evidence/1"
                or evidence.get("target") != target or evidence.get("execution_qualified") is not False
                or evidence.get("binary_sha256") != hashlib.sha256(_plain_file(windows, binary).read_bytes()).hexdigest()):
            raise ValueError("Native evidence не соответствует текущему PE: " + target)
        libraries = evidence.get("sdk_files")
        if not isinstance(libraries, list) or any(not isinstance(row, dict) for row in libraries):
            raise ValueError("Native evidence не содержит SDK file identities")
        observed: dict[str, tuple[object, object]] = {}
        for row in libraries:
            relative = row.get("path")
            if not isinstance(relative, str) or "\\" in relative:
                raise ValueError("Некорректный SDK evidence path")
            if relative.lower().endswith(".lib"):
                name = relative.casefold()
                if name in observed:
                    raise ValueError("Повторная SDK library identity")
                observed[name] = (row.get("size"), row.get("sha256"))
        expected = {path: (pin.size, pin.sha256) for path, pin in catalog.libraries.items()
                    if target == "gui" or path != "lib-x64/acad.lib"}
        if observed != expected:
            raise ValueError("Native PE собран с другим SDK import contract: " + target)
