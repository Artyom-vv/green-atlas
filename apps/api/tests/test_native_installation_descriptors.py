"""Дескрипторы установки не запускают CAD и не подменяют квалификацию платформы."""

import json
import os
import plistlib
import subprocess
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.native_query.transport import autocad_installation as installation
from app.native_query.transport import worker_config as config_module
from app.native_query.transport.worker_layout import resolve_worker_layout


def mac_install(root: Path, language: str = "en-us") -> tuple[Path, Path]:
    (root / "Contents").mkdir(parents=True)
    (root / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": "com.autodesk.AutoCAD2027"})
    )
    core = root / "Contents/Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole"
    core.parent.mkdir(parents=True)
    core.write_bytes(b"fake-core")
    core.chmod(0o700)
    template = (
        root / f"Contents/Resources/UserDataCache/{language}/Template/acadiso.dwt"
    )
    template.parent.mkdir(parents=True)
    template.write_bytes(b"fake-template")
    return core, template


def mac_worker(root: Path) -> Path:
    binary = root / "Contents/MacOS/GreenAtlasBridge"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake-worker")
    (root / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "ru.green-atlas.native-query-worker",
                "CFBundleShortVersionString": "0.1.49",
                "GAFaceProtocols": ["green-atlas.archive-face-preparation/3"],
                "GATopologyShapes": ["curve", "AcDbRegion"],
            }
        )
    )
    return binary


@pytest.mark.parametrize("language", ["en-us", "ru-ru"])
def test_mac_descriptor_preserves_paths_and_is_read_only(
    tmp_path, language, monkeypatch
):
    root = tmp_path / "AutoCAD 2027.app"
    core, template = mac_install(root, language)
    launch = Mock(side_effect=AssertionError("Нельзя запускать Core при обнаружении"))
    monkeypatch.setattr(installation.subprocess, "run", launch)
    descriptor = installation.resolve_autocad_installation(platform="macos", root=root)
    assert descriptor.core_executable == core
    assert descriptor.bootstrap_template == template
    assert descriptor.identity == "com.autodesk.AutoCAD2027"
    assert descriptor.platform == "macos"
    launch.assert_not_called()
    assert core.read_bytes() == b"fake-core"
    assert template.read_bytes() == b"fake-template"


def test_mac_prefers_en_us_template(tmp_path):
    root = tmp_path / "AutoCAD 2027.app"
    _, template = mac_install(root, "ru-ru")
    preferred = root / "Contents/Resources/UserDataCache/en-us/Template/acadiso.dwt"
    preferred.parent.mkdir(parents=True)
    preferred.write_bytes(b"preferred")
    assert (
        installation.resolve_autocad_installation(
            platform="macos", root=root
        ).bootstrap_template
        == preferred
    )
    assert template.exists()


@pytest.mark.parametrize("damage", ["identity", "core", "permission", "template"])
def test_mac_rejects_invalid_explicit_installation_without_discovery(
    tmp_path, damage, monkeypatch
):
    root = tmp_path / "AutoCAD 2027.app"
    core, template = mac_install(root)
    if damage == "identity":
        (root / "Contents/Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": "com.autodesk.AutoCAD2026"})
        )
    elif damage == "core":
        core.unlink()
    elif damage == "permission":
        core.chmod(0o600)
        if os.name == "nt":
            # NTFS chmod не моделирует POSIX execute bit; отказ доступа задаём
            # на границе Mac-адаптера, сохраняя проверку явного override.
            access = installation.os.access
            monkeypatch.setattr(
                installation.os,
                "access",
                lambda path, mode: path != core and access(path, mode),
            )
    else:
        template.unlink()
    monkeypatch.setenv("GREEN_ATLAS_AUTOCAD_ROOT", str(root))
    search = Mock(side_effect=AssertionError("Override нельзя заменить discovery"))
    monkeypatch.setattr(installation.subprocess, "run", search)
    with pytest.raises(ValueError, match="Не найден совместимый AutoCAD 2027"):
        installation.resolve_autocad_installation(platform="macos")
    search.assert_not_called()


def test_mac_spotlight_keeps_bounded_identifier_search(tmp_path, monkeypatch):
    monkeypatch.setattr(installation.sys, "platform", "darwin")
    root = tmp_path / "Иная папка/AutoCAD 2027.app"
    mac_install(root)
    monkeypatch.delenv("GREEN_ATLAS_AUTOCAD_ROOT", raising=False)
    original = installation._mac_installation
    monkeypatch.setattr(
        installation,
        "_mac_installation",
        lambda candidate: original(candidate) if candidate == root else None,
    )
    search = Mock(return_value=SimpleNamespace(returncode=0, stdout=str(root) + "\n"))
    monkeypatch.setattr(installation.subprocess, "run", search)
    descriptor = installation.resolve_autocad_installation(platform="macos")
    assert descriptor.root == root
    search.assert_called_once_with(
        ["/usr/bin/mdfind", "kMDItemCFBundleIdentifier == 'com.autodesk.AutoCAD2027'"],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )


