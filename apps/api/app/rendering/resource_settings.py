"""Persistent local appearance settings, independent of checkout and GUI shell."""

import json
import os
import sys
from pathlib import Path

from app.shared import tool_settings

_SCHEMA = "green-atlas.render-resources/1"
_FIELDS = {
    "GREEN_ATLAS_BOTANIQ_ROOT": "botaniq_root",
    "GREEN_ATLAS_RENDER_PBR_ROOT": "pbr_root",
}
_CONFIG_ENV = "GREEN_ATLAS_RENDER_RESOURCES_CONFIG"


def settings_path() -> Path:
    if sys.platform == "darwin":
        directory = Path.home() / "Library/Application Support/Green Atlas"
    elif sys.platform == "win32":
        directory = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Green Atlas"
        )
    else:
        directory = Path.home() / ".local/share/green-atlas"
    return directory / "render-resources.json"


def _path(value: object, label: str, base: Path | None = None) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"Некорректный путь ресурсов оформления: {label}")
    try:
        path = Path(value).expanduser()
        if base is not None and not path.is_absolute():
            path = base / path
        return path.resolve()
    except (OSError, RuntimeError, ValueError):
        raise ValueError(f"Некорректный путь ресурсов оформления: {label}") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate setting")
        result[key] = value
    return result


def _read_settings() -> tuple[Path, dict[str, object]] | None:
    explicit = _CONFIG_ENV in os.environ
    path = _path(os.environ[_CONFIG_ENV], _CONFIG_ENV) if explicit else settings_path()
    try:
        with path.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("settings too large")
        data = json.loads(raw, object_pairs_hook=_unique_object)
        if (
            not isinstance(data, dict)
            or set(data) != {"schema", *_FIELDS.values()}
            or data["schema"] != _SCHEMA
        ):
            raise ValueError("invalid schema")
        # Validate the complete document even when one root is overridden.
        for field in _FIELDS.values():
            _path(data[field], field, path.parent)
        return path, data
    except FileNotFoundError:
        if not explicit and not path.is_symlink():
            return None
    except (OSError, ValueError, UnicodeError):
        pass
    raise ValueError(
        f"Не удалось прочитать настройки ресурсов оформления: {path}. "
        f"Ожидается {_SCHEMA}, botaniq_root и pbr_root"
    ) from None


def configured_resource_root(variable: str) -> Path | None:
    selected = tool_settings.load().get(_FIELDS[variable])
    if selected:
        root = Path(selected)
        if not root.is_dir():
            raise ValueError("Каталог ресурсов оформления не найден: " + str(root))
        return root
    settings = _read_settings()
    if variable in os.environ:
        root = _path(os.environ[variable], variable)
        label = variable
    elif settings is not None:
        path, data = settings
        field = _FIELDS[variable]
        root = _path(data[field], field, path.parent)
        label = f"{path.name}: {field}"
    else:
        return None
    if not root.is_dir():
        raise ValueError(f"Каталог ресурсов оформления не найден: {label}")
    return root
