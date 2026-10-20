"""Реальная файловая политика установки на малых payload, без запуска Windows/CAD."""

import hashlib
import json
import os
import shutil
import struct
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.distribution.preflight import (
    RUNTIME_RESOURCES,
    build_manifest,
    check_package,
    manifest_identity,
)
from app.distribution.windows_dependencies import dependency_inventory, pe_imports
from app.distribution.windows_install import BundleInstaller, load_owned_bundle
from app.distribution.windows_layout import WINDOWS_ROOT, windows_layout
from app.distribution.windows_prerequisites import (
    supported_runtime_version,
    valid_runtime_version,
)

Payload = tuple[Path, Path, Path]


def pe_bytes(imports: tuple[str, ...] = (), marker: str = "") -> bytes:
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
    section = optional + 240
    struct.pack_into("<IIII", data, section + 8, 2048, 4096, 2048, 1024)
    if imports:
        struct.pack_into("<II", data, optional + 120, 4096, 20 * (len(imports) + 1))
        name_offset = 1024 + 20 * (len(imports) + 1)
        for index, name in enumerate(imports):
            struct.pack_into("<I", data, 1024 + 20 * index + 12, 4096 + name_offset - 1024)
            text = name.encode() + b"\0"
            data[name_offset:name_offset + len(text)] = text
            name_offset += len(text)
    return bytes(data) + marker.encode()