def test_mac_spotlight_timeout_is_missing_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(installation.sys, "platform", "darwin")
    monkeypatch.delenv("GREEN_ATLAS_AUTOCAD_ROOT", raising=False)
    monkeypatch.setattr(installation, "_mac_installation", lambda _: None)
    monkeypatch.setattr(
        installation.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired("mdfind", 3)),
    )
    with pytest.raises(ValueError, match="Не найден совместимый AutoCAD 2027"):
        installation.resolve_autocad_installation(platform="macos")


def test_mac_config_preserves_all_runner_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "desktop_platform", lambda: "macos")
    root = tmp_path / "AutoCAD 2027.app"
    core, template = mac_install(root, "ru-ru")
    worker = tmp_path / "query-worker"
    binary = mac_worker(worker)
    monkeypatch.setenv("GREEN_ATLAS_AUTOCAD_ROOT", str(root))
    monkeypatch.setenv("GREEN_ATLAS_CAD_RELEASE_WORKER", str(worker))
    descriptor = resolve_worker_layout(platform="macos")
    assert descriptor.identity == "ru.green-atlas.native-query-worker"
    config = config_module.worker_config(
        str(tmp_path / "projects.sqlite3"), job_suffix=".cad-query-jobs"
    )
    assert config.core_executable == core
    assert config.bootstrap_template == template
    assert config.worker_bundle == worker
    assert config.job_root == tmp_path / "projects.sqlite3.cad-query-jobs"
    assert config.worker_binary_sha256 == sha256(binary.read_bytes()).hexdigest()
    assert config.plugin_version == "0.1.49"
    assert config.architecture == "native"
    assert config.timeout_seconds == 600
    assert config.face_protocols == ("green-atlas.archive-face-preparation/3",)
    assert config.topology_shapes == ("curve", "AcDbRegion")
    assert not config.job_root.exists()


def windows_install(tmp_path, monkeypatch):
    root = tmp_path / "CAD папка"
    root.mkdir()
    (root / "acad.exe").write_bytes(b"gui")
    (root / "accoreconsole.exe").write_bytes(b"core")
    local = tmp_path / "User Profile"
    template = local / "Autodesk/AutoCAD 2027/R26.0/enu/Template/acadiso.dwt"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"template")
    monkeypatch.delenv("GREEN_ATLAS_AUTOCAD_ROOT", raising=False)
    monkeypatch.delenv("GREEN_ATLAS_AUTOCAD_TEMPLATE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(
        installation,
        "_registered_windows_installations",
        lambda: {root: "Autodesk.AutoCAD:R26.0/ACAD"},
    )
    return root, template


def test_windows_registry_adapter_reads_only_r26_64bit(tmp_path, monkeypatch):
    root = tmp_path / "Autodesk на другом диске"
    opened = []

    class Key:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    def open_key(parent, name, *access):
        opened.append((name, access))
        return Key(name)

    def query(key, name):
        assert name == "AcadLocation"
        if key.name == "missing":
            raise FileNotFoundError()
        return str(root), 1

    registry = SimpleNamespace(
        HKEY_LOCAL_MACHINE="HKLM",
        KEY_READ=1,
        KEY_WOW64_64KEY=2,
        OpenKey=open_key,
        QueryInfoKey=lambda key: (2, 0, 0),
        EnumKey=lambda key, index: ["ACAD-2027", "missing"][index],
        QueryValueEx=query,
    )
    monkeypatch.setitem(installation.sys.modules, "winreg", registry)
    monkeypatch.setattr(installation.sys, "platform", "win32")
    assert installation._registered_windows_installations() == {
        root: "Autodesk.AutoCAD:R26.0/ACAD-2027"
    }
    assert opened[0] == (r"SOFTWARE\Autodesk\AutoCAD\R26.0", (0, 3))


def test_windows_template_override_does_not_fall_back(tmp_path, monkeypatch):
    root, _template = windows_install(tmp_path, monkeypatch)
    monkeypatch.setenv("GREEN_ATLAS_AUTOCAD_TEMPLATE", str(tmp_path / "missing.dwt"))
    with pytest.raises(ValueError, match="GREEN_ATLAS_AUTOCAD_TEMPLATE"):
        installation.resolve_autocad_installation(platform="windows", root=root)


def test_windows_template_ambiguity_is_explicit(tmp_path, monkeypatch):
    root, template = windows_install(tmp_path, monkeypatch)
    second = template.parents[2] / "rus/Template/acadiso.dwt"
    second.parent.mkdir(parents=True)
    second.write_bytes(b"other template")
    with pytest.raises(ValueError, match="шаблон AutoCAD"):
        installation.resolve_autocad_installation(platform="windows", root=root)


