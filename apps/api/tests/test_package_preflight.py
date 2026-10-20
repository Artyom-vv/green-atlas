"""Проверка состава поставки на малых fixtures без запуска API, CAD и Blender."""

import hashlib
import json
import os
import plistlib
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.distribution.contracts import PackageManifest, QualificationEvidence
from app.distribution.preflight import (
    MANIFEST_NAME,
    RUNTIME_RESOURCES,
    build_manifest,
    check_package,
    component_evidence,
    macos_layout,
    manifest_identity,
)


@pytest.fixture
def payload(tmp_path):
    package = tmp_path / "Поставка с пробелом"
    source = tmp_path / "source"
    package.mkdir()
    source.mkdir()
    (package / "PackageContents.xml").write_text(
        '<ApplicationPackage AppVersion="0.1.119" />'
    )
    desktop = package / "Contents/Applications/Green Atlas.app"
    gui = package / "Contents/MacOS/GreenAtlasBridge.bundle"
    worker = package / "Contents/Workers/GreenAtlasQuery.bundle"
    for directory, version, binary in (
        (desktop, "0.1.119", "GreenAtlasDesktop"),
        (gui, "0.1.48", "GreenAtlasBridge"),
        (worker, "0.1.49", "GreenAtlasBridge"),
    ):
        (directory / "Contents/MacOS").mkdir(parents=True)
        (directory / "Contents/MacOS" / binary).write_bytes(b"test-native-fixture")
        info = {"CFBundleShortVersionString": version}
        if directory == worker:
            info.update(
                CFBundleIdentifier="ru.green-atlas.native-query-worker",
                GAFaceProtocols=[
                    f"green-atlas.archive-face-preparation/{number}"
                    for number in (1, 2, 3)
                ],
                GATopologyShapes=["curve", "AcDbRegion", "AcDbHatch"],
            )
        (directory / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
    runtime = desktop / "Contents/Resources/Runtime"
    runtime.mkdir(parents=True)
    (runtime / "GreenAtlasRuntime").write_bytes(b"test-frozen-fixture")
    rows = []
    for relative in RUNTIME_RESOURCES:
        content = ("fixture:" + relative).encode()
        row = source / "apps/api/app" / relative
        target = runtime / "_internal/app" / relative
        for path in (row, target):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        rows.append(
            {
                "path": row.relative_to(source).as_posix(),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    receipt = tmp_path / "source-manifest.json"
    receipt.write_text(json.dumps({"files": rows}))
    web = desktop / "Contents/Resources/Web"
    web.mkdir()
    (web / "assets").mkdir()
    (web / "index.html").write_text(
        '<script type="module" src="/assets/app.js"></script>'
    )
    (web / "assets/app.js").write_text('const API_URL="";fetch(API_URL+"/api/health");')
    return package, source, receipt, runtime, web


def assemble(payload, **options):
    package, source, receipt, _, _ = payload
    return build_manifest(
        package,
        macos_layout(package),
        source_root=source,
        source_manifest=receipt,
        **options,
    )


def test_read_only_manifest_independent_versions_and_source(payload):
    package, _, _, _, _ = payload
    manifest = assemble(payload)
    assert {row.name: row.version for row in manifest.layout.components} == {
        "desktop": "0.1.119",
        "api": "0.1.119",
        "web": "0.1.119",
        "gui_producer": "0.1.48",
        "core_worker": "0.1.49",
    }
    before = {path: path.stat().st_mtime_ns for path in package.rglob("*")}
    report = check_package(package, manifest)
    assert report.payload_valid and report.web_document_resources_local
    assert report.known_developer_origins_absent
    assert report.qualification == "not_claimed" and report.external == ()
    assert before == {path: path.stat().st_mtime_ns for path in package.rglob("*")}
    restored = PackageManifest.model_validate_json(
        manifest.model_dump_json(by_alias=True)
    )
    assert check_package(package, restored) == report


@pytest.mark.parametrize(
    "failure", ["missing", "tampered", "extra", "source", "resource"]
)
def test_missing_tampered_unrecorded_and_source_mismatch(payload, failure):
    package, source, _, runtime, web = payload
    manifest = assemble(payload)
    if failure == "missing":
        (web / "assets/app.js").unlink()
    elif failure == "tampered":
        (runtime / "GreenAtlasRuntime").write_bytes(b"changed")
    elif failure == "extra":
        (package / "unexpected.exe").write_bytes(b"unexpected")
    elif failure == "source":
        (source / "apps/api/app" / RUNTIME_RESOURCES[0]).write_bytes(b"changed")
    else:
        (runtime / "_internal/app" / RUNTIME_RESOURCES[-1]).write_bytes(b"changed")
    with pytest.raises((ValueError, FileNotFoundError)):
        assemble(payload) if failure in {"source", "resource"} else check_package(
            package, manifest
        )


def test_missing_referenced_web_asset_rejected_before_manifest(payload):
    _, _, _, _, web = payload
    (web / "index.html").write_text('<script src="/assets/missing.js"></script>')
    with pytest.raises(FileNotFoundError):
        assemble(payload)


@pytest.mark.parametrize(
    "failure", ["api_origin", "cdn", "developer", "database", "cad"]
)
def test_end_user_payload_rejects_developer_and_working_data(payload, failure):
    package, _, _, _, web = payload
    if failure == "api_origin":
        (web / "assets/app.js").write_text('fetch("http://127.0.0.1:8000/api/health")')
    elif failure == "cdn":
        (web / "index.html").write_text(
            '<script src="https://cdn.test/app.js"></script>'
        )
    elif failure == "developer":
        (package / "node_modules").mkdir()
    else:
        (
            package / ("projects.sqlite3" if failure == "database" else "source.dwg")
        ).touch()
    with pytest.raises(ValueError):
        assemble(payload)


@pytest.mark.skipif(
    os.name == "nt", reason="Fixture требует доступное создание symlink"
)
def test_internal_relative_runtime_file_and_directory_links(payload):
    package, _, _, runtime, _ = payload
    framework = runtime / "_internal/Python.framework/Versions/3.14"
    framework.mkdir(parents=True)
    (framework / "Python").write_bytes(b"fixture-framework")
    (framework.parent / "Current").symlink_to("3.14", target_is_directory=True)
    (runtime / "_internal/Python").symlink_to(
        "Python.framework/Versions/Current/Python"
    )
    manifest = assemble(payload)
    links = [row for row in manifest.entries if row.kind == "symlink"]
    assert len(links) == 2
    assert check_package(package, manifest).payload_valid
    (runtime / "_internal/Python").unlink()
    (runtime / "_internal/Python").symlink_to("Python.framework/Versions/3.14/Python")
    with pytest.raises(ValueError, match="состав"):
        check_package(package, manifest)


@pytest.mark.parametrize("failure", ["outside", "absolute", "web", "broken"])
@pytest.mark.skipif(
    os.name == "nt", reason="Fixture требует доступное создание symlink"
)
def test_escaping_absolute_non_runtime_and_broken_links_rejected(
    payload, failure, tmp_path
):
    package, _, _, runtime, web = payload
    outside = tmp_path / "external.bin"
    outside.write_bytes(b"external")
    target = runtime / "escape" if failure != "web" else web / "link"
    if failure == "outside":
        target.symlink_to(os.path.relpath(outside, target.parent))
    elif failure == "absolute":
        target.symlink_to(outside)
    elif failure == "web":
        target.symlink_to("assets/app.js")
    else:
        target.symlink_to("missing")
    with pytest.raises(ValueError, match="[Сс]сылк"):
        assemble(payload)


def test_qualification_requires_exact_source_and_each_component_inventory(payload):
    manifest = assemble(payload)
    evidence = QualificationEvidence(
        platform="macos",
        source_manifest_sha256=manifest.source.manifest_sha256,
        payload_manifest_sha256=manifest_identity(manifest),
        components=component_evidence(manifest),
        checks=("synthetic_contract_fixture",),
    )
    # Только контракт fixture; это не квитанция выполненной продуктовой приёмки.
    qualified = assemble(payload, qualification=evidence)
    assert check_package(payload[0], qualified).qualification == "receipt_matches"
    for change in (
        {"source_manifest_sha256": "0" * 64},
        {"payload_manifest_sha256": "0" * 64},
        {"components": evidence.components[:-1]},
        {"platform": "windows"},
    ):
        with pytest.raises((ValueError, ValidationError)):
            assemble(
                payload,
                qualification=QualificationEvidence.model_validate(
                    {**evidence.model_dump(), **change}
                ),
            )
    with pytest.raises(ValidationError):
        PackageManifest.model_validate({**manifest.model_dump(), "status": "qualified"})


def test_cli_build_check_does_not_replace_manifest_or_run_app(payload, tmp_path):
    package, source, receipt, _, _ = payload
    repo = Path(__file__).resolve().parents[3]
    tool = repo / "scripts/desktop/package_preflight.py"
    output = package / MANIFEST_NAME
    command = [os.sys.executable, str(tool)]
    result = subprocess.run(
        [
            *command,
            "build",
            "--package",
            str(package),
            "--source-root",
            str(source),
            "--source-manifest",
            str(receipt),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["qualification"] == "not_claimed"
    stored = output.read_bytes()
    assert (
        subprocess.run(
            [*command, "check", "--package", str(package), "--manifest", str(output)],
            capture_output=True,
        ).returncode
        == 0
    )
    repeated = subprocess.run(
        [
            *command,
            "build",
            "--package",
            str(package),
            "--source-root",
            str(source),
            "--source-manifest",
            str(receipt),
            "--output",
            str(output),
        ],
        capture_output=True,
    )
    assert repeated.returncode == 1 and output.read_bytes() == stored


def test_external_unavailability_does_not_block_own_core(payload, monkeypatch):
    from app.native_query.transport import autocad_installation
    from app.rendering import blender_runtime

    def unavailable(*args, **kwargs):
        raise ValueError("Внешняя программа не установлена")

    def must_not_launch(*args, **kwargs):
        raise AssertionError("Read-only preflight не запускает внешнюю программу")

    monkeypatch.setattr(
        autocad_installation, "resolve_autocad_installation", unavailable
    )
    monkeypatch.setattr(blender_runtime, "discover_blender", unavailable)
    monkeypatch.setattr(subprocess, "Popen", must_not_launch)
    report = check_package(payload[0], assemble(payload), discover_external=True)
    assert report.payload_valid
    assert {row.name: row.status for row in report.external} == {
        "autocad": "unavailable",
        "blender": "unavailable",
        "botaniq": "user_supplied",
    }


def test_windows_worker_descriptor_does_not_qualify_mixed_platform_package(payload):
    from app.distribution.contracts import Component, PackageLayout
    from app.native_query.transport.worker_layout import resolve_worker_layout

    package, source, receipt, _, _ = payload
    mac = macos_layout(package)
    worker_root = "Native/AutoCAD2027/worker"
    directory = package / worker_root
    directory.mkdir(parents=True)
    binary = directory / "GreenAtlasQuery.crx"
    binary.write_bytes(b"test-windows-worker-not-executable")
    metadata = directory / "worker.json"
    metadata.write_text(
        json.dumps(
            {
                "schema": "green-atlas.query-worker/1",
                "target": "windows-x86_64",
                "binary": binary.name,
                "plugin_version": "0.1.49",
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "face_protocols": [
                    f"green-atlas.archive-face-preparation/{number}"
                    for number in (1, 2, 3)
                ],
                "topology_shapes": ["curve", "AcDbRegion", "AcDbHatch"],
            }
        )
    )
    replacement = Component(
        name="core_worker",
        root=worker_root,
        entrypoint=worker_root + "/GreenAtlasQuery.crx",
        metadata=worker_root + "/worker.json",
        version="0.1.49",
        version_source="metadata",
    )
    layout = PackageLayout(
        platform="windows",
        architecture="x64",
        version="0.1.119",
        components=tuple(
            replacement if row.name == "core_worker" else row for row in mac.components
        ),
    )
    descriptor = resolve_worker_layout(platform="windows", root=directory)
    assert descriptor.version == "0.1.49"
    assert len(descriptor.face_protocols) == 3
    with pytest.raises(ValueError, match="фиксированного"):
        build_manifest(package, layout, source_root=source, source_manifest=receipt)
    data = json.loads(metadata.read_text())
    data["sha256"] = "0" * 64
    metadata.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="SHA"):
        resolve_worker_layout(platform="windows", root=directory)


@pytest.mark.parametrize(
    "failure", ["package_xml", "development_profile", "worker_version"]
)
def test_metadata_failure_does_not_get_manifest(payload, failure):
    package = payload[0]
    if failure == "package_xml":
        (package / "PackageContents.xml").write_text("broken")
    elif failure == "development_profile":
        info = package / "Contents/Applications/Green Atlas.app/Contents/Info.plist"
        data = plistlib.loads(info.read_bytes())
        data["GADevelopmentProfile"] = "private-test-profile"
        info.write_bytes(plistlib.dumps(data))
    else:
        info = package / "Contents/Workers/GreenAtlasQuery.bundle/Contents/Info.plist"
        info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "0.1.50"}))
    with pytest.raises(ValueError):
        assemble(payload)


def test_web_document_check_does_not_claim_arbitrary_api_url_same_origin(payload):
    _, _, _, _, web = payload
    (web / "assets/app.js").write_text(
        'fetch("https://remote.example:4321/api/health")'
    )
    report = check_package(payload[0], assemble(payload))
    assert report.web_document_resources_local
    assert report.known_developer_origins_absent
    assert "same_origin_web" not in report.model_dump()


@pytest.mark.parametrize("failure", ["identity", "face_protocols", "topology_shapes"])
def test_current_core_worker_requires_its_identity_and_declared_capabilities(
    payload, failure
):
    info = payload[0] / "Contents/Workers/GreenAtlasQuery.bundle/Contents/Info.plist"
    data = plistlib.loads(info.read_bytes())
    if failure == "identity":
        data["CFBundleIdentifier"] = "ru.green-atlas.autocad-bridge"
    elif failure == "face_protocols":
        data["GAFaceProtocols"] = ["green-atlas.archive-face-preparation/2"]
    else:
        data["GATopologyShapes"] = ["curve"]
    info.write_bytes(plistlib.dumps(data))
    with pytest.raises(ValueError, match="Core worker"):
        assemble(payload)


def test_package_manifest_is_written_after_nested_resign_before_outer_sign():
    repo = Path(__file__).resolve().parents[3]
    script = (repo / "tools/autocad-bridge/package-macos.sh").read_text()
    copied = script.index('ditto "$bridge_package"')
    resigned = script.index(
        '"$installer_app/Contents/Resources/GreenAtlasBridge.bundle/Contents/MacOS/GreenAtlasBridge.bundle"'
    )
    manifest_built = script.index(
        '"$repo_root/scripts/desktop/package_preflight.py" build'
    )
    manifest_checked = script.index(
        '"$repo_root/scripts/desktop/package_preflight.py" check'
    )
    outer_signed = script.index(
        'codesign --force --options runtime --sign "$signing_identity" "$installer_app"'
    )
    assert copied < resigned < manifest_built < manifest_checked < outer_signed
    assert '--package "$final_payload"' in script
