"""Полная ad-hoc сборка Mac из чистого Git; пользовательские установки не меняет."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))
sys.path.insert(0, str(REPOSITORY / "apps/api"))


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plain_parents(path: Path) -> None:
    for current in (path, *path.parents):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise ValueError("Сборочный путь содержит ссылку: " + str(current))


def new_output(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError("Сборочный путь не допускает '..'")
    output = path.absolute()
    plain_parents(output)
    if output.exists() or not any(output.is_relative_to(REPOSITORY / directory) for directory in ("output", ".runtime")):
        raise ValueError("Нужен новый каталог сборки внутри output или .runtime checkout")
    return output


def run(command: list[str], name: str, logs: Path, environment: dict[str, str]) -> list[str]:
    print(json.dumps({"stage": name, "state": "started"}), flush=True)
    with (logs / (name + ".stdout.log")).open("xb") as stdout, (logs / (name + ".stderr.log")).open("xb") as stderr:
        result = subprocess.run(command, cwd=REPOSITORY, env=environment, stdout=stdout, stderr=stderr, check=False, timeout=1800)
    if result.returncode:
        raise RuntimeError(f"Сборка {name} завершилась с кодом {result.returncode}; см. {logs}")
    print(json.dumps({"stage": name, "state": "passed"}), flush=True)
    return (logs / (name + ".stdout.log")).read_text(encoding="utf-8").splitlines()


def result_path(lines: list[str], suffix: str) -> Path:
    paths = [Path(line) for line in lines if line.startswith(str(REPOSITORY) + "/") and line.endswith(suffix)]
    if len(paths) != 1 or not paths[0].exists():
        raise ValueError("Сборщик должен вернуть один существующий " + suffix)
    plain_parents(paths[0])
    return paths[0]


def public_assets(web: Path) -> dict[str, Any]:
    rows = []
    for source in sorted((REPOSITORY / "apps/web/public").rglob("*")):
        if not source.is_file():
            continue
        plain_parents(source)
        relative = source.relative_to(REPOSITORY / "apps/web/public")
        target = web / relative
        plain_parents(target)
        if not target.is_file() or digest(source) != digest(target):
            raise ValueError("Ресурс Web отсутствует или изменён: " + relative.as_posix())
        rows.append({"path": relative.as_posix(), "sha256": digest(target), "bytes": target.stat().st_size})
    if not rows:
        raise ValueError("В Web отсутствуют собственные публичные ресурсы")
    return {"passed": True, "files": rows}


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> int:
    from app.distribution.contracts import PackageManifest
    from app.distribution.preflight import RUNTIME_RESOURCES, check_package

    from scripts.desktop.distribution_notices import collect_notices
    from scripts.desktop.frozen_provenance import prove

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--node-store", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != "arm64":
        parser.error("Нужна сборочная macOS Apple Silicon")
    expected_python = (REPOSITORY / "apps/api/.python-version").read_text().strip()
    if platform.python_version() != expected_python:
        parser.error("Требуется закреплённый CPython " + expected_python)
    output = new_output(args.output)
    output.mkdir(parents=True, mode=0o700)
    evidence, logs, temporary = (output / name for name in ("evidence", "logs", "Temp"))
    for path in (evidence, logs, temporary):
        path.mkdir(mode=0o700)
    environment = dict(os.environ)
    environment.update(GREEN_ATLAS_BUILD_PYTHON=sys.executable, GREEN_ATLAS_SIGNING_IDENTITY="-", TMPDIR=str(temporary) + "/")
    environment.pop("GREEN_ATLAS_DESKTOP_TEST_PROFILE", None)
    environment.pop("GREEN_ATLAS_LAYER_RECOGNITION_PRESETS", None)
    environment.pop("GREEN_ATLAS_GEOMETRY_SELFTEST", None)
    run([sys.executable, "apps/api/scripts/check_runtime.py"], "qualified-python", logs, environment)
    run([sys.executable, "scripts/desktop/seal_git_source.py", str(REPOSITORY), str(evidence)], "source-seal", logs, environment)
    source = json.loads((evidence / "git-source-receipt.json").read_bytes())
    runtime = result_path(run(["bash", "tools/autocad-bridge/build-desktop-runtime.sh"], "runtime", logs, environment), "/GreenAtlasRuntime")
    desktop = result_path(run(["bash", "tools/autocad-bridge/build-desktop-macos.sh", str(runtime)], "desktop", logs, environment), "/Green Atlas.app")
    environment["GREEN_ATLAS_DESKTOP_APP"] = str(desktop)
    package = result_path(run(["bash", "tools/autocad-bridge/build-macos.sh"], "native", logs, environment), "/GreenAtlasBridge.package.bundle")
    notices = evidence / "Notices"
    notices.mkdir()
    shutil.copyfile(REPOSITORY / "tools/autocad-bridge/vendor/nlohmann/LICENSE.MIT", notices / "nlohmann-LICENSE.MIT")
    notice_receipt = collect_notices(notices / "Dependencies", REPOSITORY, node_store=args.node_store)
    write_json(evidence / "notices.json", notice_receipt)
    environment["GREEN_ATLAS_DISTRIBUTION_NOTICES"] = str(notices)
    environment["GREEN_ATLAS_SOURCE_MANIFEST"] = str(evidence / "source-manifest.json")
    environment["GREEN_ATLAS_SOURCE_ROOT"] = str(REPOSITORY)
    installer = result_path(run(["bash", "tools/autocad-bridge/package-macos.sh", str(package)], "installer", logs, environment), "/Green Atlas Installer.app")
    payload = installer / "Contents/Resources/GreenAtlasBridge.bundle"
    for expected in notices.rglob("*"):
        if expected.is_file():
            actual = installer / "Contents/Resources/Notices" / expected.relative_to(notices)
            plain_parents(actual)
            if not actual.is_file() or digest(actual) != digest(expected):
                raise ValueError("Уведомление зависимости отсутствует или изменено в Installer")
    manifest_path = installer / "Contents/Resources/distribution-manifest.json"
    manifest = PackageManifest.model_validate_json(manifest_path.read_bytes())
    check = check_package(payload, manifest)
    write_json(evidence / "package-check.json", json.loads(check.model_dump_json(by_alias=True)))
    shutil.copyfile(manifest_path, evidence / "distribution-manifest.json")
    app_resources = payload / "Contents/Applications/Green Atlas.app/Contents/Resources"
    api_root = app_resources / "Runtime"
    proof = prove(api_root / "GreenAtlasRuntime", REPOSITORY, evidence / "source-manifest.json")
    write_json(evidence / "frozen-api-provenance.json", proof)
    raw = []
    for name in RUNTIME_RESOURCES:
        expected = REPOSITORY / "apps/api/app" / name
        actual = api_root / "_internal/app" / name
        if actual.read_bytes() != expected.read_bytes():
            raise ValueError("Вложенный API ресурс отличается от Git: " + name)
        raw.append({"path": name, "sha256": digest(actual), "bytes": actual.stat().st_size})
    write_json(evidence / "raw-resources.json", {"passed": True, "resources": raw})
    write_json(evidence / "web-public-assets.json", public_assets(app_resources / "Web"))
    for bundle in (installer, payload / "Contents/Applications/Green Atlas.app", payload / "Contents/MacOS/GreenAtlasBridge.bundle", payload / "Contents/Workers/GreenAtlasQuery.bundle"):
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], check=True, capture_output=True)
    # Повторная проверка checkout после всех компиляторов и упаковщиков.
    run([sys.executable, "scripts/desktop/package_preflight.py", "check-source", "--source-root", str(REPOSITORY), "--source-manifest", str(evidence / "source-manifest.json")], "source-after", logs, environment)
    with (installer / "Contents/Info.plist").open("rb") as stream:
        version = plistlib.load(stream)["CFBundleShortVersionString"]
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise ValueError("Некорректная версия установщика")
    receipt = {"schema": "green-atlas.macos-product-build/1", "state": "built-candidate", "git_commit": source["git_commit"],
               "source_manifest_sha256": digest(evidence / "source-manifest.json"), "distribution_manifest_sha256": digest(manifest_path),
               "installer": str(installer), "installer_executable_sha256": digest(installer / "Contents/MacOS/GreenAtlasInstaller"),
               "version": version, "architecture": "arm64", "build_host_macos": platform.mac_ver()[0], "minimum_macos_build_target": "14.0",
               "minimum_macos_execution_qualified": False, "ad_hoc_signed": True, "developer_id_signed": False, "notarized": False,
               "every_component_built": True, "compiled_api_source_matches": True, "raw_resources_verified": len(raw),
               "autocad_executed": False, "blender_executed": False, "user_installation_modified": False,
               "botaniq_or_pbr_included": False, "sdk_included": False, "autodesk_modules_included": False, "product_qualified": False}
    write_json(output / "product-build-receipt.json", receipt)
    print(json.dumps(receipt), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
