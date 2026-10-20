"""Повторная CI-проверка неизменного Setup с новой диагностикой, без пересборки продукта."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import stat
import subprocess
import zlib
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.request import urlopen

REPOSITORY = Path(__file__).resolve().parents[2]
REPO = "Artyom-vv/green-atlas-dev"
QA_FILES = frozenset({
    ".github/workflows/windows-candidate-check.yml",
    "scripts/desktop/ci_windows_candidate.py",
    "scripts/desktop/windows_product_install_smoke.py",
    "scripts/desktop/windows_qualification.py",
    "scripts/desktop/test_windows_product_install_smoke.py",
    "scripts/desktop/windows_prerequisite_smoke.py",
    "scripts/desktop/windows_wizard_smoke.py",
    "scripts/desktop/test_windows_prerequisite_binding.py",
    "docs/obsidian/03 Модули/Подготовка Windows и упаковка.md",
})


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def command(arguments: list[str]) -> bytes:
    return subprocess.run(arguments, cwd=REPOSITORY, capture_output=True, check=True, timeout=180).stdout


def git(*arguments: str) -> bytes:
    return command(["git", *arguments])


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plain(path: Path) -> None:
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode)
            and not getattr(info, "st_file_attributes", 0) & 0x400, "Нужен обычный файл: " + str(path))


def read(path: Path) -> dict[str, Any]:
    plain(path)
    value = json.loads(path.read_bytes())
    require(isinstance(value, dict), "Нужен JSON object")
    return value


def canonical(value: str) -> str:
    require(isinstance(value, str), "Нужен source path")
    path = PurePosixPath(value)
    require(path.parts and str(path) == value and not path.is_absolute() and ".." not in path.parts
            and "\\" not in value and ":" not in value, "Неканонический source path")
    return value


def unique(root: Path, name: str) -> Path:
    paths = list(root.rglob(name))
    require(len(paths) == 1, "Нужен один " + name)
    plain(paths[0])
    return paths[0]


def compare_sources(manifest: dict[str, Any]) -> dict[str, Any]:
    """Все tracked bytes текущего checkout равны Git; production bytes равны старому seal."""
    require(not git("status", "--porcelain=v1", "--untracked-files=no"), "Нужен чистый verification checkout")
    previous: dict[str, dict[str, Any]] = {}
    for row in manifest["files"]:
        name = canonical(row["path"])
        require(name not in previous and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "Повторённый/некорректный source seal")
        previous[name] = row
    current: dict[str, dict[str, Any]] = {}
    algorithm = git("rev-parse", "--show-object-format").decode().strip()
    require(algorithm in {"sha1", "sha256"}, "Неизвестный формат Git")
    for record in git("ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not record:
            continue
        header, encoded = record.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        name = canonical(encoded.decode())
        require(kind == "blob" and mode in {"100644", "100755"}, "Необычный tracked Git object")
        path = REPOSITORY.joinpath(*PurePosixPath(name).parts)
        plain(path)
        size = path.stat().st_size
        blob, sha, observed = hashlib.new(algorithm), hashlib.sha256(), 0
        blob.update(b"blob " + str(size).encode() + b"\0")
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                blob.update(block)
                sha.update(block)
                observed += len(block)
        require(observed == size and blob.hexdigest() == oid, "Checkout bytes отличаются от Git: " + name)
        current[name] = {"sha256": sha.hexdigest(), "bytes": size}
    changes = []
    for name in sorted(previous.keys() | current.keys()):
        before, after = previous.get(name), current.get(name)
        same = before is not None and after is not None and before["sha256"] == after["sha256"] and before["bytes"] == after["bytes"]
        if not same:
            require(name in QA_FILES, "Производственный source изменился; нужна полная сборка: " + name)
            changes.append({"path": name, "candidate": before, "verification": after})
    return {"original_source_rows": len(previous), "verification_source_rows": len(current),
            "all_current_checkout_bytes_match_git": True, "production_source_bytes_unchanged": True,
            "qa_whitelist": sorted(QA_FILES), "qa_changes": changes}


def extract_entry(archive: Any, name: str, *, maximum: int) -> bytes:
    names: dict[str, str] = {}
    for original in archive.toc:
        normalized = original.replace("\\", "/")
        require(normalized.casefold() not in names, "Неоднозначный CArchive path")
        names[normalized.casefold()] = original
    require(name.casefold() in names, "В Setup отсутствует " + name)
    offset, packed_size, size, compressed, kind = archive.toc[names[name.casefold()]]
    require(kind in {"b", "x"} and 0 < size <= maximum and 0 < packed_size <= maximum and compressed in (0, 1), "Некорректный CArchive member")
    with open(archive._filename, "rb") as stream:
        stream.seek(archive._start_offset + offset)
        packed = stream.read(packed_size)
    require(len(packed) == packed_size, "Обрезанный CArchive member")
    if compressed:
        decoder = zlib.decompressobj()
        data = decoder.decompress(packed, size + 1)
        require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail, "CArchive превышает заявленные границы")
    else:
        data = packed
    require(len(data) == size, "Размер CArchive member не совпал")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-run-id", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--setup-sha256", required=True)
    parser.add_argument("--output", type=Path, default=Path(".runtime/autocad-bridge/ci-product"))
    args = parser.parse_args()
    require(os.name == "nt" and os.environ.get("GITHUB_ACTIONS") == "true", "Допустим только настоящий Windows CI")
    require(re.fullmatch(r"[1-9][0-9]*", args.candidate_run_id) and re.fullmatch(r"[0-9a-f]{40}", args.candidate_commit)
            and re.fullmatch(r"[0-9a-f]{64}", args.setup_sha256), "Неверная identity кандидата")
    require(os.environ.get("GITHUB_REPOSITORY", "").casefold() == REPO.casefold(), "Неверный рабочий репозиторий")
    root = args.output.absolute()
    require(root.is_relative_to(REPOSITORY / ".runtime") and not root.exists(), "Нужен новый output в .runtime checkout")
    root.mkdir(parents=True)
    run = json.loads(command(["gh", "api", f"repos/{REPO}/actions/runs/{args.candidate_run_id}"]))
    require(run["head_sha"] == args.candidate_commit and run["status"] == "completed"
            and run["path"] == ".github/workflows/windows-product-build.yml"
            and run["repository"]["full_name"].casefold() == REPO.casefold(), "Исходный CI run не соответствует кандидату")
    downloaded = root / "download"
    command(["gh", "run", "download", args.candidate_run_id, "--repo", REPO, "--name", "windows-product-" + args.candidate_commit, "--dir", str(downloaded)])
    build_path, product_path = unique(downloaded, "setup-build-receipt.json"), unique(downloaded, "product-build-receipt.json")
    source_path = unique(downloaded, "source-manifest.json")
    build, product = read(build_path), read(product_path)
    native = product.get("native_wizard") is True
    version = product.get("product_version", "0.1.119")  # Legacy receipts before version metadata.
    require(isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version), "Некорректная версия продукта")
    expected_name = f"GreenAtlas-{version}-Windows-x64-Setup-2.exe" if native else f"GreenAtlas-{version}-Windows-x64-Setup.exe"
    require(product.get("setup") == expected_name, "Неизвестный installer filename")
    setup = unique(downloaded, expected_name)
    require(build["schema"] == "green-atlas.windows-setup-build/1" and product["schema"] == "green-atlas.windows-product-build/1", "Не production build receipts")
    require(build.get("state") == product.get("state") == "built-candidate" and product.get("every_component_built") is True
            and product.get("sdk_included") is False and product.get("autodesk_modules_included") is False
            and product.get("product_qualified") is False, "Неверный scope production кандидата")
    require(product["sha256"] == args.setup_sha256 == digest(setup)
            and product["bytes"] == setup.stat().st_size
            and product["git_commit"] == args.candidate_commit, "Setup bytes/receipt не соответствуют immutable кандидату")
    wizard = read(unique(downloaded, "wizard-build-receipt.json")) if native else None
    if native:
        require(wizard is not None and wizard.get("schema") == "green-atlas.windows-native-wizard-build/1"
                and wizard["sha256"] == product["sha256"] and wizard["bytes"] == product["bytes"]
                and wizard["backend_sha256"] == product["backend_sha256"] == build["sha256"]
                and wizard["source_manifest_sha256"] == product["source_manifest_sha256"]
                and wizard["distribution_manifest_sha256"] == build["distribution_manifest_sha256"], "Native frontend/backend/source binding не совпал")
    else:
        require(build["sha256"] == product["sha256"] and build["bytes"] == product["bytes"], "Legacy Setup receipt отличается")
    require(build["source_manifest_sha256"] == product["source_manifest_sha256"] == digest(source_path), "Source seal не совпал")
    source_proof = compare_sources(read(source_path))
    evidence, deliverable = root / "evidence", root / "deliverable"
    evidence.mkdir()
    deliverable.mkdir()
    for path in build_path.parent.rglob("*"):
        if not path.is_file() or path.name == "machine-webview2-upgrade.json":
            continue
        plain(path)
        require(path.suffix in {".json", ".log", ".xml"}, "В evidence найден неразрешённый файл")
        destination = evidence / path.relative_to(build_path.parent)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    for path in (setup, product_path, setup.parent / "README-Windows.md", setup.parent / "render-resources.example.json"):
        plain(path)
        shutil.copyfile(path, deliverable / path.name)
    if native:
        # Внешний native EXE не является CArchive. Его точные байты и неизменные
        # production sources проверены выше; actual UI install затем измеряет
        # сохранённый backend и полный опубликованный bundle.
        original = [path for path in downloaded.rglob("install-ci-receipt.json") if "install-smoke" in path.parts and path.parent.name == "user-checks"]
        require(len(original) == 1, "Нужна исходная успешная приёмка точного backend")
        original_report = read(original[0])
        require(original_report.get("passed") is True and original_report.get("setup_sha256") == build["sha256"]
                and original_report.get("source_manifest_sha256") == build["source_manifest_sha256"]
                and original_report.get("distribution_manifest_sha256") == build["distribution_manifest_sha256"], "Исходная приёмка backend не соответствует build")
        shutil.copyfile(original[0], deliverable / "installation-checks.json")
        manifest_data = (evidence / "distribution-manifest.json").read_bytes()
        archive = None
    else:
        pyinstaller = importlib.import_module("PyInstaller")
        readers = importlib.import_module("PyInstaller.archive.readers")
        require(pyinstaller.__version__ == "6.22.0", "Нужен pinned CArchiveReader6.22.0")
        archive = readers.CArchiveReader(str(setup))
        manifest_data = extract_entry(archive, "payload/GreenAtlas.bundle/distribution-manifest.json", maximum=16 * 1024**2)
    require(hashlib.sha256(manifest_data).hexdigest() == build["distribution_manifest_sha256"], "Вложенный manifest не совпал")
    require((evidence / "distribution-manifest.json").read_bytes() == manifest_data, "Evidence manifest отличается от реального Setup")
    manifest = json.loads(manifest_data)
    require(manifest["source"]["manifest_sha256"] == digest(source_path), "Payload связан с другим source seal")
    relative = "Contents/Windows/Prerequisites/MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    entries = [row for row in manifest["entries"] if row["path"] == relative]
    pinned = read(REPOSITORY / "tools/autocad-bridge/windows-product-inputs.json")["webview_runtime"]["sha256"]
    require(len(entries) == 1 and entries[0]["kind"] == "file" and entries[0]["sha256"] == pinned, "Нет sealed Microsoft prerequisite")
    tools = root / "tools"
    tools.mkdir()
    installer = tools / "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
    if native:
        inputs = read(REPOSITORY / "tools/autocad-bridge/windows-product-inputs.json")["webview_runtime"]
        require(inputs["url"].startswith("https://msedge.sf.dl.delivery.mp.microsoft.com/"), "Неофициальный Microsoft URL")
        with urlopen(inputs["url"], timeout=120) as source, installer.open("xb") as destination:
            received = 0
            while block := source.read(1024 * 1024):
                received += len(block)
                require(received <= 512 * 1024**2, "Runtime превысил предел")
                destination.write(block)
        require(installer.stat().st_size == entries[0]["size_bytes"] and digest(installer) == pinned, "Microsoft runtime не соответствует sealed manifest")
    else:
        data = extract_entry(archive, "payload/GreenAtlas.bundle/" + relative, maximum=512 * 1024**2)
        require(len(data) == entries[0]["size_bytes"] and hashlib.sha256(data).hexdigest() == pinned, "Microsoft prerequisite bytes отличаются")
        installer.write_bytes(data)
    receipt = {"schema": "green-atlas.windows-candidate-recheck/1", "candidate_run_id": int(args.candidate_run_id),
               "candidate_code_git": args.candidate_commit, "verification_git": git("rev-parse", "HEAD").decode().strip(),
               "setup_sha256": args.setup_sha256, "setup_bytes": setup.stat().st_size,
               "source_manifest_sha256": digest(source_path), "distribution_manifest_sha256": build["distribution_manifest_sha256"],
               "installer_sha256": pinned, "sealed_payload_bytes_verified": True, "old_install_smoke_copied": False,
               "product_rebuilt": False, "sdk_downloaded": False, "node_required": False, "product_qualified": False, **source_proof}
    receipt.update(native_wizard=native, carchive_extracted=not native,
                   native_backend_acceptance_reused=native, expected_backend_sha256=build["sha256"])
    (evidence / "candidate-recheck-source.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
