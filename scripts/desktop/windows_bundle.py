"""Сборка Windows payload с исходным seal, независимыми версиями и реестром DLL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps/api"))


def product_version(repository: Path) -> str:
    """Версия приложения общая; версии GUI/Core остаются независимыми."""
    bridge = repository / "tools/autocad-bridge"
    version = ET.parse(bridge / "PackageContents.xml").getroot().get("AppVersion")
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
        or any(len(value) > 5 or int(value) > 65535 for value in version.split("."))
    ):
        raise ValueError("Версия продукта должна содержать три числа от 0 до 65535")
    with (bridge / "desktop/Info.plist").open("rb") as stream:
        desktop = plistlib.load(stream)
    if desktop.get("CFBundleShortVersionString") != version:
        raise ValueError("Версии продукта и desktop расходятся")
    return version


def write_registration(template: Path, target: Path, version: str) -> None:
    registration = ET.parse(template)
    registration.getroot().set("AppVersion", version)
    with target.open("xb") as stream:
        registration.write(stream, encoding="utf-8", xml_declaration=True)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def copy_tree(source: Path, target: Path) -> None:
    from app.distribution.windows_install import plain_directory

    plain_directory(source)
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(directory) / name
            info = path.lstat()
            if (
                not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                or path.is_symlink()
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                raise ValueError(
                    "Сборочный вход содержит ссылку/reparse point: " + str(path)
                )
            if name in {
                "node_modules",
                ".runtime",
                ".venv",
                "__pycache__",
            } or path.suffix.lower() in {".dwg", ".dxf", ".db", ".sqlite", ".sqlite3"}:
                raise ValueError(
                    "Вход содержит developer данные или пользовательский CAD/БД"
                )
    shutil.copytree(source, target)


def native_evidence(
    binary: Path, rows: dict[str, Any], source_sha: str
) -> dict[str, object]:
    directory = binary.parent.parent
    receipt = json.loads((directory / "build-receipt.json").read_bytes())
    inventory = json.loads((directory / "source-inventory.json").read_bytes())
    if receipt["binary"]["sha256"] != digest(binary):
        raise ValueError("Native binary отличается от build receipt")
    if receipt["source_inventory_sha256"] != digest(
        directory / "source-inventory.json"
    ):
        raise ValueError("Native source inventory отличается от build receipt")
    for item in inventory["files"]:
        expected = rows.get(item["path"])
        if (
            expected is None
            or expected.sha256 != item["sha256"]
            or expected.bytes != item["size"]
        ):
            raise ValueError(
                "Native build выполнен из другого source seal: " + item["path"]
            )
    return {
        "schema": "green-atlas.packaged-native-evidence/1",
        "target": receipt["target"],
        "source_manifest_sha256": source_sha,
        "binary_sha256": digest(binary),
        "component_version": receipt["component_version"],
        "files": inventory["files"],
        "sdk_files": receipt["objectarx_files"],
        "execution_qualified": False,
    }


def assemble(args: argparse.Namespace) -> Path:
    from app.distribution.preflight import (
        SourceReceipt,
        _verify_source,
        build_manifest,
        check_package,
    )
    from app.distribution.windows_dependencies import dependency_inventory
    from app.distribution.windows_layout import (
        WINDOWS_ROOT,
        WindowsLaunch,
        windows_layout,
    )

    seal_bytes = args.source_manifest.read_bytes()
    source_sha = hashlib.sha256(seal_bytes).hexdigest()
    rows = _verify_source(REPO, SourceReceipt.model_validate_json(seal_bytes))
    version = product_version(REPO)
    args.output.mkdir(parents=True, exist_ok=False)
    bundle = args.output / "GreenAtlas.bundle"
    windows = bundle / WINDOWS_ROOT
    windows.mkdir(parents=True)
    write_registration(
        REPO / "tools/autocad-bridge/windows/PackageContents.xml",
        bundle / "PackageContents.xml",
        version,
    )
    shutil.copy2(args.gui, windows / "GreenAtlasBridge.arx")
    shutil.copy2(args.gui.parent / "producer.json", windows / "producer.json")
    shutil.copy2(args.host, windows / "GreenAtlasDesktop.exe")
    copy_tree(args.runtime.parent, windows / "Runtime")
    copy_tree(args.worker.parent, windows / "Worker")
    copy_tree(args.web, windows / "Web")
    launch = WindowsLaunch.model_validate(
        {
            "schema": "green-atlas.desktop-launch/1",
            "kind": "windows_webview2",
            "host": "GreenAtlasDesktop.exe",
            "runtime": "Runtime/GreenAtlasRuntime.exe",
            "web": "Web",
            "worker": "Worker",
        }
    )
    write(windows / "green-atlas-launch.json", launch.model_dump(by_alias=True))
    evidence = windows / "BuildEvidence"
    evidence.mkdir()
    notices = windows / "Notices"
    notices.mkdir()
    shutil.copy2(
        REPO / "tools/autocad-bridge/vendor/nlohmann/LICENSE.MIT",
        notices / "nlohmann-LICENSE.MIT",
    )
    from distribution_notices import collect_notices

    collect_notices(notices / "Dependencies", REPO)
    for name, binary in (("gui", args.gui), ("core", args.worker)):
        write(evidence / (name + ".json"), native_evidence(binary, rows, source_sha))
    api = json.loads((windows / "Runtime/frozen-api-provenance.json").read_bytes())
    if (
        api.get("compiled_source_matches") is not True
        or api.get("source_manifest_sha256") != source_sha
        or api.get("binary_sha256") != digest(args.runtime)
    ):
        raise ValueError("Frozen API provenance не соответствует исходникам/EXE")
    bootstrap_path = "tools/autocad-bridge/desktop/entry.py"
    if (
        api.get("entry", {}).get("source_path") != bootstrap_path
        or api["entry"]["source_sha256"] != rows[bootstrap_path].sha256
    ):
        raise ValueError("Frozen API bootstrap provenance не соответствует source seal")
    expected_modules = {
        path.removeprefix("apps/api/")
        .removesuffix(".py")
        .replace("/", ".")
        .removesuffix(".__init__"): row.sha256
        for path, row in rows.items()
        if path.startswith("apps/api/app/") and path.endswith(".py")
    }
    if {
        item["module"]: item["source_sha256"] for item in api["modules"]
    } != expected_modules:
        raise ValueError("Frozen API provenance не покрывает текущие app модули")
    host = json.loads((args.host.parent / "build-receipt.json").read_bytes())
    if host.get("version") != version:
        raise ValueError("Desktop host собран для другой версии продукта")
    if host["binary_sha256"] != digest(args.host):
        raise ValueError("Desktop host отличается от build receipt")
    for item in host["files"]:
        if item["path"] not in rows or rows[item["path"]].sha256 != item["sha256"]:
            raise ValueError("Desktop host собран из другого source seal")
    write(evidence / "desktop.json", host)
    web = json.loads((windows / "Web/web-build.json").read_bytes())
    if web["source_manifest_sha256"] != source_sha or web["vite_api_url"] != "":
        raise ValueError("Web build receipt не соответствует source seal/config")
    for item in web["files"]:
        from app.distribution.contracts import relative_path

        relative_path(item["path"])
        if digest(windows / "Web" / item["path"]) != item["sha256"]:
            raise ValueError("Web байты не соответствуют build receipt")
    write(
        windows / "dependency-inventory.json",
        dependency_inventory(
            bundle,
            crt=args.crt,
            autocad=args.autocad,
            objectarx_sdk=args.objectarx_sdk,
            objectarx_archive_sha256=args.objectarx_archive_sha256,
        ),
    )
    prereqs = windows / "Prerequisites"
    prereqs.mkdir()
    shutil.copy2(
        args.webview_installer, prereqs / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    )
    shutil.copy2(args.webview_receipt, prereqs / "webview2-installer.json")
    manifest = build_manifest(
        bundle,
        windows_layout(bundle),
        source_root=REPO,
        source_manifest=args.source_manifest,
    )
    write(
        bundle / "distribution-manifest.json",
        manifest.model_dump(by_alias=True, mode="json"),
    )
    check_package(bundle, manifest)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    web = commands.add_parser("web-receipt")
    web.add_argument("--directory", type=Path, required=True)
    web.add_argument("--source-manifest", type=Path, required=True)
    stage = commands.add_parser("assemble")
    for name in (
        "source-manifest",
        "gui",
        "host",
        "runtime",
        "worker",
        "web",
        "crt",
        "webview-installer",
        "webview-receipt",
        "output",
    ):
        stage.add_argument("--" + name, type=Path, required=True)
    external = stage.add_mutually_exclusive_group(required=True)
    external.add_argument("--autocad", type=Path)
    external.add_argument("--objectarx-sdk", type=Path)
    stage.add_argument("--objectarx-archive-sha256")
    args = parser.parse_args()
    if args.command == "web-receipt":
        files = [
            {"path": p.relative_to(args.directory).as_posix(), "sha256": digest(p)}
            for p in sorted(args.directory.rglob("*"))
            if p.is_file()
        ]
        write(
            args.directory / "web-build.json",
            {
                "schema": "green-atlas.web-build/1",
                "source_manifest_sha256": digest(args.source_manifest),
                "vite_api_url": "",
                "files": files,
            },
        )
    else:
        print(assemble(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
