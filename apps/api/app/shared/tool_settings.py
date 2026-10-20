"""User-selected external tools; a single atomic document outside project data."""

import json
import os
import sys
import tempfile
from pathlib import Path

FIELDS = ("autocad_root", "autocad_template", "blender", "botaniq_root", "pbr_root")
SCHEMA = "green-atlas.external-tools/1"


def settings_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Green Atlas"
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support/Green Atlas"
    else:
        base = Path.home() / ".local/share/green-atlas"
    return base / "external-tools.json"


def normalize(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise ValueError("Ожидаются пути AutoCAD, шаблона, Blender, Botaniq и PBR")
    result = {}
    for field in FIELDS:
        raw = value[field]
        if not isinstance(raw, str) or len(raw) > 8192 or any(ord(c) < 32 for c in raw):
            raise ValueError("Некорректный путь: " + field)
        raw = raw.strip()
        if raw:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                raise ValueError("Выберите полный путь: " + field)
            raw = str(path.resolve())
        result[field] = raw
    if len(json.dumps({"schema": SCHEMA, **result}, ensure_ascii=False).encode("utf-8")) > 65536:
        raise ValueError("Слишком длинные пути компонентов")
    return result


def load() -> dict[str, str]:
    path = settings_path()
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
    except FileNotFoundError:
        return dict.fromkeys(FIELDS, "")
    try:
        value = json.loads(raw)
        if (
            len(raw) > 65536
            or not isinstance(value, dict)
            or value.pop("schema", None) != SCHEMA
        ):
            raise ValueError()
        return normalize(value)
    except (ValueError, UnicodeError, OSError):
        raise ValueError(
            "Настройки компонентов повреждены. Откройте настройки и сохраните пути заново."
        ) from None


def save(value: dict[str, str]) -> None:
    value = normalize(value)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".external-tools-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"schema": SCHEMA, **value}, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
