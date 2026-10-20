"""Настоящий native мастер в новом обычном CI аккаунте: окна, кадры и обслуживание."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps/api"))


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(value: Any, reason: str) -> None:
    if not value:
        raise RuntimeError(reason)


def token_environment() -> dict[str, str]:
    """Окружение настоящего текущего SID, без наследования профиля CI runner."""
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    advapi.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    advapi.OpenProcessToken.restype = w.BOOL
    userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.BOOL]
    userenv.CreateEnvironmentBlock.restype = w.BOOL
    userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
    token = w.HANDLE()
    require(advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x000A, ctypes.byref(token)),
            "Не открыт собственный token для окружения")
    block = ctypes.c_void_p()
    try:
        require(userenv.CreateEnvironmentBlock(ctypes.byref(block), token, False),
                "Не создано окружение собственного token")
        try:
            result: dict[str, str] = {}
            cursor = block.value
            require(cursor, "Пустой environment block")
            for _ in range(4096):
                entry = ctypes.wstring_at(cursor)
                if not entry:
                    return result
                name, separator, value = entry.partition("=")
                if name and separator:
                    result[name.upper()] = value
                cursor += (len(entry.encode("utf-16-le")) // 2 + 1) * 2
            raise RuntimeError("Слишком большой environment block")
        finally:
            userenv.DestroyEnvironmentBlock(block)
    finally:
        kernel.CloseHandle(token)


def known_folder(identifier: str) -> Path:
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32")
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    guid = ctypes.create_string_buffer(uuid.UUID(identifier).bytes_le)
    result = ctypes.c_void_p()
    # Новый неинтерактивный CI профиль может ещё не иметь Desktop. Windows сама
    # создаёт именно известную папку текущего token, без подмены USERPROFILE.
    status = shell.SHGetKnownFolderPath(guid, 0x8000, None, ctypes.byref(result))
    require(status == 0, f"Не найден known folder {identifier}: HRESULT 0x{status & 0xffffffff:08x}")
    try:
        return Path(ctypes.wstring_at(result))
    finally:
        ole.CoTaskMemFree(result)


class NativeUI:
    def __init__(self) -> None:
        from ctypes import wintypes as w

        self.w = w
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "EnumWindows": ([self.callback, w.LPARAM], w.BOOL),
            "EnumChildWindows": ([w.HWND, self.callback, w.LPARAM], w.BOOL),
            "GetWindowTextW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetClassNameW": ([w.HWND, w.LPWSTR, ctypes.c_int], ctypes.c_int),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "GetDlgItem": ([w.HWND, ctypes.c_int], w.HWND),
            "GetDlgCtrlID": ([w.HWND], ctypes.c_int),
            "SetWindowTextW": ([w.HWND, w.LPCWSTR], w.BOOL),
            "IsWindowVisible": ([w.HWND], w.BOOL),
            "IsWindow": ([w.HWND], w.BOOL),
            "IsWindowEnabled": ([w.HWND], w.BOOL),
            "PostMessageW": ([w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL),
            "SendMessageTimeoutW": ([w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT,
                                      ctypes.POINTER(ctypes.c_size_t)], w.LPARAM),
            "GetWindowRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "PrintWindow": ([w.HWND, w.HDC, w.UINT], w.BOOL),
            "GetDC": ([w.HWND], w.HDC),
            "ReleaseDC": ([w.HWND, w.HDC], ctypes.c_int),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user, name)
            function.argtypes, function.restype = args, result
        signatures = {
            "CreateCompatibleDC": ([w.HDC], w.HDC),
            "CreateDIBSection": ([w.HDC, ctypes.c_void_p, w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD], w.HBITMAP),
            "SelectObject": ([w.HDC, w.HANDLE], w.HANDLE),
            "DeleteObject": ([w.HANDLE], w.BOOL),
            "DeleteDC": ([w.HDC], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.gdi, name)
            function.argtypes, function.restype = args, result

    def text(self, window: int, *, kind: bool = False) -> str:
        # Имена классов короткие; 32768 UTF-16 символов переполняют внутренний
        # UNICODE_STRING.Length Win32 (байтовое поле USHORT).
        value = ctypes.create_unicode_buffer(256 if kind else 32768)
        (self.user.GetClassNameW if kind else self.user.GetWindowTextW)(window, value, len(value))
        if not kind and not value.value:
            # GetWindowText не читает Edit/Memo другого процесса. Только bounded
            # синхронное чтение собственного проверяемого окна, без изменения UI.
            result = ctypes.c_size_t()
            self.user.SendMessageTimeoutW(window, 0x000D, len(value), ctypes.addressof(value),
                                          0x0002, 200, ctypes.byref(result))
        return value.value

    def windows(self, parent: int | None = None) -> list[int]:
        result = []
        callback = self.callback(lambda window, _: result.append(window) is None)
        if parent is None:
            self.user.EnumWindows(callback, 0)
        else:
            self.user.EnumChildWindows(parent, callback, 0)
        return result

    def pid(self, window: int) -> int:
        pid = self.w.DWORD()
        self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        return pid.value

    def content(self, window: int) -> str:
        return "\n".join(self.text(child) for child in [window, *self.windows(window)] if self.user.IsWindowVisible(child))

    def click(self, window: int, *prefixes: str) -> None:
        for child in self.windows(window):
            text = self.text(child).replace("&", "").strip()
            if self.user.IsWindowVisible(child) and self.user.IsWindowEnabled(child) and text.startswith(prefixes):
                require(self.user.PostMessageW(child, 0xF5, 0, 0), "Не отправлен native BM_CLICK")
                return
        raise RuntimeError("Не найдена native кнопка: " + repr(prefixes) + ": " + self.content(window)[:1200])

    def frame(self, window: int, target: Path) -> None:
        rect = self.w.RECT()
        require(self.user.GetWindowRect(window, ctypes.byref(rect)), "Нет размеров окна")
        width, height = rect.right - rect.left, rect.bottom - rect.top
        require(100 <= width <= 4096 and 80 <= height <= 2160, "Неожиданный размер окна")
        screen = self.user.GetDC(None)
        dc = self.gdi.CreateCompatibleDC(screen)
        bits = ctypes.c_void_p()
        header = ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0))
        bitmap = self.gdi.CreateDIBSection(screen, header, 0, ctypes.byref(bits), None, 0)
        require(dc and bitmap and bits.value, "Не создан GDI кадр")
        previous = self.gdi.SelectObject(dc, bitmap)
        try:
            require(self.user.PrintWindow(window, dc, 2), "PrintWindow не отрисовал окно")
            raw = ctypes.string_at(bits, width * height * 4)
            require(len(set(raw[::4])) > 5, "Кадр пустой")
            rows = bytearray()
            for y in range(height):
                rows.append(0)
                line = raw[y * width * 4:(y + 1) * width * 4]
                rgb = bytearray(width * 3)
                rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]
                rows.extend(rgb)

            def chunk(kind: bytes, data: bytes) -> bytes:
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

            target.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                               + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
        finally:
            self.gdi.SelectObject(dc, previous)
            self.gdi.DeleteObject(bitmap)
            self.gdi.DeleteDC(dc)
            self.user.ReleaseDC(None, screen)


def wait_for(check: Any, *, timeout: float = 180, reason: str = "Ожидание окна истекло") -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise RuntimeError(reason)


def main() -> int:
    import winreg

    import psutil
    from app.distribution.windows_install import load_owned_bundle
    from app.distribution.windows_setup_guard import current_user_sid
    from app.distribution.windows_shell import UNINSTALL_KEY, require_windows_user

    from scripts.desktop.windows_product_install_smoke import (
        shortcut_target,
        verify_hkcu,
    )

    parser = argparse.ArgumentParser()
    for name in ("setup", "setup-receipt", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-sid-hex", required=True)
    args = parser.parse_args()
    require_windows_user()
    require(current_user_sid().hex() == args.expected_sid_hex, "Не тот ordinary SID")
    require(args.output.absolute().is_relative_to(REPO / ".runtime"), "Вывод только внутри CI checkout")
    verify_hkcu(current_user_sid())
    output = args.output.absolute()
    output.mkdir()
    report: dict[str, Any] = {"schema": "green-atlas.windows-native-wizard-ci/1", "passed": False,
        "ordinary_user_token": True, "product_qualified": False, "autocad_executed": False,
        "blender_executed": False, "screenshots": [], "stages": []}
    owned: list[subprocess.Popen[bytes]] = []
    ui: NativeUI | None = None
    try:
        build = json.loads(args.setup_receipt.read_text(encoding="utf-8-sig"))
        require(sha(args.setup) == build["sha256"], "Wizard не совпадает с sealed receipt")
        # Start-Process -Credential загружает HKCU, но может наследовать USERPROFILE
        # административного runner. Получаем значения из Windows для проверенного SID.
        profile_environment = token_environment()
        profile_home = Path(profile_environment.get("USERPROFILE", ""))
        require(profile_home.is_absolute() and profile_home.name.startswith("ga-ci-"),
                "Windows не загрузила профиль одноразового SID")
        os.environ.update(profile_environment)
        roaming = known_folder("3EB685DB-65F9-4CF6-A03A-E3EF65729F3D")
        local = known_folder("F1B32785-6FBA-4FCF-9D55-7B8E7F157091")
        desktop = known_folder("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
        home = known_folder("5E6C858F-0E22-4760-9AFE-EA3317B67173")
        require(home.name.startswith("ga-ci-") and roaming.is_relative_to(home) and local.is_relative_to(home),
                "Known folders не принадлежат одноразовому CI профилю")
        installed = roaming / "Autodesk/ApplicationPlugins/GreenAtlas.bundle"
        profile = local / "GreenAtlas"
        require(not installed.exists(), "Нужна новая учётная запись без установки")
        require(not profile.exists() and not profile.is_symlink(),
                "Первой установке нужен отсутствующий профиль GreenAtlas")
        # Системные компоненты WebView2 требуют полное окружение Windows, в том
        # числе SystemDrive, ProgramData и ProgramFiles. Никакого окружения runner.
        environment = profile_environment.copy()
        environment.update(APPDATA=str(roaming), LOCALAPPDATA=str(local), USERPROFILE=str(home),
            PATH=str(Path(os.environ["SystemRoot"]) / "System32"), TEMP=str(output.parent / "Temp"),
            TMP=str(output.parent / "Temp"), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        ui = NativeUI()
        # Deliberately tiny file fixtures exercise configuration packaging, not
        # proprietary models or an actual Blender/AutoCAD rendering session.
        from app.desktop.dependencies import appearance_files

        fixture = output / "Компоненты на другом диске"
        cad_fixture = fixture / "AutoCAD fixture"
        cad_fixture.mkdir(parents=True)
        (cad_fixture / "Шаблон проверки.dwt").write_bytes(b"Picker fixture only, not a CAD template")
        for root, names in ((fixture / "botaniq_full", appearance_files()[0]), (fixture / "pbr", appearance_files()[1])):
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"owned CI asset fixture; not a render qualification")
        (fixture / "botaniq_full/textures").mkdir()
        (fixture / "botaniq_full/textures/leaf.png").write_bytes(b"owned texture fixture")
        # A text file named .exe triggers a modal Windows "unsupported 16-bit"
        # error before the API can reject it. A real OS utility is executable
        # but cannot report Blender's version, so validation fails without UI.
        shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32/where.exe", fixture / "blender.exe")
        settings_file = local / "Green Atlas/external-tools.json"

        def children(process: subprocess.Popen[bytes]) -> set[int]:
            try:
                return {process.pid, *(p.pid for p in psutil.Process(process.pid).children(recursive=True))}
            except psutil.NoSuchProcess:
                return set()

        def window(process: subprocess.Popen[bytes]) -> int | None:
            pids = children(process)
            return next((w for w in ui.windows() if ui.pid(w) in pids and ui.user.IsWindowVisible(w)
                         and (ui.text(w, kind=True) == "TWizardForm" or ui.text(w, kind=True) == "#32770")), None)

        def start(arguments: list[str]) -> subprocess.Popen[bytes]:
            process = subprocess.Popen([str(args.setup), *arguments], env=environment, cwd=output)
            owned.append(process)
            return process

        def capture(hwnd: int, name: str, *, process: subprocess.Popen[bytes] | None = None) -> None:
            time.sleep(0.25)
            if process is not None:
                require(ui.pid(hwnd) in children(process), "Кадр native picker относится к чужому процессу")
            ui.frame(hwnd, output / name)
            report["screenshots"].append({"file": name, "sha256": sha(output / name)})

        def progress_to_ready(process: subprocess.Popen[bytes], prefix: str) -> int:
            hwnd = wait_for(lambda: window(process), timeout=20, reason="Не появилось первое native окно")
            wait_for(lambda: "Мастер установит" in ui.content(hwnd))
            capture(hwnd, prefix + "01-welcome.png")
            ui.click(hwnd, "Далее")
            wait_for(lambda: "Папки и запуск" in ui.content(hwnd))
            content = ui.content(hwnd)
            require(str(installed).casefold() in content.casefold() and str(local / "GreenAtlas/Workspace").casefold() in content.casefold(), "Native мастер показывает другие папки")
            capture(hwnd, prefix + "02-locations.png")
            ui.click(hwnd, "Далее")
            wait_for(lambda: "Компоненты и ресурсы" in ui.content(hwnd), timeout=90)
            if not prefix:
                # Capture the first painted frame while automatic discovery runs.
                # A blocking CurPageChanged used to leave labels/buttons blank.
                capture(hwnd, "02a-dependencies-loading.png")
            wait_for(lambda: "Поиск завершён" in ui.content(hwnd) and any(ui.text(c).replace("&", "").startswith("Далее") and ui.user.IsWindowEnabled(c) for c in ui.windows(hwnd)), timeout=90)
            capture(hwnd, prefix + "02b-dependencies.png")
            if not prefix:
                edits = [c for c in ui.windows(hwnd) if ui.user.IsWindowVisible(c) and ui.text(c, kind=True) in {"TNewEdit", "Edit"}]
                def top(handle: int) -> int:
                    rect = ui.w.RECT()
                    require(ui.user.GetWindowRect(handle, ctypes.byref(rect)), "Нет позиции поля мастера")
                    return rect.top
                edits.sort(key=top)
                require(len(edits) == 5, "Native мастер не показывает пять полей компонентов")
                for index, path in ((3, fixture), (4, fixture / "pbr")):
                    value = ctypes.create_unicode_buffer(str(path))
                    result = ctypes.c_size_t()
                    ui.user.SendMessageTimeoutW(edits[index], 0x000C, 0, ctypes.addressof(value), 0x0002, 1000, ctypes.byref(result))
                    require(ui.text(edits[index]) == str(path), "Native поле не приняло Unicode путь")
                capture(hwnd, "02c-component-paths.png")
            ui.click(hwnd, "Далее")
            wait_for(lambda: "дополнительные задачи" in ui.content(hwnd).lower(), timeout=90)
            capture(hwnd, prefix + "03-shortcut.png")
            ui.click(hwnd, "Далее")
            wait_for(lambda: any(ui.text(c).replace("&", "").startswith("Установить") for c in ui.windows(hwnd)))
            capture(hwnd, prefix + "04-ready.png")
            return hwnd

        # Реальная отмена первого запуска не должна публиковать программу/ярлык.
        cancelled = start(["/SP-", "/LOG=" + str(output / "cancel.log")])
        hwnd = progress_to_ready(cancelled, "cancel-")
        ui.click(hwnd, "Отмена")
        confirm = wait_for(lambda: next((w for w in ui.windows() if ui.pid(w) in children(cancelled)
            and ui.user.IsWindowVisible(w) and w != hwnd and "выход" in ui.content(w).lower()), None), timeout=10)
        # Стандартные MessageBox-кнопки следуют языку Windows, не языку Inno.
        ui.click(confirm, "Да", "Yes")
        require(cancelled.wait(timeout=20) != 0 and not installed.exists(), "Отмена опубликовала программу")
        require(not settings_file.exists(), "Отмена мастера записала настройки компонентов")
        require(not profile.exists() and not profile.is_symlink(),
                "Отмена до установки создала профиль GreenAtlas")
        report["cancel_before_install_passed"] = True

        actual = start(["/SP-", "/LOG=" + str(output / "install.log")])
        hwnd = progress_to_ready(actual, "")
        ui.click(hwnd, "Установить")
        captured_progress = False
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            content = ui.content(hwnd)
            for modal in ui.windows():
                if (modal != hwnd and ui.pid(modal) in children(actual) and ui.user.IsWindowVisible(modal)
                        and ui.text(modal, kind=True) in ("#32770", "TSetupMessageForm")):
                    capture(modal, "failure-dialog.png")
                    raise RuntimeError("Native мастер сообщил ошибку: " + ui.content(modal)[:1600])
            if "Установка не завершена" in content:
                capture(hwnd, "failure-page.png")
                raise RuntimeError(content[:2000])
            if "Green Atlas установлен" in content:
                break
            if "Подготавливаем" in content or "Устанавливаем" in content or "Проверяем" in content:
                if not captured_progress:
                    capture(hwnd, "05-progress.png")
                    captured_progress = True
                if content not in report["stages"]:
                    report["stages"].append(content[:2000])
            require(actual.poll() is None, "Мастер завершился до страницы успеха")
            time.sleep(0.1)
        else:
            raise RuntimeError("Мастер не завершил установку")
        require(captured_progress, "Не было видимого прогресса")
        capture(hwnd, "06-finished.png")
        require(installed.is_dir() and sha(installed / "distribution-manifest.json") == build["distribution_manifest_sha256"], "Установлен другой пакет")
        load_owned_bundle(installed)
        configured = json.loads(settings_file.read_bytes())
        require(configured["botaniq_root"] == str(fixture / "botaniq_full") and configured["pbr_root"] == str(fixture / "pbr"), "Frozen Setup не сохранил нормализованные пути библиотек")
        report["wizard_component_paths_saved"] = True
        # WM_SETTEXT checks Unicode entry and backend persistence, not Browse.
        # Keep this gap explicit until the native installer pickers are exercised.
        report["wizard_browse_buttons_tested"] = False
        host = installed / "Contents/Windows/GreenAtlasDesktop.exe"
        start_menu_target, start_menu_arguments = shortcut_target(
            roaming / "Microsoft/Windows/Start Menu/Programs/Green Atlas.lnk"
        )
        require((start_menu_target, start_menu_arguments) == (str(host), ""),
                "Нет правильного меню Пуск")
        require(shortcut_target(desktop / "Green Atlas.lnk") == (str(host), ""), "Нет выбранного ярлыка на рабочем столе")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            uninstaller = Path(winreg.QueryValueEx(key, "NativeUninstaller")[0])
            backend = Path(winreg.QueryValueEx(key, "BackendSetup")[0])
            require(sha(backend) == build["backend_sha256"], "Native мастер запустил другой backend")
            require(winreg.QueryValueEx(key, "UninstallString")[0] == '"' + str(uninstaller) + '"', "Settings не использует native удаление")
        require(uninstaller.is_file(), "Нет native uninstaller")
        ui.click(hwnd, "Готово")
        require(actual.wait(timeout=30) == 0, "Мастер не завершился успешно")
        report["native_wizard_completed"] = True

        # Finish должен действительно открыть именно установленный Desktop.
        def launched_host() -> int | None:
            for w in ui.windows():
                if ui.text(w, kind=True) != "GreenAtlasDesktopCurrent" or not ui.user.IsWindowVisible(w):
                    continue
                try:
                    if Path(psutil.Process(ui.pid(w)).exe()) == host:
                        return w
                except psutil.Error:
                    pass
            return None

        app_window = wait_for(launched_host, timeout=45, reason="Finish не открыл установленный Desktop")
        app_pid = ui.pid(app_window)
        wait_for(lambda: ui.text(app_window) == "Green Atlas — Готово", timeout=180,
                 reason="Установленный Desktop не подготовил API и WebView2")
        time.sleep(3)
        app_memory = {"idle_rss_bytes": 0, "idle_private_bytes": 0,
                      "peak_rss_bytes": 0, "peak_private_bytes": 0,
                      "max_processes": 0, "samples": 0}

        def sample_app_memory(*, idle=False):
            try:
                root = psutil.Process(app_pid)
                processes = [root, *root.children(recursive=True)]
            except psutil.Error:
                return
            rss = private = live = 0
            for process in processes:
                try:
                    info = process.memory_info()
                    rss += info.rss
                    private += getattr(info, "private", 0)
                    live += 1
                except psutil.Error:
                    continue
            app_memory["samples"] += 1
            app_memory["max_processes"] = max(app_memory["max_processes"], live)
            app_memory["peak_rss_bytes"] = max(app_memory["peak_rss_bytes"], rss)
            app_memory["peak_private_bytes"] = max(app_memory["peak_private_bytes"], private)
            if idle:
                app_memory["idle_rss_bytes"] = rss
                app_memory["idle_private_bytes"] = private

        sample_app_memory(idle=True)
        capture(app_window, "07-launched-app.png")
        component_script = REPO / "scripts/desktop/windows_component_ui_smoke.ps1"
        powershell = Path(environment["SYSTEMROOT"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        with (output / "component-ui.stdout.log").open("wb") as stdout, (output / "component-ui.stderr.log").open("wb") as stderr:
            component_process = subprocess.Popen([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(component_script), "-WindowHandle", str(app_window), "-AppProcessId", str(app_pid), "-FixtureRoot", str(fixture), "-OutputDirectory", str(output)], env=environment, cwd=REPO, stdout=stdout, stderr=stderr)
            owned.append(component_process)
            captured = set()
            deadline = time.monotonic() + 480
            while component_process.poll() is None and time.monotonic() < deadline:
                sample_app_memory()
                step_path = output / "component-step.json"
                if step_path.exists():
                    try:
                        step = json.loads(step_path.read_text(encoding="utf-8-sig"))
                    except (ValueError, OSError):
                        time.sleep(0.1)
                        continue
                    if step["name"] not in captured:
                        handle = int(step["handle"])
                        require(ui.pid(handle) == app_pid, "Кадр компонентов относится к чужому окну")
                        capture(handle, step["name"] + ".png")
                        captured.add(step["name"])
                        (output / (step["name"] + ".ack")).write_text("captured")
                time.sleep(0.1)
            require(component_process.poll() is not None, "UI настроек не завершился вовремя")
        component_receipt = json.loads((output / "component-ui-receipt.json").read_text(encoding="utf-8-sig"))
        report["component_ui"] = component_receipt
        report["app_process_tree_memory"] = app_memory
        component_error = ("UI настроек: " + component_receipt.get("error", "не выполнен")
                           if component_process.returncode != 0 or not component_receipt["passed"] else None)
        # Preserve independent update/uninstall and native wizard Browse evidence
        # even if the Desktop component-settings probe fails.
        def close_application(hwnd: int) -> None:
            pid = ui.pid(hwnd)
            try:
                root = psutil.Process(pid)
                processes = [root, *root.children(recursive=True)]
            except psutil.Error as error:
                raise RuntimeError("Не найдено собственное дерево Desktop") from error
            require(ui.user.PostMessageW(hwnd, 0x10, 0, 0), "Не отправлено закрытие Desktop")
            wait_for(lambda: all(not process.is_running() for process in processes),
                     timeout=45, reason="Собственные Desktop, API и WebView2 не завершились")

        close_application(app_window)
        settings_sha = sha(settings_file)
        report["finish_launched_installed_application"] = True
        report["installed_api_and_webview_session_ready"] = True
        database = profile / "Workspace/projects.sqlite3"
        require(database.is_file(), "Запущенное приложение не создало свою БД")
        report["clean_profile_first_install"] = True
        # Ручные данные добавляем после подготовки чистого профиля установщиком
        # и успешного запуска и закрытия API установленным Desktop.
        sentinel = database.parent / "ci-preserved.txt"
        with sentinel.open("xb") as stream:
            stream.write(os.urandom(37))
        preserved_sha = sha(sentinel)

        # Проверяем обычный вход отдельно от кнопки «Готово» мастера Inno.
        # Проверенная цель меню «Пуск» запускается без аргументов и подготовки,
        # с обычными строгими проверками профиля установленного Desktop.
        normal = subprocess.Popen([start_menu_target], env=environment, cwd=host.parent)
        owned.append(normal)
        normal_window = wait_for(launched_host, timeout=45,
                                 reason="Обычный запуск не открыл установленный Desktop")
        require(ui.pid(normal_window) in children(normal),
                "Обычный запуск показал чужой Desktop")
        wait_for(lambda: ui.text(normal_window) == "Green Atlas — Готово", timeout=180,
                 reason="Обычный запуск не подготовил API и WebView2")
        close_application(normal_window)
        require(normal.wait(timeout=10) == 0, "Обычный Desktop завершился с ошибкой")
        require(sha(sentinel) == preserved_sha, "Обычный запуск изменил Workspace sentinel")
        require(sha(settings_file) == settings_sha, "Обычный запуск изменил пути компонентов")
        report["normal_launch_after_finish"] = True
        database_sha = sha(database)

        # Тот же native EXE выполняет настоящую повторную установку.
        repeat = start(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=" + str(output / "update.log")])
        require(repeat.wait(timeout=600) == 0, "Повторная native установка не прошла")
        require(sha(sentinel) == preserved_sha and sha(database) == database_sha, "Обновление изменило данные")
        require(sha(settings_file) == settings_sha, "Обновление изменило пути компонентов")
        report["native_update_preserved_database"] = True
        remove = subprocess.Popen([str(uninstaller), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=" + str(output / "uninstall.log")], env=environment, cwd=output)
        owned.append(remove)
        require(remove.wait(timeout=600) == 0 and not installed.exists(), "Native удаление не прошло")
        require(not (desktop / "Green Atlas.lnk").exists(), "Удаление оставило Desktop ярлык")
        require(sha(sentinel) == preserved_sha and sha(database) == database_sha, "Удаление изменило данные")
        require(sha(settings_file) == settings_sha, "Удаление изменило пути компонентов")
        report["native_uninstall_preserved_database"] = True

        # Browse in the native wizard is independent from the five pickers in
        # Desktop settings. Reopen the same sealed Setup after uninstall, test
        # every accept/cancel round-trip, then cancel without publishing it.
        browse_probe = start(["/SP-", "/LOG=" + str(output / "browse-only.log")])
        browse_window = wait_for(lambda: window(browse_probe), timeout=20)
        wait_for(lambda: "Мастер установит" in ui.content(browse_window))
        ui.click(browse_window, "Далее")
        wait_for(lambda: "Папки и запуск" in ui.content(browse_window))
        ui.click(browse_window, "Далее")
        wait_for(lambda: "Компоненты и ресурсы" in ui.content(browse_window), timeout=90)
        wait_for(lambda: "Поиск завершён" in ui.content(browse_window), timeout=90)
        capture(browse_window, "17-wizard-browse-page.png")

        def top(handle: int) -> int:
            rect = ui.w.RECT()
            require(ui.user.GetWindowRect(handle, ctypes.byref(rect)), "Нет позиции native поля")
            return rect.top

        controls = ui.windows(browse_window)
        wizard_edits = sorted((c for c in controls if ui.user.IsWindowVisible(c)
            and ui.text(c, kind=True) in {"TNewEdit", "Edit"}), key=top)
        wizard_buttons = sorted((c for c in controls if ui.user.IsWindowVisible(c)
            and ui.text(c).replace("&", "").strip().startswith("Выбрать")), key=top)
        require(len(wizard_edits) == len(wizard_buttons) == 5, "Мастер не показывает пять native Browse полей")

        def picker_dialog(title: str) -> int:
            return wait_for(lambda: next((w for w in ui.windows() if w != browse_window
                and ui.user.IsWindowVisible(w) and ui.pid(w) in children(browse_probe)
                and ui.text(w) == title), None), timeout=20, reason="Native picker мастера не открылся")

        def dialog_control(dialog: int, identifier: int) -> int:
            control = ui.user.GetDlgItem(dialog, identifier)
            if control:
                return control
            return next((w for w in ui.windows(dialog) if ui.user.GetDlgCtrlID(w) == identifier), 0)

        def dialog_click(dialog: int, identifier: int) -> None:
            require(ui.pid(dialog) in children(browse_probe), "Picker мастера принадлежит другому процессу")
            control = dialog_control(dialog, identifier)
            require(control and ui.pid(control) == ui.pid(dialog), "Нет принадлежащей picker кнопки")
            require(ui.user.PostMessageW(control, 0xF5, 0, 0), "Native picker не принял BM_CLICK")
            wait_for(lambda: not ui.user.IsWindow(dialog), timeout=20, reason="Native picker мастера не закрылся")

        picker_paths = [cad_fixture, cad_fixture / "Шаблон проверки.dwt", fixture / "blender.exe", fixture, fixture / "pbr"]
        picker_names = ["autocad_root", "autocad_template", "blender", "botaniq_root", "pbr_root"]
        for index, (name, path) in enumerate(zip(picker_names, picker_paths, strict=True)):
            original = ui.text(wizard_edits[index])
            title = "Выберите файл" if index in (1, 2) else "Выберите папку компонента"
            require(ui.user.PostMessageW(wizard_buttons[index], 0xF5, 0, 0), "Browse не открылся")
            modal = picker_dialog(title)
            capture(modal, f"18-wizard-{name}-cancel.png", process=browse_probe)
            dialog_click(modal, 2)
            require(ui.text(wizard_edits[index]) == original, "Cancel изменил поле мастера " + name)

            require(ui.user.PostMessageW(wizard_buttons[index], 0xF5, 0, 0), "Browse не открылся повторно")
            modal = picker_dialog(title)
            capture(modal, f"19-wizard-{name}-accept.png", process=browse_probe)
            field = dialog_control(modal, 1148 if index in (1, 2) else 1152)
            require(field and ui.pid(field) == ui.pid(modal), "Нет собственного поля пути в picker " + name)
            value = ctypes.create_unicode_buffer(str(path))
            result = ctypes.c_size_t()
            require(ui.user.SendMessageTimeoutW(field, 0x000C, 0, ctypes.addressof(value),
                    0x0002, 1000, ctypes.byref(result)) and result.value != 0
                    and ui.text(field) == str(path),
                    "Native picker не принял Unicode путь " + name)
            dialog_click(modal, 1)
            require(ui.text(wizard_edits[index]) == str(path), "Accept записал другой путь в мастере " + name)
            value = ctypes.create_unicode_buffer(original)
            result = ctypes.c_size_t()
            ui.user.SendMessageTimeoutW(wizard_edits[index], 0x000C, 0, ctypes.addressof(value), 0x0002, 1000, ctypes.byref(result))
            require(ui.text(wizard_edits[index]) == original, "Не восстановлено исходное поле " + name)
        report["wizard_browse_buttons_tested"] = True
        report["wizard_browse_accept_fields"] = picker_names
        report["wizard_browse_cancel_preserved_fields"] = picker_names
        capture(browse_window, "20-wizard-browse-restored.png")
        ui.click(browse_window, "Отмена")
        confirm = wait_for(lambda: next((w for w in ui.windows() if ui.pid(w) in children(browse_probe)
            and ui.user.IsWindowVisible(w) and w != browse_window and "выход" in ui.content(w).lower()), None), timeout=10)
        ui.click(confirm, "Да", "Yes")
        require(browse_probe.wait(timeout=20) != 0 and not installed.exists(), "Browse-only отмена опубликовала пакет")
        require(sha(sentinel) == preserved_sha and sha(database) == database_sha and sha(settings_file) == settings_sha,
                "Browse-only отмена изменила данные или настройки")
        require(component_error is None, component_error or "UI настроек не выполнен")
        report.update(passed=True, setup_sha256=build["sha256"], source_manifest_sha256=build["source_manifest_sha256"],
            first_window_before_payload_extraction=True,
            component_settings_preserved_by_update_and_uninstall=True, component_libraries_are_ci_fixtures=True)
    except Exception as error:  # noqa: BLE001 - сохранить ограниченный отчёт при любом сбое UI
        report["error"] = type(error).__name__ + ": " + str(error)
        if ui is not None:
            try:
                pids = {p.pid for p in owned}
                for process in owned:
                    if process.poll() is None:
                        pids.update(p.pid for p in psutil.Process(process.pid).children(recursive=True))
                report["failure_windows"] = [{"class": ui.text(w, kind=True), "text": ui.content(w)[:4000]}
                    for w in ui.windows() if ui.pid(w) in pids and ui.user.IsWindowVisible(w)]
            except (OSError, psutil.Error):
                report["failure_windows_unavailable"] = True
    finally:
        for process in owned:
            if process.poll() is None:
                try:
                    for child in psutil.Process(process.pid).children(recursive=True):
                        child.kill()
                    process.kill()
                    process.wait(timeout=10)
                except (psutil.Error, subprocess.TimeoutExpired):
                    pass
        (output / "install-ci-receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "receipt": str(output / "install-ci-receipt.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
