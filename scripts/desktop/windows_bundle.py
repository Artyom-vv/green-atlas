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
import zipfile
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


# Only this accepted release is supported by the recovery path. These pins are
# build-tool policy, never supplied by the caller or reconstructed from receipts.
ACCEPTED_131 = {
    "installer_sha256": "244d7edb5efa47b000a38858f7a3315135729c18b6b779055610e5adf8a037a8",
    "installer_bytes": 297859763,
    "manifest_sha256": "bed6ced19f8a1077e7366c05ded67185f9da249b83b407e7577a848f072ae8bc",
    "source_sha256": "7afff98ccd5ed76cffc7ac7320349fa0a465775e31edb0b0ac8c0cc0ae16c624",
    "recovery_sha256": "064c3ece5bff0eec1437fd036a3b08f66345780e66161f4de8875c69629ed3ba",
    "gui_evidence_sha256": "a0fead81ed3e1587e78e6988e2b86605eaf1183383a23a05561edb8e80d9900f",
    "core_evidence_sha256": "72ff270b8cef15a1f7eb5f49cfb50030bec5dfa930ad8241e4a1168b5e643d4e",
    "gui_binary_sha256": "3fd68ad5e4c8d16b629501bb91f4eb25ad50e1434aa3df85dafc3f4a529e8f60",
    "core_binary_sha256": "f2e37f9c5031169ef1b2a3bc91a34850907a0b1e9a2780830ef257ac41ccef9f",
}
ACCEPTED_131_FILES = frozenset({
    "distribution-manifest.json", "RECOVERY-PROVENANCE.json",
    "Contents/Windows/BuildEvidence/gui.json", "Contents/Windows/BuildEvidence/core.json",
    "Contents/Windows/GreenAtlasBridge.arx", "Contents/Windows/producer.json",
    "Contents/Windows/Worker/GreenAtlasQuery.crx", "Contents/Windows/Worker/worker.json",
    "Contents/Windows/dependency-inventory.json",
    *(prefix + name for prefix in ("Contents/Windows/", "Contents/Windows/Worker/")
      for name in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")),
})


def _unique_object(data: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("Повторён JSON ключ accepted native package")
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=pairs)
    if not isinstance(result, dict):
        raise ValueError("Accepted native metadata требует JSON object")  # noqa: TRY004
    return result


def _native_rows(items: Any) -> dict[str, tuple[int, str]]:
    from app.distribution.contracts import relative_path

    if not isinstance(items, list) or not 1 <= len(items) <= 512:
        raise ValueError("Недопустимый native source/SDK inventory")
    result: dict[str, tuple[int, str]] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise ValueError("Недопустимая native inventory row")
        path = relative_path(item["path"])
        key = path.casefold()
        if (key in result or type(item["size"]) is not int or item["size"] <= 0
                or not isinstance(item["sha256"], str)
                or not re.fullmatch("[0-9a-f]{64}", item["sha256"])):
            raise ValueError("Недопустимая/повторная native inventory identity")
        result[key] = (item["size"], item["sha256"])
    return result


def accepted_native_package(
    directory: Path, rows: dict[str, Any], source_sha: str
) -> tuple[dict[str, bytes], dict[str, dict[str, Any]]]:
    """Read only the pinned minimal recovery kit; never manufacture build receipts.

    Return verified byte buffers, so later copies cannot reread changed inputs.
    The original flattened evidence remains byte-identical in the origin ZIP.
    """
    from app.distribution.contracts import relative_path
    from app.distribution.preflight import _plain_file
    from app.distribution.windows_install import plain_directory

    plain_directory(directory)
    observed: set[str] = set()
    folded: set[str] = set()
    allowed_directories = {
        parent.as_posix() for name in ACCEPTED_131_FILES
        for parent in Path(name).parents if parent.as_posix() != "."
    }
    for parent, dirs, files in os.walk(directory, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(parent) / name
            relative = path.relative_to(directory).as_posix()
            relative_path(relative)
            info = path.lstat()
            if (path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400
                    or relative.casefold() in folded):
                raise ValueError("Accepted native input содержит ссылку/reparse/повтор пути")
            folded.add(relative.casefold())
            if stat.S_ISDIR(info.st_mode):
                if relative not in allowed_directories:
                    raise ValueError("Лишний каталог accepted native input")
            elif stat.S_ISREG(info.st_mode) and relative in ACCEPTED_131_FILES:
                if info.st_size > 4 * 1024**2:
                    raise ValueError("Превышен размер accepted native input")
                observed.add(relative)
            else:
                raise ValueError("Лишний/специальный файл accepted native input")
    if observed != ACCEPTED_131_FILES:
        raise ValueError("Неполный accepted native package")
    blobs = {name: _plain_file(directory, name).read_bytes() for name in sorted(observed)}

    def sha(name: str) -> str:
        return hashlib.sha256(blobs[name]).hexdigest()

    if (sha("distribution-manifest.json") != ACCEPTED_131["manifest_sha256"]
            or sha("RECOVERY-PROVENANCE.json") != ACCEPTED_131["recovery_sha256"]):
        raise ValueError("Accepted131 manifest/provenance не соответствует закреплённому выпуску")
    manifest = _unique_object(blobs["distribution-manifest.json"])
    recovery = _unique_object(blobs["RECOVERY-PROVENANCE.json"])
    if (manifest.get("source", {}).get("manifest_sha256") != ACCEPTED_131["source_sha256"]
            or recovery.get("schema") != "green-atlas.accepted131-native-recovery/1"
            or recovery.get("outer_installer_sha256") != ACCEPTED_131["installer_sha256"]
            or recovery.get("outer_installer_bytes") != ACCEPTED_131["installer_bytes"]
            or recovery.get("distribution_manifest_sha256") != ACCEPTED_131["manifest_sha256"]
            or recovery.get("original_native_build_receipts_present") is not False):
        raise ValueError("Accepted131 origin identity не совпала")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or len(entries) > 10000:
        raise ValueError("Некорректный accepted131 manifest inventory")
    inventory: dict[str, Any] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Некорректный accepted131 manifest row")  # noqa: TRY004
        path = relative_path(entry.get("path"))
        if path.casefold() in inventory:
            raise ValueError("Повторный accepted131 manifest path")
        inventory[path.casefold()] = entry
    for name, data in blobs.items():
        if name in {"distribution-manifest.json", "RECOVERY-PROVENANCE.json"}:
            continue
        entry = inventory.get(name.casefold(), {})
        if (entry.get("path") != name or entry.get("kind") != "file"
                or entry.get("size_bytes") != len(data) or entry.get("sha256") != sha(name)):
            raise ValueError("Accepted131 файл не совпал с исходным manifest: " + name)
    evidence: dict[str, dict[str, Any]] = {}
    for target, binary, metadata, version in (
        ("gui", "GreenAtlasBridge.arx", "producer.json", "0.1.48"),
        ("core", "Worker/GreenAtlasQuery.crx", "Worker/worker.json", "0.1.49"),
    ):
        name = "Contents/Windows/BuildEvidence/" + target + ".json"
        binary_path = "Contents/Windows/" + binary
        if (sha(name) != ACCEPTED_131[target + "_evidence_sha256"]
                or sha(binary_path) != ACCEPTED_131[target + "_binary_sha256"]):
            raise ValueError("Accepted131 native/evidence SHA не совпал")
        original = _unique_object(blobs[name])
        if (set(original) != {"schema", "target", "source_manifest_sha256", "binary_sha256",
                              "component_version", "files", "sdk_files", "execution_qualified"}
                or original["schema"] != "green-atlas.packaged-native-evidence/1"
                or original["target"] != target or original["component_version"] != version
                or original["source_manifest_sha256"] != ACCEPTED_131["source_sha256"]
                or original["binary_sha256"] != sha(binary_path)
                or original["execution_qualified"] is not False):
            raise ValueError("Accepted131 native evidence contract не совпал")
        _native_rows(original["sdk_files"])
        _native_rows(original["files"])
        # Match the complete inventory policy in build-windows-2027.ps1, not
        # merely the old inventory as a subset of the new sealed source tree.
        plan_path = "tools/autocad-bridge/windows-2027/build-plan.json"
        plan_bytes = _plain_file(REPO, plan_path).read_bytes()
        sealed_plan = rows.get(plan_path)
        if (sealed_plan is None or sealed_plan.bytes != len(plan_bytes)
                or sealed_plan.sha256 != hashlib.sha256(plan_bytes).hexdigest()):
            raise ValueError("Native build plan отличается от текущего source seal")
        plan = _unique_object(plan_bytes)
        if plan.get("schema") != "green-atlas.native-build-plan/1":
            raise ValueError("Неизвестный native build plan")
        component = plan.get("targets", {}).get(target, {})
        sources = component.get("sources")
        adapters = component.get("macos_adapters", []) if target == "gui" else []
        if (not isinstance(sources, list) or not isinstance(adapters, list)
                or not all(isinstance(path, str) for path in [*sources, *adapters])):
            raise ValueError("Native build plan не содержит полного списка исходников")
        expected_paths = {relative_path(path) for path in [*sources, *adapters]}
        prefixes = tuple("tools/autocad-bridge/" + directory + "/"
                         for directory in ("native", "query-worker", "vendor", "windows"))
        for path in rows:
            lowered = path.casefold()
            if (lowered.startswith(prefixes) and
                    (Path(lowered).suffix in {".h", ".hpp"}
                     or Path(lowered).name in {"readme.md", "license.mit"})):
                expected_paths.add(path)
        expected_paths.update({plan_path, "tools/autocad-bridge/build-windows-2027.ps1",
                               "tools/autocad-bridge/build-macos.sh",
                               "tools/autocad-bridge/build-query-worker-macos.sh"})
        if {item["path"] for item in original["files"]} != expected_paths:
            raise ValueError("Accepted131 native source closure pathset отличается")
        for item in original["files"]:
            expected = rows.get(item["path"])
            if (expected is None or expected.bytes != item["size"]
                    or expected.sha256 != item["sha256"]):
                raise ValueError("Accepted131 native source closure отличается: " + item["path"])
        meta = _unique_object(blobs["Contents/Windows/" + metadata])
        if (meta.get("target") != "windows-x86_64" or meta.get("binary") != Path(binary).name
                or meta.get("plugin_version") != version or meta.get("sha256") != sha(binary_path)):
            raise ValueError("Accepted131 native metadata не совпала")
        evidence[target] = {
            **original, "source_manifest_sha256": source_sha,
            "reused_from": {
                "source_kind": "recovered_accepted_release",
                "release_tag": "v0.1.131-windows-m003",
                "installer_sha256": ACCEPTED_131["installer_sha256"],
                "installer_bytes": ACCEPTED_131["installer_bytes"],
                "distribution_manifest_sha256": ACCEPTED_131["manifest_sha256"],
                "source_manifest_sha256": ACCEPTED_131["source_sha256"],
                "raw_evidence_sha256": sha(name),
                "origin_archive": "Contents/Windows/BuildEvidence/accepted131-origin.zip",
                "new_native_compilation": False,
            },
        }
    return blobs, evidence


def write_accepted_native(
    windows: Path, blobs: dict[str, bytes], evidence: dict[str, dict[str, Any]]
) -> None:
    """Copy verified native buffers; keep raw origin PE inside an inventory-sealed ZIP."""
    (windows / "Worker").mkdir()
    for name in ("GreenAtlasBridge.arx", "producer.json", "Worker/GreenAtlasQuery.crx", "Worker/worker.json"):
        with (windows / name).open("xb") as stream:
            stream.write(blobs["Contents/Windows/" + name])
    origin = windows / "BuildEvidence/accepted131-origin.zip"
    with zipfile.ZipFile(origin, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(blobs.items()):
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, data)
    for value in evidence.values():
        value["reused_from"]["origin_archive_sha256"] = digest(origin)


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
    accepted = getattr(args, "accepted_native_package", None)
    if accepted is not None and (args.gui is not None or args.worker is not None):
        raise ValueError("Выберите original build inputs или accepted native package")
    if accepted is None and (args.gui is None or args.worker is None):
        raise ValueError("Original native mode требует GUI и Core с исходными receipts")
    recovered = accepted_native_package(accepted, rows, source_sha) if accepted else None
    args.output.mkdir(parents=True, exist_ok=False)
    bundle = args.output / "GreenAtlas.bundle"
    windows = bundle / WINDOWS_ROOT
    windows.mkdir(parents=True)
    write_registration(
        REPO / "tools/autocad-bridge/windows/PackageContents.xml",
        bundle / "PackageContents.xml",
        version,
    )
    evidence = windows / "BuildEvidence"
    evidence.mkdir()
    if recovered:
        write_accepted_native(windows, *recovered)
    else:
        shutil.copy2(args.gui, windows / "GreenAtlasBridge.arx")
        shutil.copy2(args.gui.parent / "producer.json", windows / "producer.json")
    shutil.copy2(args.host, windows / "GreenAtlasDesktop.exe")
    copy_tree(args.runtime.parent, windows / "Runtime")
    if not recovered:
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
    notices = windows / "Notices"
    notices.mkdir()
    shutil.copy2(
        REPO / "tools/autocad-bridge/vendor/nlohmann/LICENSE.MIT",
        notices / "nlohmann-LICENSE.MIT",
    )
    from distribution_notices import collect_notices

    collect_notices(notices / "Dependencies", REPO)
    for name, binary in (("gui", args.gui), ("core", args.worker)):
        value = recovered[1][name] if recovered else native_evidence(binary, rows, source_sha)
        write(evidence / (name + ".json"), value)
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
        "host",
        "runtime",
        "web",
        "crt",
        "webview-installer",
        "webview-receipt",
        "output",
    ):
        stage.add_argument("--" + name, type=Path, required=True)
    stage.add_argument("--gui", type=Path)
    stage.add_argument("--worker", type=Path)
    stage.add_argument("--accepted-native-package", type=Path)
    external = stage.add_mutually_exclusive_group(required=True)
    external.add_argument("--autocad", type=Path)
    external.add_argument("--objectarx-sdk", type=Path)
    stage.add_argument("--objectarx-archive-sha256")
    accepted_check = commands.add_parser("check-accepted-native")
    accepted_check.add_argument("--accepted-native-package", type=Path, required=True)
    accepted_check.add_argument("--source-manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "assemble":
        if bool(args.accepted_native_package) == bool(args.gui or args.worker):
            parser.error("Choose exactly one native mode: --gui/--worker or --accepted-native-package")
        if args.accepted_native_package is None and (args.gui is None or args.worker is None):
            parser.error("Original native mode requires both --gui and --worker")
    if args.command == "check-accepted-native":
        from app.distribution.preflight import SourceReceipt, _verify_source

        sealed = args.source_manifest.read_bytes()
        rows = _verify_source(REPO, SourceReceipt.model_validate_json(sealed))
        blobs, _ = accepted_native_package(args.accepted_native_package, rows, hashlib.sha256(sealed).hexdigest())
        print(json.dumps({"accepted_native_verified": True, "origin_files": len(blobs),
                          "new_native_compilation": False, "Windows_execution_qualified": False}))
        return 0
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
