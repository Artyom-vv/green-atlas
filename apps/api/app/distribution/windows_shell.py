"""Win32 оболочка установки: текущий пользователь, ярлыки и HKCU без PowerShell."""

from __future__ import annotations

import ctypes
import os
import re
import stat
import sys
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.distribution.windows_install import plain_directory
from app.distribution.windows_setup_guard import current_user_sid

UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GreenAtlas"


def native_uninstaller(local: Path, executable: Path) -> Path:
    """Нативное обслуживание разрешено только в собственном фиксированном каталоге."""
    expected = local.absolute() / "GreenAtlas/Installer/Wizard"
    path = executable.absolute()
    if path.parent != expected or not re.fullmatch(r"unins[0-9]{3}\.exe", path.name):
        raise ValueError("Неизвестный путь мастера удаления Green Atlas")
    plain_directory(expected)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Мастер удаления содержит ссылку")
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("Мастер удаления не является Windows-программой")
    return path


def require_windows_user() -> None:
    if os.name != "nt":
        raise RuntimeError("Установщик предназначен только для Windows")
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise ValueError("Запустите установщик от обычного пользователя без повышения прав")


def setup_mutex_name(sid: bytes) -> str:
    """Одинаковый SID получает один mutex во всех сеансах, разные SID независимы."""
    if not 8 <= len(sid) <= 68:
        raise ValueError("Некорректный SID установки")
    return "Global\\GreenAtlas.CurrentUser.Setup." + sid.hex()


@contextmanager
def installation_mutex() -> Iterator[None]:
    require_windows_user()
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    kernel.ReleaseMutex.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, True, setup_mutex_name(current_user_sid()))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    existed = ctypes.get_last_error() == 183
    try:
        if existed:
            raise ValueError("Другой установщик Green Atlas уже работает")
        yield
    finally:
        if not existed:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def message(text: str, *, confirm: bool = False, error: bool = False) -> bool:
    flags = 0x24 if confirm else (0x10 if error else 0x40)
    return ctypes.windll.user32.MessageBoxW(None, text, "Green Atlas", flags) == (6 if confirm else 1)


def write_diagnostic(text: str, *, error: bool = False) -> bool:
    """Передать UTF-8 JSON в унаследованный pipe даже у windowed frozen Setup."""
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        try:
            stream.write(text)
            stream.flush()
            return True
        except (OSError, ValueError):
            pass
    if os.name != "nt":
        return False
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = (wintypes.DWORD,)
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.WriteFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    kernel.WriteFile.restype = wintypes.BOOL
    handle = kernel.GetStdHandle(0xfffffff4 if error else 0xfffffff5)
    if not handle or handle == ctypes.c_void_p(-1).value:
        # Двойной щелчок по GUI setup обычно не имеет console/pipe; консоль не создаём.
        return False
    payload = text.encode("utf-8")
    data = ctypes.create_string_buffer(payload)
    offset = 0
    while offset < len(payload):
        written = wintypes.DWORD()
        count = min(len(payload) - offset, 1024 * 1024)
        if not kernel.WriteFile(handle, ctypes.byref(data, offset), count, ctypes.byref(written), None):
            return False
        if not 0 < written.value <= count:
            return False
        offset += written.value
    return True


