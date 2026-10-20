"""Строгий SDK provider проверяется на синтетическом COFF/PE, без AutoCAD."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.distribution import objectarx_imports as sdk
from app.distribution import windows_dependencies as dependencies


def _archive(member: bytes) -> bytes:
    header = b"native.obj/".ljust(16) + b"0".ljust(12) + b"0".ljust(6) * 2
    header += b"100644".ljust(8) + str(len(member)).encode().ljust(10) + b"`\n"
    return b"!<arch>\n" + header + member + (b"\n" if len(member) % 2 else b"")


def _library(module: str | None, *, machine: int = 0x8664) -> bytes:
    if module is None:
        return _archive(struct.pack("<HHIIIHH", machine, 0, 0, 0, 0, 0, 0))
    strings = b"symbol\0" + module.encode() + b"\0"
    return _archive(struct.pack("<HHHHIIHH", 0, 0xFFFF, 0, machine, 0, len(strings), 0, 4) + strings)


def _pe(imports: tuple[str, ...] = ()) -> bytes:
    data = bytearray(4096)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HH", data, 132, 0x8664, 1)
    struct.pack_into("<H", data, 148, 240)
    optional = 152
    struct.pack_into("<H", data, optional, 0x20B)
    struct.pack_into("<I", data, optional + 60, 1024)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<IIII", data, optional + 240 + 8, 2048, 4096, 2048, 1024)
    if imports:
        struct.pack_into("<II", data, optional + 120, 4096, 20 * (len(imports) + 1))
        start = 1024 + 20 * (len(imports) + 1)
        for index, name in enumerate(imports):
            struct.pack_into("<I", data, 1024 + 20 * index + 12, 4096 + start - 1024)
            encoded = name.encode() + b"\0"
            data[start:start + len(encoded)] = encoded
            start += len(encoded)
    return bytes(data)


@pytest.fixture
def sdk_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "SDK"
    pins = {}
    for relative, pin in sdk.LIBRARIES.items():
        data = _library(pin.module)
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        pins[relative] = sdk.LibraryPin(len(data), hashlib.sha256(data).hexdigest(), pin.module)
    monkeypatch.setattr(sdk, "LIBRARIES", pins)
    return root


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sdk_root: Path) -> tuple[Path, Path]:
    root = tmp_path / "package"
    windows = root / "Contents/Windows"
    (windows / "Worker").mkdir(parents=True)
    (windows / "BuildEvidence").mkdir()
    for target, binary in (("gui", "GreenAtlasBridge.arx"), ("core", "Worker/GreenAtlasQuery.crx")):
        data = _pe(("acbr26.dbx", "acgeomentobj.dbx", "accore.dll"))
        (windows / binary).write_bytes(data)
        value = {"schema": "green-atlas.packaged-native-evidence/1", "target": target,
                 "execution_qualified": False, "binary_sha256": hashlib.sha256(data).hexdigest(),
                 "sdk_files": [{"path": path, "size": pin.size, "sha256": pin.sha256}
                               for path, pin in sdk.LIBRARIES.items()
                               if target == "gui" or path != "lib-x64/acad.lib"]}
        (windows / "BuildEvidence" / (target + ".json")).write_text(json.dumps(value))
    crt, system = tmp_path / "CRT", tmp_path / "System"
    crt.mkdir()
    (system / "System32").mkdir(parents=True)
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        (crt / name).write_bytes(_pe())
    monkeypatch.setattr(dependencies, "os", SimpleNamespace(name="nt", environ={"SystemRoot": str(system)}))
    return root, crt


def _inventory(package: tuple[Path, Path], sdk_root: Path) -> dict[str, object]:
    root, crt = package
    return dependencies.dependency_inventory(root, crt=crt, objectarx_sdk=sdk_root,
                                              objectarx_archive_sha256=sdk.ARCHIVE_SHA256)


def test_short_import_module_and_static_amd64() -> None:
    assert sdk.import_modules(_library("AcBr26.dbx")) == ("acbr26.dbx",)
    assert sdk.import_modules(_library(None)) == ()


@pytest.mark.parametrize("module", [None, "accore.dll"])
def test_wrong_machine_rejected(module: str | None) -> None:
    with pytest.raises(ValueError, match="AMD64"):
        sdk.import_modules(_library(module, machine=0x14C))


@pytest.mark.parametrize("module", ["../acbr26.dbx", "C:\\bad.dll", "broken.txt", ""])
def test_short_import_rejects_non_module_name(module: str) -> None:
    with pytest.raises(ValueError, match="имя модуля"):
        sdk.import_modules(_library(module))


@pytest.mark.parametrize("data", [b"", b"!<arch>\n", _library("accore.dll")[:-2]])
def test_truncated_or_empty_library_rejected(data: bytes) -> None:
    with pytest.raises(ValueError):
        sdk.import_modules(data)


def test_catalog_rejects_other_archive_hash(sdk_root: Path) -> None:
    with pytest.raises(ValueError, match="archive SHA256"):
        sdk.load_catalog(sdk_root, "f" * 64)


@pytest.mark.parametrize("same_size", [True, False])
def test_library_mutation_rejected(sdk_root: Path, same_size: bool) -> None:
    path = sdk_root / "lib-x64/accore.lib"
    data = path.read_bytes()
    path.write_bytes(data[:-1] + b"x" if same_size else data + b"x")
    with pytest.raises(ValueError, match="SHA|Размер"):
        sdk.load_catalog(sdk_root, sdk.ARCHIVE_SHA256)


def test_sdk_contract_only_records_hashes_and_external_names(package: tuple[Path, Path], sdk_root: Path) -> None:
    result = _inventory(package, sdk_root)
    assert result["resolution"] == "objectarx-sdk-import-contract"
    assert result["native_execution_qualified"] is False
    description = json.dumps(result)
    assert "runtime_presence_checked" in description
    assert '"import_library_sha256"' in description
    assert '"sha256"' not in json.dumps(next(row for row in result["binaries"]
                                            if row["binary"].endswith("GreenAtlasQuery.crx"))["imports"])
    assert len(result["objectarx_sdk"]["libraries"]) == 9
    assert not list(package[0].rglob("*.lib"))
    assert not list(package[0].rglob("*.dbx"))


@pytest.mark.parametrize("binary", ["GreenAtlasDesktop.exe", "Runtime/GreenAtlasRuntime.exe", "Worker/helper.dll", "unexpected.arx"])
def test_sdk_contract_cannot_supply_external_modules_to_other_payload_binaries(
    package: tuple[Path, Path], sdk_root: Path, binary: str,
) -> None:
    root = package[0] / "Contents/Windows" / binary
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_bytes(_pe(("acdb26.dll",)))
    with pytest.raises(ValueError, match="acdb26.dll"):
        _inventory(package, sdk_root)


def test_unknown_external_module_still_fails(package: tuple[Path, Path], sdk_root: Path) -> None:
    binary = package[0] / "Contents/Windows/GreenAtlasBridge.arx"
    data = _pe(("unknown-autodesk.dll",))
    binary.write_bytes(data)
    evidence = package[0] / "Contents/Windows/BuildEvidence/gui.json"
    value = json.loads(evidence.read_bytes())
    value["binary_sha256"] = hashlib.sha256(data).hexdigest()
    evidence.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="unknown-autodesk.dll"):
        _inventory(package, sdk_root)


@pytest.mark.parametrize("change", ["library_sha", "library_missing", "binary_sha", "library_duplicate"])
def test_sdk_contract_binds_to_actual_native_evidence(
    package: tuple[Path, Path], sdk_root: Path, change: str,
) -> None:
    path = package[0] / "Contents/Windows/BuildEvidence/core.json"
    value = json.loads(path.read_bytes())
    if change == "library_sha":
        value["sdk_files"][0]["sha256"] = "f" * 64
    elif change == "library_missing":
        value["sdk_files"].pop()
    elif change == "library_duplicate":
        value["sdk_files"].append(value["sdk_files"][0])
    else:
        value["binary_sha256"] = "f" * 64
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="SDK|Native"):
        _inventory(package, sdk_root)


def test_sdk_contract_matches_windows_case_insensitive_library_paths(package: tuple[Path, Path], sdk_root: Path) -> None:
    path = package[0] / "Contents/Windows/BuildEvidence/core.json"
    value = json.loads(path.read_bytes())
    for row in value["sdk_files"]:
        row["path"] = row["path"].upper()
    path.write_text(json.dumps(value))
    assert _inventory(package, sdk_root)["resolution"] == "objectarx-sdk-import-contract"


def test_core_cannot_use_gui_only_acad_exe_contract(package: tuple[Path, Path], sdk_root: Path) -> None:
    binary = package[0] / "Contents/Windows/Worker/GreenAtlasQuery.crx"
    data = _pe(("acad.exe",))
    binary.write_bytes(data)
    path = package[0] / "Contents/Windows/BuildEvidence/core.json"
    value = json.loads(path.read_bytes())
    value["binary_sha256"] = hashlib.sha256(data).hexdigest()
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="import contract цели"):
        _inventory(package, sdk_root)


def test_cannot_bundle_autodesk_runtime_module(package: tuple[Path, Path], sdk_root: Path) -> None:
    (package[0] / "Contents/Windows/Worker/acbr26.dbx").write_bytes(_pe())
    with pytest.raises(ValueError, match="Autodesk|runtime module"):
        _inventory(package, sdk_root)


@pytest.mark.parametrize("arguments", [{}, {"objectarx_archive_sha256": "f" * 64},
                                    {"autocad": Path("cad"), "objectarx_sdk": Path("SDK"),
                                     "objectarx_archive_sha256": sdk.ARCHIVE_SHA256}])
def test_external_provider_requires_exactly_one_explicit_mode(
    package: tuple[Path, Path], arguments: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="Выберите"):
        dependencies.dependency_inventory(package[0], crt=package[1], **arguments)


def test_installed_root_resolves_dbx_and_dll_without_copying(package: tuple[Path, Path], tmp_path: Path) -> None:
    cad = tmp_path / "AutoCAD2027"
    cad.mkdir()
    for name in ("acbr26.dbx", "acgeomentobj.dbx", "accore.dll"):
        (cad / name).write_bytes(_pe())
    result = dependencies.dependency_inventory(package[0], crt=package[1], autocad=cad)
    assert result["resolution"] == "build-file-inventory"
    assert result["native_execution_qualified"] is False
    assert "objectarx_sdk" not in result
    for row in result["binaries"]:
        if row["binary"].endswith((".crx", ".arx")):
            assert {item["dll"] for item in row["imports"]} == {"acbr26.dbx", "acgeomentobj.dbx", "accore.dll"}
            assert all(item["provider"] == "autocad2027" and "sha256" in item for item in row["imports"])
    assert not list(package[0].rglob("*.dbx"))


def test_installed_root_does_not_supply_worker_helper_dll(package: tuple[Path, Path], tmp_path: Path) -> None:
    cad = tmp_path / "AutoCAD2027"
    cad.mkdir()
    for name in ("acbr26.dbx", "acgeomentobj.dbx", "accore.dll"):
        (cad / name).write_bytes(_pe())
    (package[0] / "Contents/Windows/Worker/helper.dll").write_bytes(_pe(("acbr26.dbx",)))
    with pytest.raises(ValueError, match="acbr26.dbx"):
        dependencies.dependency_inventory(package[0], crt=package[1], autocad=cad)
