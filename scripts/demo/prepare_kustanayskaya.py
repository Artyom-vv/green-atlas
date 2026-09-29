"""Copy the verified CAD files of Kustanayskaya for an isolated AutoCAD demo.

The input is the extracted `Исходные данные` directory supplied with the case.
This script checks known CAD checksums but never interprets DWG/DXF content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "fixtures" / "cad" / "kustanayskaya" / "manifest.json"
OUTPUT = ROOT / ".runtime" / "kustanayskaya-demo" / "input"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Подготовить рабочую копию Кустанайской")
    parser.add_argument("--source", type=Path, required=True,
                        help="Каталог Исходные данные из официального комплекта улицы")
    parser.add_argument("--destination", type=Path, default=OUTPUT,
                        help="Новая рабочая папка внутри .runtime/kustanayskaya-demo")
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.destination.resolve()
    if not source.is_dir() or source.name != "Исходные данные":
        parser.error("Укажите именно каталог Исходные данные Кустанайской")
    if (ROOT / ".runtime" / "kustanayskaya-demo").resolve() not in output.parents:
        parser.error("Рабочая копия должна находиться внутри .runtime/kustanayskaya-demo")
    if output.exists():
        parser.error(f"Рабочая копия уже существует: {output}. Не перезаписываю её")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    checked = []
    for item in manifest["files"]:
        relative = Path(item["path"]).relative_to("source")
        path = source / relative
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            parser.error(f"Отсутствует файл или неверный размер: {relative}")
        if file_sha256(path) != item["sha256"]:
            parser.error(f"Не совпадает SHA-256: {relative}")
        checked.append(relative)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    for relative in checked:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, destination)
        if file_sha256(destination) != file_sha256(source / relative):
            raise RuntimeError(f"Не удалось проверить рабочую копию: {relative}")
    main_dwg = next(path for path in checked if path.name == "05_10004141_Генплан.dwg")
    print(json.dumps({"checked_cad_files": len(checked),
                      "source_untouched": True,
                      "working_copy": str(output),
                      "open_in_autocad": str(output / main_dwg)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
