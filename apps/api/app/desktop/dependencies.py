"""Discovery and validation shared by desktop settings and the native Setup."""

import json
import os
import subprocess
from pathlib import Path

from app.shared import tool_settings

LABELS = {
    "autocad_root": "AutoCAD 2027",
    "autocad_template": "Шаблон AutoCAD",
    "blender": "Blender",
    "botaniq_root": "Botaniq",
    "pbr_root": "PBR",
}


def appearance_files() -> tuple[set[str], set[str]]:
    profile = json.loads(
        (Path(__file__).parents[1] / "rendering/native_style.json").read_bytes()
    )
    models = {
        "blends_280/Library_Botaniq_Materials.blend",
        profile["shrub_asset"],
        *profile["grass_assets"],
    }
    for variants in profile["tree_assets"].values():
        models.update(variants)
    textures = {
        name for roles in profile["materials"].values() for name in roles.values()
    }
    return models, textures


def library_root(path: Path, field: str) -> Path:
    """Accept the library itself or a selected parent, with bounded traversal."""
    candidates = [path]
    if field == "botaniq_root":
        candidates.extend((path / "botaniq_full", path / "botaniq"))
        if path.is_dir():
            candidates.extend(sorted(path.glob("botaniq*/botaniq_full")))
        marker = "blends_280/Library_Botaniq_Materials.blend"
        return next(
            (candidate for candidate in candidates if (candidate / marker).is_file()),
            path,
        )
    required = appearance_files()[1]
    candidates.extend((path / "pbr", path / "PBR", path / "textures/pbr"))
    return next(
        (
            candidate
            for candidate in candidates
            if all((candidate / name).is_file() for name in required)
        ),
        path,
    )


def discover() -> dict[str, object]:
    warning = ""
    try:
        values = tool_settings.load()
    except ValueError as error:
        values, warning = dict.fromkeys(tool_settings.FIELDS, ""), str(error)
    from app.native_query.transport.autocad_installation import (
        resolve_autocad_installation,
    )
    from app.rendering.blender_runtime import discover_blender
    from app.rendering.resource_settings import configured_resource_root

    if not values["autocad_root"] or not Path(values["autocad_root"]).is_dir():
        try:
            cad = resolve_autocad_installation(use_settings=False)
            values["autocad_root"] = str(cad.root)
            if not values["autocad_template"]:
                values["autocad_template"] = str(cad.bootstrap_template)
        except (OSError, ValueError):
            # Discovery must still offer a registered root before first AutoCAD
            # launch, when the per-user template may not exist yet.
            if os.name == "nt":
                from app.native_query.transport.autocad_installation import (
                    _registered_windows_installations,
                )

                roots = [
                    p
                    for p in _registered_windows_installations()
                    if (p / "acad.exe").is_file()
                    and (p / "accoreconsole.exe").is_file()
                ]
                if len(roots) == 1:
                    values["autocad_root"] = str(roots[0])
    if not values["blender"] or not Path(values["blender"]).is_file():
        try:
            values["blender"] = str(discover_blender(use_settings=False).executable)
        except (OSError, ValueError):
            pass
    for variable, field in (
        ("GREEN_ATLAS_BOTANIQ_ROOT", "botaniq_root"),
        ("GREEN_ATLAS_RENDER_PBR_ROOT", "pbr_root"),
    ):
        if not values[field]:
            try:
                root = configured_resource_root(variable)
                if root is not None:
                    values[field] = str(root)
            except (OSError, ValueError):
                pass
    if not values["botaniq_root"] and os.name == "nt":
        base = (
            Path(os.environ.get("APPDATA", str(Path.home())))
            / "Blender Foundation/Blender"
        )
        candidates = [
            *base.glob("*/scripts/addons/botaniq*"),
            *base.glob("*/extensions/*/botaniq*"),
        ]
        matches = {
            str(library_root(p, "botaniq_root"))
            for p in candidates
            if library_root(p, "botaniq_root") != p
            or (p / "blends_280/Library_Botaniq_Materials.blend").is_file()
        }
        if len(matches) == 1:
            values["botaniq_root"] = matches.pop()
    if not values["pbr_root"] and values["botaniq_root"]:
        candidate = library_root(Path(values["botaniq_root"]).parent, "pbr_root")
        if candidate != Path(values["botaniq_root"]).parent:
            values["pbr_root"] = str(candidate)
    return {"paths": values, "warning": warning, "checks": {}}


def check(value: object, *, probe: bool = True) -> dict[str, object]:
    values = tool_settings.normalize(value)
    checks: dict[str, dict[str, object]] = {}
    for field in values:
        raw = values[field]
        if not raw:
            checks[field] = {
                "ok": True,
                "state": "deferred",
                "message": "Не подключено. Можно настроить позже.",
            }
            continue
        path = Path(raw)
        try:
            if field == "autocad_root":
                from app.native_query.transport.autocad_installation import (
                    resolve_autocad_installation,
                )

                installation = resolve_autocad_installation(
                    root=path,
                    template=Path(values["autocad_template"])
                    if values["autocad_template"]
                    else None,
                    use_settings=False,
                )
                values["autocad_template"] = str(installation.bootstrap_template)
                detail = "AutoCAD 2027 найден. Работа с чертежом проверяется в AutoCAD."
            elif field == "blender":
                from app.rendering.blender_runtime import (
                    BlenderInstallation,
                    probe_blender,
                )

                if not path.is_file():
                    raise ValueError("Выберите исполняемый файл Blender")
                installed = probe_blender(BlenderInstallation(path)) if probe else None
                detail = (
                    "Blender " + ".".join(map(str, installed.version))
                    if installed and installed.version
                    else "Файл Blender найден"
                )
            elif field == "autocad_template":
                if not path.is_file() or path.suffix.lower() != ".dwt":
                    raise ValueError("Выберите существующий шаблон .dwt")
                detail = "Шаблон найден"
            else:
                path = library_root(path, field)
                required = appearance_files()[0 if field == "botaniq_root" else 1]
                missing = sorted(
                    name for name in required if not (path / name).is_file()
                )
                if missing:
                    raise ValueError(
                        "Не найдены файлы: "
                        + ", ".join(missing[:3])
                        + (f"; ещё {len(missing) - 3}" if len(missing) > 3 else "")
                    )
                if field == "botaniq_root" and not any(
                    p.is_file() for p in (path / "textures").rglob("*")
                ):
                    raise ValueError("Не найдены текстуры Botaniq в папке textures")
                values[field] = str(path)
                detail = "Нужные файлы библиотеки найдены. Полный рендер проверяется отдельно."
            checks[field] = {"ok": True, "state": "found", "message": detail}
        except (ValueError, OSError, TimeoutError, subprocess.TimeoutExpired) as error:
            detail = str(error)
            if field == "autocad_root" and "GREEN_ATLAS_AUTOCAD_TEMPLATE" in detail:
                detail = (
                    "Выберите доступный шаблон AutoCAD .dwt в поле «Шаблон AutoCAD»."
                )
            elif field == "autocad_root" and "GREEN_ATLAS_AUTOCAD_ROOT" in detail:
                detail = "Выберите папку зарегистрированного AutoCAD 2027 с файлами acad.exe и accoreconsole.exe."
            checks[field] = {"ok": False, "state": "invalid", "message": detail}
    return {
        "paths": values,
        "checks": checks,
        "valid": all(item["ok"] for item in checks.values()),
    }


def save(value: object) -> dict[str, object]:
    result = check(value)
    if result["valid"]:
        tool_settings.save(result["paths"])
    return result