@pytest.fixture
def factory(tmp_path: Path) -> Callable[[str], Payload]:
    def create(marker: str) -> Payload:
        package = tmp_path / ("Поставка " + marker)
        source = tmp_path / ("Исходники " + marker)
        package.mkdir()
        source.mkdir()
        (package / "PackageContents.xml").write_bytes((Path(__file__).resolve().parents[3] / "tools/autocad-bridge/windows/PackageContents.xml").read_bytes())
        windows = package / WINDOWS_ROOT
        for directory in (windows, windows / "Runtime", windows / "Worker", windows / "Web/assets"):
            directory.mkdir(parents=True, exist_ok=True)
        for name in ("GreenAtlasDesktop.exe", "GreenAtlasBridge.arx", "Runtime/GreenAtlasRuntime.exe", "Worker/GreenAtlasQuery.crx", "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "Worker/msvcp140.dll", "Worker/vcruntime140.dll", "Worker/vcruntime140_1.dll"):
            (windows / name).write_bytes(pe_bytes(marker=marker + name))
        (windows / "green-atlas-launch.json").write_text(json.dumps({"schema": "green-atlas.desktop-launch/1", "kind": "windows_webview2", "host": "GreenAtlasDesktop.exe", "runtime": "Runtime/GreenAtlasRuntime.exe", "web": "Web", "worker": "Worker"}))
        gui = windows / "GreenAtlasBridge.arx"
        (windows / "producer.json").write_text(json.dumps({"schema": "green-atlas.gui-producer/1", "target": "windows-x86_64", "binary": gui.name, "plugin_version": "0.1.48", "sha256": hashlib.sha256(gui.read_bytes()).hexdigest()}))
        worker = windows / "Worker/GreenAtlasQuery.crx"
        (windows / "Worker/worker.json").write_text(json.dumps({"schema": "green-atlas.query-worker/1", "target": "windows-x86_64", "binary": worker.name, "plugin_version": "0.1.49", "sha256": hashlib.sha256(worker.read_bytes()).hexdigest(), "face_protocols": [f"green-atlas.archive-face-preparation/{n}" for n in (1, 2, 3)], "topology_shapes": ["curve", "AcDbRegion", "AcDbHatch"]}))
        (windows / "Web/index.html").write_text('<script src="/assets/app.js"></script>')
        (windows / "Web/assets/app.js").write_text('fetch("/api/health")')
        prereqs = windows / "Prerequisites"
        prereqs.mkdir()
        offline = prereqs / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
        offline.write_bytes(b"synthetic-offline-prerequisite-not-executed")
        (prereqs / "webview2-installer.json").write_text(json.dumps({"schema": "green-atlas.offline-webview2/1", "file": offline.name, "sha256": hashlib.sha256(offline.read_bytes()).hexdigest(), "publisher": "Microsoft Corporation", "authenticode": "Valid"}))
        rows = []
        for relative in RUNTIME_RESOURCES:
            content = ("fixture:" + relative).encode()
            for root in (source / "apps/api/app", windows / "Runtime/_internal/app"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            rows.append({"path": "apps/api/app/" + relative, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)})
        seal = tmp_path / (marker + "-source.json")
        seal.write_text(json.dumps({"files": rows}))
        manifest = build_manifest(package, windows_layout(package), source_root=source, source_manifest=seal)
        (package / "distribution-manifest.json").write_text(manifest.model_dump_json(by_alias=True))
        return package, source, seal

    return create


def test_fixed_windows_payload_keeps_versions_and_narrow_claim(factory: Callable[[str], Payload]) -> None:
    package, _, _ = factory("first")
    manifest = load_owned_bundle(package)
    assert {row.name: row.version for row in manifest.layout.components} == {"desktop": "0.1.119", "api": "0.1.119", "web": "0.1.119", "gui_producer": "0.1.48", "core_worker": "0.1.49"}
    report = check_package(package, manifest)
    assert report.qualification == "not_claimed"
    assert report.payload_valid


@pytest.mark.parametrize("changes", [{"runtime": "C:/dev/python.exe"}, {"profile": "C:/Users/dev"}, {"kind": "python_dev"}])
def test_launch_cannot_inject_developer_paths(factory: Callable[[str], Payload], changes: dict[str, str]) -> None:
    package, source, seal = factory("launch")
    path = package / WINDOWS_ROOT / "green-atlas-launch.json"
    value = json.loads(path.read_text())
    value.update(changes)
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        build_manifest(package, windows_layout(package), source_root=source, source_manifest=seal)


def test_launch_requires_all_six_keys(factory: Callable[[str], Payload]) -> None:
    package, source, seal = factory("missing")
    (package / WINDOWS_ROOT / "green-atlas-launch.json").write_text('{}')
    with pytest.raises(ValueError):
        build_manifest(package, windows_layout(package), source_root=source, source_manifest=seal)


def test_install_update_uninstall_preserve_workspace_without_old_builds(factory: Callable[[str], Payload], tmp_path: Path) -> None:
    first, _, _ = factory("first")
    second, _, _ = factory("second")
    workspace = tmp_path / "Локальный профиль/GreenAtlas/Workspace"
    workspace.mkdir(parents=True)
    project = workspace / "projects.sqlite3"
    project.write_bytes(b"existing-user-project-do-not-open")
    engine = BundleInstaller(tmp_path / "Roaming/Autodesk/ApplicationPlugins")
    guard_calls = []
    engine.install(first, before_publish=lambda: guard_calls.append("first"))
    update = engine.install(second, before_publish=lambda: guard_calls.append("second"))
    assert update.backup is None and not update.cleanup_pending
    assert len(update.removed_program_copies) == 1
    assert not list(engine.plugins.glob("GreenAtlas.backup-*"))
    removed = engine.uninstall()
    assert not engine.installed.exists()
    assert removed.backup is not None and Path(removed.backup).is_dir()
    assert project.read_bytes() == b"existing-user-project-do-not-open"
    assert guard_calls == ["first", "second"]
    purged = engine.purge_retained()
    assert len(purged) == 1
    assert project.read_bytes() == b"existing-user-project-do-not-open"


def test_shell_failure_restores_previous_bundle(factory: Callable[[str], Payload], tmp_path: Path) -> None:
    first, _, _ = factory("first")
    second, _, _ = factory("second")
    engine = BundleInstaller(tmp_path / "plugins")
    expected = engine.install(first).package_sha256

    def fail(_: Path, version: str) -> None:
        assert version == "0.1.119"
        raise OSError("synthetic shell failure")

    with pytest.raises(OSError, match="synthetic"):
        engine.install(second, publish_shell=fail)
    assert manifest_identity(load_owned_bundle(engine.installed)) == expected
    assert not list(engine.plugins.glob("GreenAtlas.failed-*"))


def test_incomplete_previous_install_is_preserved_and_replaced(factory: Callable[[str], Payload], tmp_path: Path) -> None:
    package, _, _ = factory("repair")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.installed.mkdir(parents=True)
    (engine.installed / "PackageContents.xml").write_bytes((package / "PackageContents.xml").read_bytes())
    binary = engine.installed / "Contents/Windows/GreenAtlasBridge.arx"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"old-arx")
    (engine.installed / "user-note.txt").write_text("keep me")

    receipt = engine.install(package)

    assert receipt.backup is not None
    assert Path(receipt.backup).name.startswith("GreenAtlas.incomplete-")
    assert (Path(receipt.backup) / "user-note.txt").read_text() == "keep me"
    assert binary.read_bytes() != b"old-arx"
    load_owned_bundle(engine.installed)