def test_windows_descriptor_requires_registered_r26_and_template(tmp_path, monkeypatch):
    root, template = windows_install(tmp_path, monkeypatch)
    descriptor = installation.resolve_autocad_installation(
        platform="windows", root=root
    )
    assert descriptor.platform == "windows"
    assert descriptor.core_executable == root / "accoreconsole.exe"
    assert descriptor.bootstrap_template == template
    assert descriptor.identity == "Autodesk.AutoCAD:R26.0/ACAD"
    assert set(root.iterdir()) == {root / "acad.exe", root / "accoreconsole.exe"}


@pytest.mark.parametrize(
    "failure", ["unregistered", "core", "template", "override", "ambiguous"]
)
def test_windows_descriptor_rejects_incomplete_or_ambiguous_installation(
    tmp_path, monkeypatch, failure
):
    root, template = windows_install(tmp_path, monkeypatch)
    if failure == "unregistered":
        monkeypatch.setattr(
            installation, "_registered_windows_installations", lambda: {}
        )
    elif failure == "core":
        (root / "accoreconsole.exe").unlink()
    elif failure == "template":
        template.unlink()
    elif failure == "override":
        monkeypatch.setenv("GREEN_ATLAS_AUTOCAD_ROOT", str(tmp_path / "not-installed"))
    else:
        second = tmp_path / "Other AutoCAD"
        second.mkdir()
        for name in ("acad.exe", "accoreconsole.exe"):
            (second / name).write_bytes(b"fake")
        monkeypatch.setattr(
            installation,
            "_registered_windows_installations",
            lambda: {root: "R26/A", second: "R26/B"},
        )
    with pytest.raises(ValueError):
        installation.resolve_autocad_installation(platform="windows")


def windows_worker(root):
    root.mkdir()
    binary = root / "GreenAtlasQuery.crx"
    binary.write_bytes(b"fake-crx")
    info = {
        "schema": "green-atlas.query-worker/1",
        "target": "windows-x86_64",
        "binary": "GreenAtlasQuery.crx",
        "plugin_version": "0.1.49",
        "sha256": sha256(binary.read_bytes()).hexdigest(),
    }
    (root / "worker.json").write_text(json.dumps(info))
    return info


def test_windows_worker_metadata_with_no_capabilities_is_not_upgraded(tmp_path):
    root = tmp_path / "Работник"
    windows_worker(root)
    descriptor = resolve_worker_layout(platform="windows", root=root)
    assert descriptor.version == "0.1.49"
    assert descriptor.face_protocols == ()
    assert descriptor.topology_shapes == ()
    assert descriptor.binary == root / "GreenAtlasQuery.crx"


@pytest.mark.parametrize(
    "damage", ["hash", "target", "binary", "capabilities", "oversize", "symlink"]
)
def test_windows_worker_rejects_corruption(tmp_path, damage):
    root = tmp_path / "Работник"
    info = windows_worker(root)
    metadata = root / "worker.json"
    if damage == "hash":
        (root / "GreenAtlasQuery.crx").write_bytes(b"changed")
    elif damage == "target":
        info["target"] = "macos-arm64"
    elif damage == "binary":
        info["binary"] = "../other.crx"
    elif damage == "capabilities":
        info["face_protocols"] = ["protocol", "protocol"]
    elif damage == "oversize":
        metadata.write_bytes(b" " * 16385)
    else:
        metadata.unlink()
        outside = tmp_path / "outside.json"
        outside.write_text(json.dumps(info))
        metadata.symlink_to(outside)
    if damage not in {"oversize", "symlink"}:
        metadata.write_text(json.dumps(info))
    with pytest.raises(ValueError):
        resolve_worker_layout(platform="windows", root=root)


def test_windows_config_uses_registered_core_and_curated_crx(tmp_path, monkeypatch):
    root, template = windows_install(tmp_path, monkeypatch)
    worker = tmp_path / "Работник Core"
    info = windows_worker(worker)
    monkeypatch.setenv("GREEN_ATLAS_CAD_RELEASE_WORKER", str(worker))
    monkeypatch.setattr(config_module, "desktop_platform", lambda: "windows")
    forbidden = Mock(side_effect=AssertionError("Нельзя искать Mac-установку"))
    monkeypatch.setattr(config_module, "_autocad_installation", forbidden)
    config = config_module.worker_config(str(tmp_path / "projects.sqlite3"))
    assert config.core_executable == root / "accoreconsole.exe"
    assert config.bootstrap_template == template
    assert config.worker_bundle == worker
    assert config.worker_binary_sha256 == info["sha256"]
    assert config.plugin_version == "0.1.49"
    assert not config.job_root.exists()
    forbidden.assert_not_called()


def test_windows_worker_rejects_duplicate_identity_fields(tmp_path):
    root = tmp_path / "Работник"
    info = windows_worker(root)
    raw = json.dumps(info)
    (root / "worker.json").write_text(raw[:-1] + ', "target": "windows-x86_64"}')
    with pytest.raises(ValueError, match="повторяющееся поле"):
        resolve_worker_layout(platform="windows", root=root)