def create_shortcut(path: Path, executable: Path, *, arguments: str = "") -> None:
    """Создать IShellLinkW через COM; команды оболочки и скрипты не используются."""
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    def guid(value: str) -> Guid:
        return Guid.from_buffer_copy(uuid.UUID(value).bytes_le)

    def invoke(pointer: ctypes.c_void_p, index: int, types: tuple[Any, ...], *args: object) -> int:
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])
        result = function(pointer, *args)
        if result < 0:
            raise OSError(f"Ошибка COM ярлыка: 0x{result & 0xffffffff:08x}")
        return result

    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = (ctypes.POINTER(Guid), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p))
    ole.CoCreateInstance.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0:
        raise OSError("Не удалось открыть COM для ярлыка")
    link = ctypes.c_void_p()
    persist = ctypes.c_void_p()
    try:
        clsid = guid("00021401-0000-0000-c000-000000000046")
        iid = guid("000214f9-0000-0000-c000-000000000046")
        if ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(link)) < 0:
            raise OSError("Не удалось создать IShellLinkW")
        invoke(link, 20, (wintypes.LPCWSTR,), str(executable))
        invoke(link, 9, (wintypes.LPCWSTR,), str(executable.parent))
        invoke(link, 11, (wintypes.LPCWSTR,), arguments)
        invoke(link, 7, (wintypes.LPCWSTR,), "Green Atlas")
        persist_iid = guid("0000010b-0000-0000-c000-000000000046")
        invoke(link, 0, (ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)), ctypes.byref(persist_iid), ctypes.byref(persist))
        invoke(persist, 6, (wintypes.LPCWSTR, wintypes.BOOL), str(path), True)
    finally:
        if persist.value:
            invoke(persist, 2, ())
        if link.value:
            invoke(link, 2, ())
        ole.CoUninitialize()


class WindowsShell:
    def __init__(self, roaming: Path, setup: Path, *, uninstaller: Path | None = None) -> None:
        self.shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/Green Atlas.lnk"
        self.rollback_shortcut = self.shortcut.with_name("Green Atlas, предыдущая версия.lnk")
        self.setup = setup
        self.uninstaller = uninstaller

    def _transaction(self, operation: Callable[[], None]) -> None:
        import winreg

        snapshots = {}
        for path in (self.shortcut, self.rollback_shortcut):
            plain_directory(path.parent, create=True)
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValueError("Ярлык Green Atlas содержит ссылку или чужой тип")
            snapshots[path] = path.read_bytes() if path.exists() else None
        registry: list[tuple[str, Any, int]] = []
        existed = False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                existed = True
                index = 0
                while True:
                    try:
                        registry.append(winreg.EnumValue(key, index))
                        index += 1
                    except OSError:
                        break
        except FileNotFoundError:
            pass
        try:
            operation()
        except BaseException:
            for path, data in snapshots.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(data)
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
            except FileNotFoundError:
                pass
            if existed:
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                    for name, registry_data, kind in registry:
                        winreg.SetValueEx(key, name, 0, kind, registry_data)
            raise

    def publish(self, installed: Path, version: str) -> None:
        import winreg

        def operation() -> None:
            host = installed / "Contents/Windows/GreenAtlasDesktop.exe"
            create_shortcut(self.shortcut, host)
            # Successful updates retain no old program payload. Crash recovery
            # uses the install journal; a historical downgrade shortcut would
            # promise a package that has intentionally been removed.
            self.rollback_shortcut.unlink(missing_ok=True)
            uninstaller = self.uninstaller
            # Вызов сохранённого backend (например, откат) сохраняет нативный вход.
            if uninstaller is None:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as previous:
                        value, _ = winreg.QueryValueEx(previous, "NativeUninstaller")
                    uninstaller = native_uninstaller(Path(os.environ["LOCALAPPDATA"]), Path(value))
                except FileNotFoundError:
                    pass
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
                values = {"DisplayName": "Green Atlas", "DisplayVersion": version,
                          "InstallLocation": str(installed), "Publisher": "Green Atlas",
                          "DisplayIcon": str(host), "BackendSetup": str(self.setup),
                          "UninstallString": f'"{self.setup}" --uninstall',
                          "QuietUninstallString": f'"{self.setup}" --uninstall --quiet'}
                if uninstaller is not None:
                    values.update(NativeUninstaller=str(uninstaller), UninstallString=f'"{uninstaller}"',
                                  QuietUninstallString=f'"{uninstaller}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART')
                for name, value in values.items():
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
                for name in ("NoModify", "NoRepair"):
                    winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)

        self._transaction(operation)

    def remove(self) -> None:
        import winreg

        def operation() -> None:
            self.shortcut.unlink(missing_ok=True)
            self.rollback_shortcut.unlink(missing_ok=True)
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
            except FileNotFoundError:
                pass

        self._transaction(operation)