def test_incomplete_previous_install_is_restored_on_shell_failure(factory: Callable[[str], Payload], tmp_path: Path) -> None:
    package, _, _ = factory("repair-failure")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.installed.mkdir(parents=True)
    (engine.installed / "PackageContents.xml").write_bytes((package / "PackageContents.xml").read_bytes())
    binary = engine.installed / "Contents/Windows/GreenAtlasBridge.arx"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"old-arx")

    with pytest.raises(OSError, match="shell failure"):
        engine.install(package, publish_shell=lambda *_: (_ for _ in ()).throw(OSError("shell failure")))

    assert binary.read_bytes() == b"old-arx"
    assert not list(engine.plugins.glob("GreenAtlas.incomplete-*"))


def test_unrecognised_bundle_without_manifest_is_left_untouched(factory: Callable[[str], Payload], tmp_path: Path) -> None:
    package, _, _ = factory("foreign")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.installed.mkdir(parents=True)
    note = engine.installed / "user.dwg"
    note.write_bytes(b"drawing")

    with pytest.raises(ValueError, match="distribution-manifest.json"):
        engine.install(package)
    assert note.read_bytes() == b"drawing"


def test_guard_refusal_happens_after_staging_before_any_rename(factory: Callable[[str], Payload], tmp_path: Path) -> None:
    first, _, _ = factory("first")
    second, _, _ = factory("second")
    engine = BundleInstaller(tmp_path / "plugins")
    expected = engine.install(first).package_sha256

    def running() -> None:
        assert manifest_identity(load_owned_bundle(engine.installed)) == expected
        stage = list(engine.plugins.glob("GreenAtlas.staging-*"))
        assert len(stage) == 1
        load_owned_bundle(stage[0])
        raise RuntimeError("application is running")

    with pytest.raises(RuntimeError, match="running"):
        engine.install(second, before_publish=running)
    assert manifest_identity(load_owned_bundle(engine.installed)) == expected
    assert not list(engine.plugins.glob("GreenAtlas.backup-*"))


def test_existing_unowned_data_blocks_update_and_survives(factory: Callable[[str], Payload], tmp_path: Path) -> None:
    first, _, _ = factory("first")
    second, _, _ = factory("second")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)
    unknown = engine.installed / "user.dwg"
    unknown.write_bytes(b"user CAD")
    with pytest.raises(ValueError):
        engine.install(second)
    assert unknown.read_bytes() == b"user CAD"
    with pytest.raises(ValueError):
        engine.uninstall()
    assert unknown.read_bytes() == b"user CAD"


