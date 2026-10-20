"""Платформенный поиск установленного AutoCAD Core и шаблона без запуска CAD.

Дескриптор описывает установку, а не квалификацию подготовки или выпуска.
"""

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.shared import tool_settings

DesktopPlatform = Literal["macos", "windows"]
_NOT_FOUND = (
    "Не найден совместимый AutoCAD 2027 с модулем выпуска и шаблоном acadiso.dwt. "
    "Установите полную версию AutoCAD либо выберите папку программы в разделе «Компоненты»."
)


@dataclass(frozen=True)
class AutoCADInstallation:
    platform: DesktopPlatform
    root: Path
    core_executable: Path
    bootstrap_template: Path
    identity: str
    release: str = "2027"


def desktop_platform() -> DesktopPlatform:
    """Выбирает реализованный формат установки без неявной Mac-подмены."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    raise ValueError("Платформа установки AutoCAD не поддерживается")


def resolve_autocad_installation(
    *,
    platform: DesktopPlatform | None = None,
    root: Path | None = None,
    template: Path | None = None,
    use_settings: bool = True,
) -> AutoCADInstallation:
    """Проверяет явный корень или установленный продукт; неверный override не заменяет."""
    selected = platform or desktop_platform()
    saved = tool_settings.load() if use_settings else {}
    configured = saved.get("autocad_root") or os.environ.get("GREEN_ATLAS_AUTOCAD_ROOT")
    explicit = root if root is not None else Path(configured) if configured else None
    if selected == "windows":
        chosen_template = template or (
            Path(saved["autocad_template"]) if saved.get("autocad_template") else None
        )
        return _windows_installation(explicit, chosen_template)
    if selected != "macos":
        raise ValueError("Платформа установки AutoCAD не поддерживается")
    candidates = (
        [explicit]
        if explicit is not None
        else [
            Path("/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app"),
            Path("/Applications/AutoCAD 2027.app"),
            Path.home() / "Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app",
            Path.home() / "Applications/AutoCAD 2027.app",
        ]
    )
    for candidate in candidates:
        found = _mac_installation(candidate)
        if found is not None:
            return found
    if explicit is None and sys.platform == "darwin":
        try:
            result = subprocess.run(
                [
                    "/usr/bin/mdfind",
                    "kMDItemCFBundleIdentifier == 'com.autodesk.AutoCAD2027'",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    found = _mac_installation(Path(line))
                    if found is not None:
                        return found
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise ValueError(_NOT_FOUND)


def _mac_installation(app: Path) -> AutoCADInstallation | None:
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, ValueError):
        return None
    if info.get("CFBundleIdentifier") != "com.autodesk.AutoCAD2027":
        return None
    core = app / "Contents/Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole"
    templates = app / "Contents/Resources/UserDataCache"
    preferred = templates / "en-us/Template/acadiso.dwt"
    template = next(
        (
            path
            for path in (preferred, *sorted(templates.glob("*/Template/acadiso.dwt")))
            if path.is_file()
        ),
        None,
    )
    if core.is_file() and os.access(core, os.X_OK) and template is not None:
        return AutoCADInstallation(
            "macos", app, core, template, "com.autodesk.AutoCAD2027"
        )
    return None


def _registered_windows_installations() -> dict[Path, str]:
    """Читает 64-битную регистрацию R26.0; имя каталога не доказывает версию CAD."""
    if sys.platform != "win32":
        raise ValueError("Регистрация установки Windows проверяется на Windows")
    import winreg

    candidates: dict[Path, str] = {}
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Autodesk\AutoCAD\R26.0",
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        ) as registry:
            for index in range(winreg.QueryInfoKey(registry)[0]):
                name = winreg.EnumKey(registry, index)
                with winreg.OpenKey(registry, name) as product:
                    try:
                        value = winreg.QueryValueEx(product, "AcadLocation")[0]
                    except FileNotFoundError:
                        continue
                    if isinstance(value, str) and Path(value).is_absolute():
                        candidates[Path(value).absolute()] = (
                            "Autodesk.AutoCAD:R26.0/" + name
                        )
    except FileNotFoundError:
        pass
    return candidates


def _windows_installation(
    explicit: Path | None, chosen_template: Path | None = None
) -> AutoCADInstallation:
    registered = _registered_windows_installations()
    if explicit is not None:
        selected = explicit.absolute()
        candidates = {selected: registered[selected]} if selected in registered else {}
    else:
        candidates = registered
    found = {
        path: identity
        for path, identity in candidates.items()
        if (path / "acad.exe").is_file() and (path / "accoreconsole.exe").is_file()
    }
    if not found:
        raise ValueError(_NOT_FOUND)
    if len(found) != 1:
        raise ValueError(
            "Найдено несколько AutoCAD 2027. Выберите папку программы в разделе «Компоненты»."
        )
    app, identity = next(iter(found.items()))
    configured = chosen_template or os.environ.get("GREEN_ATLAS_AUTOCAD_TEMPLATE")
    if configured:
        template = Path(configured).absolute()
        if not template.is_file():
            if chosen_template is not None:
                raise ValueError(
                    "Выбранный шаблон AutoCAD недоступен. Выберите файл .dwt в разделе «Компоненты»."
                )
            raise ValueError("Не найден шаблон GREEN_ATLAS_AUTOCAD_TEMPLATE")
    else:
        local = os.environ.get("LOCALAPPDATA", "")
        if not local or not Path(local).is_absolute():
            raise ValueError("Не определён каталог профиля AutoCAD")
        templates = sorted(
            (Path(local) / "Autodesk/AutoCAD 2027/R26.0").glob("*/Template/acadiso.dwt")
        )
        templates = [path for path in templates if path.is_file()]
        if not templates:
            templates = sorted((app / "UserDataCache").glob("*/Template/acadiso.dwt"))
            templates = [path for path in templates if path.is_file()]
        if len(templates) != 1:
            raise ValueError("Выберите шаблон AutoCAD .dwt в разделе «Компоненты».")
        template = templates[0]
    return AutoCADInstallation(
        "windows", app, app / "accoreconsole.exe", template, identity
    )
