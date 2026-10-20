"""Локальные уведомления зависимостей, без импорта их кода или сетевых запросов."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import site
import stat
import sys
from email.parser import BytesParser
from email.policy import default as email_policy
from pathlib import Path, PurePosixPath
from typing import Any

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_PACKAGES = 4096
MAX_FILES = 8192
NOTICE_NAME = re.compile(r"^(licen[cs]e|notice|copying|authors?|copyright)(?:$|[._-])", re.IGNORECASE)


def _checked(path: Path, boundary: Path) -> Path:
    """Каждый компонент остаётся внутри разрешённого дерева, включая ссылки."""
    boundary = boundary.absolute()
    if not path.absolute().is_relative_to(boundary):
        raise ValueError("Путь уведомления выходит за разрешённый каталог")
    resolved_root = boundary.resolve()
    for current in (path, *path.parents):
        if not current.resolve().is_relative_to(resolved_root):
            raise ValueError("Ссылка уведомления выходит за разрешённый каталог")
        if current == boundary:
            break
    return path


class _Reader:
    def __init__(self) -> None:
        self.total = 0
        self.files = 0

    def read(self, path: Path, boundary: Path) -> bytes:
        _checked(path, boundary)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            raise ValueError("Уведомление должно быть ограниченным обычным файлом")
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        self.total += len(data)
        self.files += 1
        if len(data) > MAX_FILE_BYTES or self.total > MAX_TOTAL_BYTES or self.files > MAX_FILES:
            raise ValueError("Превышен предел чтения уведомлений")
        return data


def _relative(value: str) -> Path:
    parts = value.split("/")
    if not value or "\\" in value or ":" in value or "\0" in value or any(p in {"", ".", ".."} for p in parts):
        raise ValueError("Некорректный License-File в metadata")
    if PurePosixPath(value).is_absolute():
        raise ValueError("License-File должен быть относительным")
    return Path(*parts)


def _notice_files(root: Path, *, recursive: bool = False) -> list[Path]:
    result: list[Path] = []
    pending = [(root, 0)]
    observed = 0
    while pending:
        directory, depth = pending.pop()
        for path in sorted(directory.iterdir()):
            observed += 1
            if observed > 4096:
                raise ValueError("Слишком большой каталог уведомлений")
            _checked(path, root)
            if path.is_dir():
                if recursive or path.name.casefold() in {"license", "licenses", "licence", "licences", "notices"}:
                    if depth >= 12:
                        raise ValueError("Слишком глубокий каталог уведомлений")
                    pending.append((path, depth + 1))
            elif NOTICE_NAME.match(path.name):
                result.append(path)
    return result


def _python_sites() -> list[tuple[Path, Path]]:
    prefix = Path(sys.prefix).absolute()
    return [(Path(value).absolute(), prefix) for value in site.getsitepackages()
            if Path(value).absolute().is_relative_to(prefix)]


def _interpreter() -> dict[str, Any]:
    root = Path(sys.base_prefix).absolute()
    candidates = []
    names = ["LICENSE", "LICENSE.txt", "LICENSE.md"]
    if sys.platform == "darwin":
        # Standalone CPython хранит собственную лицензию рядом со стандартной библиотекой.
        version = ".".join(sys.version.split()[0].split(".")[:2])
        names.append(f"lib/python{version}/LICENSE.txt")
    for name in names:
        path = _checked(root / name, root)
        if path.exists() or path.is_symlink():
            candidates.append((path, root))
            break
    if not candidates and sys.platform == "win32" and sys.implementation.name == "cpython":
        raise ValueError("Для Windows Setup не найдена лицензия CPython в sys.base_prefix")
    return {"ecosystem": "python-interpreter", "name": sys.implementation.name,
            "version": sys.version.split()[0], "metadata": {"implementation": sys.implementation.name, "version": sys.version},
            "location": "interpreter-base", "candidates": candidates,
            "missing_declared_license_files": [] if candidates else ["LICENSE / LICENSE.txt / LICENSE.md"]}


def _python_packages(reader: _Reader) -> list[dict[str, Any]]:
    packages = []
    for directory, prefix in _python_sites():
        _checked(directory, prefix)
        for metadata in sorted(directory.glob("*.dist-info")):
            _checked(metadata, directory)
            message = BytesParser(policy=email_policy).parsebytes(reader.read(metadata / "METADATA", metadata))
            fields = {name: [str(value) for value in message.get_all(name, [])] for name in (
                "Name", "Version", "License", "License-Expression", "License-File",
                "Author", "Author-email", "Home-page", "Project-URL",
            )}
            roots: set[Path] = set()
            record = metadata / "RECORD"
            if record.exists():
                for row in csv.reader(io.StringIO(reader.read(record, metadata).decode("utf-8"))):
                    if row:
                        parts = PurePosixPath(row[0]).parts
                        if parts and all(part not in {".", ".."} for part in parts) and not PurePosixPath(row[0]).is_absolute():
                            candidate = directory / parts[0]
                            _checked(candidate, directory)
                            if candidate.is_dir() and candidate != metadata and not candidate.name.endswith((".dist-info", ".data")):
                                roots.add(candidate)
            top = metadata / "top_level.txt"
            if top.exists():
                for name in reader.read(top, metadata).decode("utf-8").splitlines():
                    if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
                        candidate = directory / name
                        _checked(candidate, directory)
                        if candidate.is_dir():
                            roots.add(candidate)
            candidates = _notice_files(metadata, recursive=True)
            for package_root in sorted(roots):
                candidates.extend(_notice_files(package_root))
            missing_declared = []
            for declared in fields["License-File"]:
                relative = _relative(str(declared))
                options = [metadata / "licenses" / relative, metadata / relative]
                options.extend(package_root / relative for package_root in sorted(roots))
                found = False
                for option in options:
                    owner = metadata if option.is_relative_to(metadata) else next(p for p in roots if option.is_relative_to(p))
                    _checked(option, owner)
                    if option.is_file():
                        candidates.append(option)
                        found = True
                        break
                if not found:
                    missing_declared.append(str(declared))
            packages.append({"ecosystem": "python", "name": str(message.get("Name", metadata.name)),
                             "version": str(message.get("Version", "")), "metadata": fields,
                             "location": metadata.relative_to(directory).as_posix(),
                             "candidates": [(p, metadata if p.is_relative_to(metadata) else next(r for r in roots if p.is_relative_to(r))) for p in candidates],
                             "missing_declared_license_files": missing_declared})
            if len(packages) > MAX_PACKAGES:
                raise ValueError("Слишком много зависимостей Python")
    return packages


def _locked_node_packages(repository: Path, store: Path, reader: _Reader) -> set[str]:
    expected = reader.read(repository / "pnpm-lock.yaml", repository)
    actual = reader.read(store / "lock.yaml", store)
    if actual != expected:
        raise ValueError("Явный pnpm store не соответствует pnpm-lock.yaml исходников")
    text = actual.decode("utf-8")
    if not re.search(r"^lockfileVersion: ['\"]?9\.0['\"]?\s*$", text, re.MULTILINE):
        raise ValueError("Нужен pnpm lockfile формата 9.0")
    if text.count("\npackages:\n") != 1 or text.count("\nsnapshots:\n") != 1:
        raise ValueError("Не найдены однозначные packages/snapshots в pnpm lockfile")
    section = text.split("\npackages:\n", 1)[1].split("\nsnapshots:\n", 1)[0]
    names = set()
    for line in section.splitlines():
        if not line.startswith("  ") or line.startswith("   ") or not line.rstrip().endswith(":"):
            continue
        key = line.strip()[:-1]
        if key.startswith("'") and key.endswith("'"):
            key = key[1:-1].replace("''", "'")
        elif key.startswith('"'):
            key = json.loads(key)
        if not isinstance(key, str) or "@" not in key or key in names:
            raise ValueError("Некорректная запись package в pnpm lockfile")
        names.add(key)
    if not names or len(names) > MAX_PACKAGES:
        raise ValueError("Некорректное число packages в pnpm lockfile")
    return names


def _node_packages(repository: Path, reader: _Reader, node_store: Path | None = None) -> list[dict[str, Any]]:
    store = repository / "node_modules/.pnpm" if node_store is None else node_store
    boundary = repository
    locked: set[str] | None = None
    if node_store is not None:
        if (not store.is_absolute() or store != store.resolve() or store.name != ".pnpm"
                or store.parent.name != "node_modules"):
            raise ValueError("Явный pnpm store должен быть каноническим физическим node_modules/.pnpm")
        boundary = store
        locked = _locked_node_packages(repository, store, reader)
    _checked(store, boundary)
    if not store.is_dir():
        raise ValueError("Для уведомлений нужен локальный pnpm virtual store")
    packages = []
    for slot in sorted(store.iterdir()):
        _checked(slot, boundary)
        if not slot.is_dir():
            continue
        modules = slot / "node_modules"
        _checked(modules, boundary)
        if not modules.is_dir():
            continue
        roots = []
        for entry in sorted(modules.iterdir()):
            _checked(entry, boundary)
            if entry.name.startswith("@") and entry.is_dir() and not entry.is_symlink():
                roots.extend(sorted(entry.iterdir()))
            else:
                roots.append(entry)
        for package_root in roots:
            _checked(package_root, boundary)
            # Зависимости slot являются ссылками на соседние slot; читаем их владельца один раз.
            if package_root.is_symlink() or not package_root.is_dir():
                continue
            package_json = package_root / "package.json"
            if not package_json.is_file():
                continue
            value = json.loads(reader.read(package_json, package_root))
            if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not isinstance(value.get("version"), str):
                raise TypeError("Некорректные name/version зависимости Node")
            if locked is not None and value["name"] + "@" + value["version"] not in locked:
                raise ValueError("Фактические name/version зависимости Node отсутствуют в pnpm lockfile")
            packages.append({"ecosystem": "node", "name": value["name"], "version": value["version"],
                             "metadata": {key: value[key] for key in ("name", "version", "license", "licenses", "author", "contributors", "homepage", "repository") if key in value},
                             "location": package_root.relative_to(repository).as_posix() if node_store is None else "pnpm-store/" + package_root.relative_to(store).as_posix(),
                             "candidates": [(p, package_root) for p in _notice_files(package_root)],
                             "missing_declared_license_files": []})
            if len(packages) > MAX_PACKAGES:
                raise ValueError("Слишком много зависимостей Node")
    return packages


def _filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value).strip(". ")[:100] or "notice"


def collect_notices(output: Path, repository: Path, *, node_store: Path | None = None) -> dict[str, object]:
    """Собрать точные metadata и тексты build environment, не заявляя лицензионный допуск."""
    repository = repository.absolute()
    output = output.absolute()
    _checked(output, repository)
    index_path = output / "third-party-notices.json"
    if index_path.exists() or index_path.is_symlink():
        raise ValueError("Уведомления уже существуют; нужен новый сборочный каталог")
    reader = _Reader()
    packages = [_interpreter()] + _python_packages(reader) + _node_packages(repository, reader, node_store)
    packages.sort(key=lambda p: (p["ecosystem"], p["name"], p["version"], p["location"]))
    if len(packages) > MAX_PACKAGES:
        raise ValueError("Превышен общий предел зависимостей")
    texts: list[tuple[str, bytes]] = []
    missing = []
    for number, package in enumerate(packages, 1):
        candidates = package.pop("candidates")
        package["license_files"] = []
        seen: set[Path] = set()
        for path, boundary in candidates:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if path.suffix.lower() in {".py", ".pyc", ".dll", ".exe", ".so", ".pyd"}:
                raise ValueError("License-File не должен указывать на код или бинарник")
            content = reader.read(path, boundary)
            decoded = content.decode("utf-8-sig")
            if any(ord(char) < 32 and char not in "\t\n\r\f" for char in decoded):
                raise ValueError("Уведомление содержит нетекстовые данные")
            relative = f"{package['ecosystem']}/{number:04d}-{_filename(package['name'])}-{_filename(package['version'])}/{len(package['license_files']) + 1:03d}-{_filename(path.name)}"
            texts.append((relative, content))
            package["license_files"].append({"source": path.relative_to(boundary).as_posix(), "path": relative,
                                              "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        package["license_file_status"] = "text_collected" if package["license_files"] else "metadata_only"
        if not package["license_files"] or package["missing_declared_license_files"]:
            missing.append({key: package[key] for key in ("ecosystem", "name", "version", "license_file_status", "missing_declared_license_files")})
    report: dict[str, object] = {"schema": "green-atlas.third-party-notices/1",
                                "scope": "CPython interpreter notice, installed Python build environment and checkout pnpm virtual store; not a legal approval or runtime-only dependency claim",
                                "packages": packages, "missing_license_files": missing}
    if node_store is not None:
        report["explicit_node_store"] = {"lockfile_sha256": hashlib.sha256(reader.read(repository / "pnpm-lock.yaml", repository)).hexdigest(),
                                         "package_versions_verified": True, "store_absolute_path_recorded": False}
    encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_TOTAL_BYTES:
        raise ValueError("Индекс уведомлений превышает предел")
    output.mkdir(parents=True, exist_ok=True)
    for relative, content in texts:
        destination = _checked(output / relative, output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(content)
    with index_path.open("xb") as stream:
        stream.write(encoded)
    return {"schema": report["schema"], "manifest": index_path.name,
            "python_packages": sum(p["ecosystem"] == "python" for p in packages),
            "node_packages": sum(p["ecosystem"] == "node" for p in packages),
            "interpreter_license_files": sum(len(p["license_files"]) for p in packages if p["ecosystem"] == "python-interpreter"),
            "license_files": len(texts), "license_bytes": sum(len(content) for _, content in texts),
            "missing_license_files": missing}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--node-store", type=Path)
    options = parser.parse_args()
    print(json.dumps(collect_notices(options.output, options.repository, node_store=options.node_store), ensure_ascii=False))