def test_destination_symlink_is_rejected_without_touching_target(factory: Callable[[str], Payload], tmp_path: Path) -> None:
    first, _, _ = factory("first")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    sentinel = foreign / "keep.txt"
    sentinel.write_text("keep")
    link = tmp_path / "plugins"
    if os.name == "nt":
        from _winapi import CreateJunction

        CreateJunction(str(foreign), str(link))
    else:
        link.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="ссылку"):
        BundleInstaller(link).install(first)
    assert list(foreign.iterdir()) == [sentinel]


@pytest.mark.parametrize("version,ready", [(None, False), ("", False), ("0.0.0.0", False), ("151.0.4191.53", False), ("152.0.4191.52", False), ("152.0.4191.53", True), ("153.0.1.0", True), ("152.0.4191.53 beta", False)])
def test_webview_minimum_matches_pinned_sdk(version: object, ready: bool) -> None:
    assert supported_runtime_version(version) is ready
    assert valid_runtime_version("0.0.0.0") is None


def test_pe_imports_are_read_without_loading_and_corruption_rejected(tmp_path: Path) -> None:
    binary = tmp_path / "sample.dll"
    binary.write_bytes(pe_bytes(("KERNEL32.dll", "msvcp140.dll")))
    assert pe_imports(binary) == ("kernel32.dll", "msvcp140.dll")
    data = bytearray(binary.read_bytes())
    struct.pack_into("<I", data, 1024 + 12, 0x7fffffff)
    binary.write_bytes(data)
    with pytest.raises(ValueError, match="RVA"):
        pe_imports(binary)


def test_worker_crt_is_local_even_when_same_dll_exists_at_host_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.distribution.windows_dependencies as dependencies

    windows = tmp_path / "package/Contents/Windows"
    (windows / "Worker").mkdir(parents=True)
    crt, cad, system = (tmp_path / name for name in ("crt", "cad", "system"))
    for path in (crt, cad, system / "System32"):
        path.mkdir(parents=True)
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140_2.dll"):
        (crt / name).write_bytes(pe_bytes())
    (windows / "msvcp140_2.dll").write_bytes(pe_bytes())
    (windows / "Worker/GreenAtlasQuery.crx").write_bytes(pe_bytes(("msvcp140_2.dll",)))
    monkeypatch.setattr(dependencies, "os", SimpleNamespace(name="nt", environ={"SystemRoot": str(system)}))
    result = dependency_inventory(tmp_path / "package", crt=crt, autocad=cad)
    assert (windows / "Worker/msvcp140_2.dll").is_file()
    assert "Contents/Windows/Worker/msvcp140_2.dll" in result["copied_crt"]


def test_standalone_host_cannot_claim_autocad_dll_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.distribution.windows_dependencies as dependencies

    windows = tmp_path / "package/Contents/Windows"
    (windows / "Worker").mkdir(parents=True)
    crt, cad, system = (tmp_path / name for name in ("crt", "cad", "system"))
    for path in (crt, cad, system / "System32"):
        path.mkdir(parents=True)
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        (crt / name).write_bytes(pe_bytes())
    (cad / "acdb26.dll").write_bytes(pe_bytes())
    (windows / "GreenAtlasDesktop.exe").write_bytes(pe_bytes(("acdb26.dll",)))
    monkeypatch.setattr(dependencies, "os", SimpleNamespace(name="nt", environ={"SystemRoot": str(system)}))
    with pytest.raises(ValueError, match="acdb26.dll"):
        dependency_inventory(tmp_path / "package", crt=crt, autocad=cad)


