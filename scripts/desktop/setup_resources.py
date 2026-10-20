"""Данные проверяющего модуля внутри самого frozen Setup, отдельно от payload."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Protocol


class Archive(Protocol):
    toc: Mapping[str, object]

    def extract(self, name: str) -> bytes | None: ...


def resources(repository: Path) -> dict[str, Path]:
    app = repository / "apps/api/app"
    tree = ast.parse((app / "distribution/preflight.py").read_text(encoding="utf-8"))
    definitions = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "RUNTIME_RESOURCES" for target in node.targets)
    ]
    if len(definitions) != 1:
        raise ValueError("Нужен единственный список RUNTIME_RESOURCES")
    names = ast.literal_eval(definitions[0])
    if not isinstance(names, tuple) or not names:
        raise ValueError("RUNTIME_RESOURCES должен быть непустым tuple")
    result: dict[str, Path] = {}
    for name in names:
        if not isinstance(name, str):
            raise TypeError("Имя ресурса должно быть строкой")
        relative = PurePosixPath(name)
        if (
            not relative.parts
            or relative.is_absolute()
            or str(relative) != name
            or ".." in relative.parts
            or "\\" in name
            or ":" in name
            or "app/" + name in result
        ):
            raise ValueError("Неканонический или повторённый ресурс: " + name)
        source = app.joinpath(*relative.parts)
        for part in (source, *source.parents):
            if part == repository:
                break
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Ресурс Setup содержит ссылку: " + name)
        if not source.is_file() or source.stat().st_size == 0:
            raise ValueError("Ресурс Setup отсутствует или пуст: " + name)
        result["app/" + name] = source
    return result


def verify_resources(repository: Path, archive: Archive) -> dict[str, dict[str, str | int]]:
    archive_names = {}
    for name in archive.toc:
        normalized = name.replace("\\", "/")
        if normalized in archive_names:
            raise ValueError("Повторён путь в архиве frozen Setup: " + normalized)
        archive_names[normalized] = name
    verified: dict[str, dict[str, str | int]] = {}
    for name, source in resources(repository).items():
        try:
            actual = archive.extract(archive_names[name])
        except KeyError as error:
            raise ValueError("В frozen Setup отсутствует ресурс: " + name) from error
        expected = source.read_bytes()
        if actual != expected:
            raise ValueError("Ресурс frozen Setup отличается от исходника: " + name)
        verified[name] = {"bytes": len(expected), "sha256": hashlib.sha256(expected).hexdigest()}
    return verified


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("arguments", "verify"))
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--setup", type=Path)
    args = parser.parse_args()
    if args.mode == "arguments":
        print(json.dumps([
            value
            for name, source in resources(args.repository).items()
            for value in ("--add-data", str(source) + ";" + str(PurePosixPath(name).parent))
        ]))
    else:
        from PyInstaller.archive.readers import CArchiveReader

        if args.setup is None:
            parser.error("verify требует --setup")
        verified = verify_resources(args.repository, CArchiveReader(str(args.setup)))
        print(json.dumps({"schema": "green-atlas.setup-resources/1", "resources": verified}))


if __name__ == "__main__":
    main()
