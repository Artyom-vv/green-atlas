"""Поиск установленного Blender и изоляция его запуска от frozen API.

Версия описывает найденную установку, но не подтверждает совместимость рендера.
Ресурсы растений и сохранённые подготовки принадлежат отдельным сценариям.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.shared import tool_settings
from app.shared.child_dll_search import child_dll_search


@dataclass(frozen=True)
class BlenderInstallation:
    executable: Path
    version: tuple[int, int, int] | None = None
    version_output: str | None = None


def _windows_registry_blenders() -> list[str]:
    import winreg

    paths = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(
                    hive,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\blender.exe",
                    0,
                    winreg.KEY_READ | view,
                ) as key:
                    paths.append(winreg.QueryValueEx(key, "")[0])
            except OSError:
                pass
            try:
                with winreg.OpenKey(
                    hive,
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                    0,
                    winreg.KEY_READ | view,
                ) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(
                                root, winreg.EnumKey(root, index)
                            ) as key:
                                name = winreg.QueryValueEx(key, "DisplayName")[0]
                                if not isinstance(
                                    name, str
                                ) or not name.lower().startswith("blender"):
                                    continue
                                for field in ("InstallLocation", "DisplayIcon"):
                                    try:
                                        raw = winreg.QueryValueEx(key, field)[0]
                                        if isinstance(raw, str):
                                            raw = (
                                                raw.rsplit(",", 1)[0].strip(' "')
                                                if field == "DisplayIcon"
                                                else str(Path(raw) / "blender.exe")
                                            )
                                            if Path(raw).name.lower() == "blender.exe":
                                                paths.append(raw)
                                    except OSError:
                                        pass
                        except OSError:
                            continue
            except OSError:
                pass
    return [
        path for path in paths if isinstance(path, str) and Path(path).is_absolute()
    ]


def discover_blender(*, use_settings: bool = True) -> BlenderInstallation:
    """Находит прежним маршрутом executable, не запускает и не настраивает его."""
    configured = (
        tool_settings.load().get("blender") if use_settings else None
    ) or os.environ.get("GREEN_ATLAS_BLENDER")
    candidates = (
        [configured]
        if configured
        else [
            shutil.which("blender"),
            "/Applications/Blender.app/Contents/MacOS/Blender",
        ]
    )
    if not configured and os.name == "nt":
        candidates.extend(_windows_registry_blenders())
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            directory = os.environ.get(variable)
            if directory:
                candidates.extend(
                    str(path)
                    for path in sorted(
                        (Path(directory) / "Blender Foundation").glob(
                            "Blender*/blender.exe"
                        ),
                        reverse=True,
                    )
                )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return BlenderInstallation(Path(candidate).resolve())
    raise ValueError(
        "Blender не найден. Установите его и выберите программу в разделе «Компоненты»."
    )


def probe_blender(
    installation: BlenderInstallation, *, timeout_seconds: float = 10
) -> BlenderInstallation:
    """Читает только --version; проверка сцены и моделей сюда не входит."""
    child = launch_blender(
        [str(installation.executable), "--version"],
        environment=dict(os.environ),
        stdout=subprocess.PIPE,
    )
    try:
        output, _ = child.communicate(timeout=timeout_seconds)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    if child.returncode:
        raise ValueError("Blender не смог сообщить версию установки")
    value = output.decode("utf-8", errors="replace").strip()
    # Нативная библиотека может вывести предупреждение перед строкой версии.
    match = re.search(r"^Blender (\d+)\.(\d+)\.(\d+)(?:\s|$)", value, re.MULTILINE)
    if match is None:
        raise ValueError("Установка не вернула распознаваемую версию Blender")
    version = (int(match[1]), int(match[2]), int(match[3]))
    return BlenderInstallation(installation.executable, version, value)


def blender_job_environment(directory: Path) -> dict[str, str]:
    """Отдаёт Blender временные пути своего задания, сохраняя окружение Mac."""
    environment = {
        **os.environ,
        "TMPDIR": str(directory),
        "BLENDER_USER_CONFIG": str(directory / "blender-config"),
    }
    if os.name == "nt":
        environment.update(TEMP=str(directory), TMP=str(directory))
    return environment


def _require_windows_blender_executable(path: Path) -> None:
    """Reject malformed/16-bit files before Windows can show its own modal."""
    import ctypes
    from ctypes import wintypes

    binary_type = wintypes.DWORD()
    get_binary_type = ctypes.WinDLL("kernel32", use_last_error=True).GetBinaryTypeW
    get_binary_type.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    get_binary_type.restype = wintypes.BOOL
    if not get_binary_type(str(path.resolve()), ctypes.byref(binary_type)) or binary_type.value != 6:
        raise ValueError(
            "Выберите 64-разрядный blender.exe для Windows. "
            "Этот файл не является поддерживаемой программой."
        )


def launch_blender(
    command: list[str], *, environment: dict[str, str], stdout: BinaryIO | int
) -> subprocess.Popen[bytes]:
    """Ограничивает DLL-контекст созданием Blender; ожидание остаётся у владельца."""
    child = None
    try:
        if os.name == "nt":
            _require_windows_blender_executable(Path(command[0]))
        with child_dll_search(external=True):
            child = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=subprocess.STDOUT,
                env=environment,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0
                ),
            )
    except BaseException:
        # Ошибка восстановления DLL-поиска не оставляет уже созданный Blender живым.
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        raise
    return child