def test_numpy_hook_directory_is_scoped_and_reported_as_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.distribution.windows_dependencies as dependencies

    windows = tmp_path / "package/Contents/Windows"
    internal = windows / "Runtime/_internal"
    (internal / "numpy/_core").mkdir(parents=True)
    (internal / "numpy.libs").mkdir()
    (windows / "Worker").mkdir()
    crt, cad, system = (tmp_path / name for name in ("crt", "cad", "system"))
    for path in (crt, cad, system / "System32"):
        path.mkdir(parents=True)
    for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        (crt / name).write_bytes(pe_bytes())
    (internal / "numpy/_core/math.pyd").write_bytes(pe_bytes(("openblas-example.dll",)))
    (internal / "numpy.libs/openblas-example.dll").write_bytes(pe_bytes())
    monkeypatch.setattr(dependencies, "os", SimpleNamespace(name="nt", environ={"SystemRoot": str(system)}))
    result = dependency_inventory(tmp_path / "package", crt=crt, autocad=cad)
    row = next(row for row in result["binaries"] if row["binary"].endswith("math.pyd"))
    assert row["imports"] == [{"dll": "openblas-example.dll", "provider": "payload", "path": "Contents/Windows/Runtime/_internal/numpy.libs/openblas-example.dll", "loader": "pyinstaller-wheel-hook-candidate"}]
    assert result["native_execution_qualified"] is False
    (windows / "GreenAtlasDesktop.exe").write_bytes(pe_bytes(("openblas-example.dll",)))
    with pytest.raises(ValueError, match="openblas-example"):
        dependency_inventory(tmp_path / "package", crt=crt, autocad=cad)


@pytest.mark.parametrize("old,new", [("Win64", "Mac"), ("R26.0", "R25.1"), ("./Contents/Windows/GreenAtlasBridge.arx", "./Contents/MacOS/GreenAtlasBridge.bundle"), ("Global=\"GAOPEN\"", "Global=\"OTHER\""), ("AppVersion=\"0.1.48\"", "AppVersion=\"0.1.49\"")])
def test_manifest_rejects_registration_for_wrong_platform_module_command_or_version(factory, old, new):
    package, source, seal = factory("xml")
    path = package / "PackageContents.xml"
    content = path.read_text()
    assert old in content
    path.write_text(content.replace(old, new))
    with pytest.raises(ValueError):
        build_manifest(package, windows_layout(package), source_root=source, source_manifest=seal)


@pytest.mark.parametrize("name", ["msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"])
def test_worker_requires_own_crt_even_when_root_crt_is_present(factory, name):
    package, source, seal = factory("worker-crt")
    (package / WINDOWS_ROOT / "Worker" / name).unlink()
    assert (package / WINDOWS_ROOT / name).is_file()
    with pytest.raises((ValueError, OSError)):
        build_manifest(package, windows_layout(package), source_root=source, source_manifest=seal)


def test_rollback_preserves_dangling_foreign_installed_link(factory, tmp_path):
    first, _, _ = factory("link-rollback-first")
    second, _, _ = factory("link-rollback-second")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)
    engine.install(second)
    backup = engine.plugins / ("GreenAtlas.backup-" + "a" * 32)
    shutil.copytree(first, backup)
    engine.uninstall()
    foreign = tmp_path / "foreign-missing"
    try:
        engine.installed.symlink_to(foreign, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows account cannot create dangling symlinks")
        raise
    original_target = engine.installed.readlink()
    with pytest.raises(ValueError, match="ссылку"):
        engine.rollback(backup)
    assert engine.installed.is_symlink()
    assert engine.installed.readlink() == original_target
    assert backup.is_dir()


@pytest.mark.parametrize("mode", ["missing", "changed", "not-object"])
def test_package_requires_intact_offline_prerequisite_before_install(factory, mode):
    package, source, seal = factory("offline")
    prereqs = package / WINDOWS_ROOT / "Prerequisites"
    if mode == "missing":
        (prereqs / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe").unlink()
    elif mode == "changed":
        (prereqs / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe").write_bytes(b"wrong")
    else:
        (prereqs / "webview2-installer.json").write_text('[]')
    with pytest.raises((ValueError, OSError)):
        build_manifest(package, windows_layout(package), source_root=source, source_manifest=seal)
