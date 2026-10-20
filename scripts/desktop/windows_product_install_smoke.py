"""Приёмка настоящего Setup под отдельным обычным пользователем CI.

Проверка не подменяет политику установщика и не запускает AutoCAD или Blender.
Рабочие каталоги синтетические; HKCU и SID принадлежат настоящему Windows token.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import stat
import subprocess
import sys
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps/api"))

from scripts.desktop.windows_qualification import (
    clean_environment,
    executable_json,
    ready_record,
    smoke_http,
    wait_clean_exit,
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def ordinary_file(path: Path) -> Path:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Ожидался обычный файл без reparse point: " + str(path))
    return path


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(ordinary_file(path).read_bytes())
    if not isinstance(value, dict):
        raise ValueError("Ожидался JSON объект: " + path.name)
    return value


def create_synthetic_profile(output: Path) -> Path:
    """Короткий Unicode-профиль создаётся отдельно от журналов под тем же CI grant."""
    from app.distribution.windows_install import plain_directory

    parent = plain_directory(output.parent)
    profile = parent / "П"
    profile.mkdir(mode=0o700)
    for name in ("Roaming", "Local", "Temp"):
        (profile / name).mkdir(mode=0o700)
    return profile


def runtime_native_dll_paths(package: Path) -> dict[str, object]:
    """Измерить реальные Windows UTF-16 пути DLL, ограничив обход и размер отчёта."""
    runtime = package.absolute() / "Contents/Windows/Runtime"
    longest, maximum, count = "", 0, 0
    for number, path in enumerate(runtime.rglob("*"), start=1):
        if number > 20000:
            raise ValueError("Runtime inventory превысил предел проверки путей")
        if path.suffix.lower() != ".dll":
            continue
        ordinary_file(path)
        text = str(path)
        size = len(text.encode("utf-16-le")) // 2
        if size > 4096:
            raise ValueError("Путь DLL превысил предел диагностического отчёта")
        count += 1
        if size > maximum:
            longest, maximum = text, size
    if not count:
        raise ValueError("Установленный runtime не содержит DLL для проверки путей")
    return {"runtime_native_dll_count": count, "longest_runtime_native_dll_path": longest,
            "runtime_native_dll_max_path_chars": maximum,
            "runtime_native_dll_paths_below_max_path": maximum < 260}


def sid_string(value: bytes) -> str:
    """Разобрать собственный SID для адресной сверки загруженного HKCU."""
    if len(value) < 8 or value[0] != 1 or value[1] > 15 or len(value) != 8 + 4 * value[1]:
        raise ValueError("Некорректный SID")
    numbers = [value[0], int.from_bytes(value[2:8], "big")]
    numbers.extend(int.from_bytes(value[offset:offset + 4], "little") for offset in range(8, len(value), 4))
    return "S-" + "-".join(map(str, numbers))


def verify_hkcu(value: bytes) -> None:
    """HKCU обязан отображаться в HKEY_USERS именно проверенного обычного SID."""
    import winreg

    path = "Software\\GreenAtlasCI\\" + uuid.uuid4().hex
    marker = uuid.uuid4().hex
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
            winreg.SetValueEx(key, "identity", 0, winreg.REG_SZ, marker)
        with winreg.OpenKey(winreg.HKEY_USERS, sid_string(value) + "\\" + path) as key:
            if winreg.QueryValueEx(key, "identity") != (marker, winreg.REG_SZ):
                raise ValueError("HKCU не соответствует профилю обычного CI пользователя")
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


class _Resources(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.paths: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        attribute = "src" if tag == "script" else "href" if tag == "link" else None
        value = values.get(attribute) if attribute else None
        if value:
            parsed = urlsplit(value)
            decoded = unquote(parsed.path)
            if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
                raise ValueError("Compiled Web ссылается на внешний или неоднозначный ресурс")
            from app.distribution.contracts import relative_path

            relative = relative_path(decoded.removeprefix("/"))
            self.paths.add(relative)


def document_resources(document: bytes) -> tuple[str, ...]:
    parser = _Resources()
    parser.feed(document.decode("utf-8"))
    paths = tuple(sorted(parser.paths))
    if not any(path.endswith(".js") for path in paths):
        raise ValueError("Проверка требует настоящий compiled Web с JavaScript entry")
    return paths


def verify_web_http(origin: str, secret: str, web: Path) -> dict[str, object]:
    """Сравнить HTTP index и каждый ресурс документа с установленными байтами."""

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    opener = build_opener(ProxyHandler({}), NoRedirect())
    document = ordinary_file(web / "index.html").read_bytes()
    resources = document_resources(document)
    for relative in ("index.html", *resources):
        expected = ordinary_file(web / relative)
        route = "/" if relative == "index.html" else "/" + quote(relative, safe="/")
        request = Request(origin + route, headers={"X-Green-Atlas-Local-Session": secret})
        with opener.open(request, timeout=30) as response:
            if response.status != 200:
                raise ValueError("Installed Web не вернул ресурс: " + relative)
            received, size = hashlib.sha256(), 0
            while block := response.read(1024 * 1024):
                received.update(block)
                size += len(block)
                if size > expected.stat().st_size:
                    raise ValueError("HTTP ресурс превышает установленный файл: " + relative)
            if size != expected.stat().st_size or received.hexdigest() != digest(expected):
                raise ValueError("HTTP ресурс отличается от установленного файла: " + relative)
    return {"index_and_document_resources_match": True, "resources_checked": len(resources),
            "browser_javascript_executed": False}


def installed_api(package: Path, profile: Path, diagnostics: Path) -> dict[str, object]:
    """Запустить именно установленный EXE с полным установленным compiled Web."""
    from app.shared.windows_process import launch_owned

    windows = package / "Contents/Windows"
    environment = clean_environment(profile, os.environ)
    data = profile / "Local/GreenAtlas/Workspace"
    transfers = profile / "Local/GreenAtlas/Transfers"
    child = launch_owned([
        str(windows / "Runtime/GreenAtlasRuntime.exe"), "--data-dir", str(data),
        "--web-dir", str(windows / "Web"), "--ticket-root", str(transfers),
        "--cad-worker", str(windows / "Worker"),
    ], cwd=windows, environment=environment)

    def save_failure(text: str) -> None:
        with (diagnostics / "installed-api.stderr.log").open("x", encoding="utf-8") as stream:
            stream.write(text)

    try:
        ready = ready_record(child, on_failure=save_failure)
        origin, secret = str(ready["origin"]), str(ready["session_token"])
        if smoke_http(origin, secret, "/api/projects", authenticated=False)[0] != 403:
            raise ValueError("Installed API доступен без приватного токена")
        if smoke_http(origin, secret, "/_desktop/health") != (200, {"service": "green-atlas-desktop", "protocol": 1}):
            raise ValueError("Installed API health не соответствует контракту")
        if smoke_http(origin, secret, "/api/projects") != (200, []):
            raise ValueError("Новый workspace не пуст")
        web = verify_web_http(origin, secret, windows / "Web")
        if smoke_http(origin, secret, "/_desktop/shutdown", method="POST") != (202, {"state": "stopping"}):
            raise ValueError("Installed API не принял graceful shutdown")
        shutdown = wait_clean_exit(child, timeout=20, label="Установленный API")
        if not (data / "projects.sqlite3").is_file():
            raise ValueError("Installed API не создал синтетическую рабочую базу")
        return {"passed": True, "installed_executable": True, "clean_system_path": True,
                "auth_403": True, "health": True, "empty_projects": True,
                "shutdown_202_exit_0": True, "shutdown": shutdown,
                "web": web, "cad_executed": False}
    finally:
        child.close()


def shortcut_target(path: Path) -> tuple[str, str]:
    """Прочитать настоящий .lnk через IShellLinkW и IPersistFile, без его запуска."""
    from ctypes import wintypes

    class Guid(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    def guid(value: str) -> Guid:
        return Guid.from_buffer_copy(uuid.UUID(value).bytes_le)

    def call(pointer: ctypes.c_void_p, index: int, types: tuple[Any, ...], *args: object) -> None:
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        result = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *types)(table[index])(pointer, *args)
        if result < 0:
            raise OSError(f"Ошибка чтения COM ярлыка: 0x{result & 0xffffffff:08x}")

    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoCreateInstance.argtypes = (ctypes.POINTER(Guid), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p))
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = ()
    ole.CoUninitialize.restype = None
    if ole.CoInitializeEx(None, 2) < 0:
        raise OSError("Не удалось открыть COM для проверки ярлыка")
    link, persist = ctypes.c_void_p(), ctypes.c_void_p()
    try:
        clsid, iid = guid("00021401-0000-0000-c000-000000000046"), guid("000214f9-0000-0000-c000-000000000046")
        if ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(link)) < 0:
            raise OSError("Не удалось открыть IShellLinkW")
        persist_iid = guid("0000010b-0000-0000-c000-000000000046")
        call(link, 0, (ctypes.POINTER(Guid), ctypes.POINTER(ctypes.c_void_p)), ctypes.byref(persist_iid), ctypes.byref(persist))
        call(persist, 5, (wintypes.LPCWSTR, wintypes.DWORD), str(ordinary_file(path)), 0)
        target, arguments = ctypes.create_unicode_buffer(32768), ctypes.create_unicode_buffer(32768)
        call(link, 3, (wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD), target, len(target), None, 0)
        call(link, 10, (wintypes.LPWSTR, ctypes.c_int), arguments, len(arguments))
        return target.value, arguments.value
    finally:
        if persist.value:
            call(persist, 2, ())
        if link.value:
            call(link, 2, ())
        ole.CoUninitialize()


def verify_shell(roaming: Path, saved: Path, installed: Path, version: str, *, present: bool) -> None:
    import winreg

    from app.distribution.windows_shell import UNINSTALL_KEY

    shortcut = roaming / "Microsoft/Windows/Start Menu/Programs/Green Atlas.lnk"
    rollback = shortcut.with_name("Green Atlas, предыдущая версия.lnk")
    if present:
        host = installed / "Contents/Windows/GreenAtlasDesktop.exe"
        if shortcut_target(shortcut) != (str(host), "") or rollback.exists() or rollback.is_symlink():
            raise ValueError("Installed .lnk указывает на другой EXE или аргументы")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            expected = {"DisplayName": "Green Atlas", "DisplayVersion": version, "InstallLocation": str(installed),
                        "UninstallString": f'"{saved}" --uninstall', "QuietUninstallString": f'"{saved}" --uninstall --quiet'}
            for name, value in expected.items():
                if winreg.QueryValueEx(key, name) != (value, winreg.REG_SZ):
                    raise ValueError("HKCU uninstall отличается: " + name)
    else:
        if shortcut.exists() or rollback.exists():
            raise ValueError("Удаление оставило ярлыки")
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY):
                raise ValueError("Удаление оставило HKCU uninstall")
        except FileNotFoundError:
            pass


def setup_command(setup: Path, arguments: list[str], output: Path, name: str, environment: dict[str, str]) -> dict[str, Any] | None:
    """Windowed EXE получает настоящие pipe; сырые каналы содержат только Setup JSON."""
    completed = subprocess.run([str(setup), *arguments, "--quiet"], env=environment, cwd=output,
                               capture_output=True, timeout=780, check=False)
    (output / (name + ".stdout.log")).write_bytes(completed.stdout)
    (output / (name + ".stderr.log")).write_bytes(completed.stderr)
    if completed.returncode != 0 or completed.stderr:
        raise RuntimeError(f"Настоящий Setup не прошёл {name}; код {completed.returncode}; см. частные журналы")
    if not completed.stdout and arguments == ["--purge-app-backups"]:
        return None
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise ValueError("Setup не вернул JSON объект: " + name)
    return value


def main() -> int:
    from app.distribution.preflight import manifest_identity
    from app.distribution.windows_install import load_owned_bundle, plain_directory
    from app.distribution.windows_prerequisites import (
        supported_runtime_version,
        webview_runtime_version,
    )
    from app.distribution.windows_setup_guard import current_user_sid
    from app.distribution.windows_shell import require_windows_user

    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--setup-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sid-hex", required=True)
    args = parser.parse_args()
    # Никаких записей до проверки настоящего неповышенного token и пути checkout.
    require_windows_user()
    user_sid = current_user_sid()
    if user_sid.hex() != args.expected_sid_hex:
        parser.error("Token не принадлежит созданному CI пользователю")
    output = args.output.absolute()
    if not output.is_relative_to(REPO / ".runtime"):
        parser.error("Synthetic install разрешён только в .runtime checkout")
    plain_directory(output.parent)
    verify_hkcu(user_sid)
    output.mkdir()
    report: dict[str, Any] = {"schema": "green-atlas.windows-product-install-ci/1", "passed": False,
        "ordinary_user_token": True, "production_admin_policy_unmodified": True,
        "installer_executed": False, "product_qualified": False, "autocad_executed": False,
        "blender_executed": False, "webview_gui_executed": False,
        "different_version_update_tested": False, "synthetic_profile_only": True, "steps": []}
    try:
        setup = ordinary_file(args.setup.absolute())
        build = read_object(args.setup_receipt)
        if (build.get("schema") != "green-atlas.windows-setup-build/1" or build.get("setup") != "GreenAtlasSetup.exe"
                or build.get("sha256") != digest(setup) or build.get("bytes") != setup.stat().st_size):
            raise ValueError("Для smoke нужен настоящий Setup и его точная production build receipt")
        profile = create_synthetic_profile(output)
        environment = clean_environment(profile, os.environ)
        roaming, local = profile / "Roaming", profile / "Local"
        plugins = roaming / "Autodesk/ApplicationPlugins"
        installed = plugins / "GreenAtlas.bundle"
        saved = local / "GreenAtlas/Installer/Versions" / str(build["sha256"]) / "GreenAtlasSetup.exe"
        sentinels = [local / "GreenAtlas" / name / "ci-preserve.txt" for name in ("Workspace", "Transfers", "WebView2", "Models", "Logs")]
        sentinels.append(plugins / "UnrelatedVendor.bundle/ci-preserve.txt")
        for path in sentinels:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(os.urandom(37))
        expected_sentinels = {path: digest(path) for path in sentinels}

        def preserved() -> None:
            if any(digest(path) != expected for path, expected in expected_sentinels.items()):
                raise ValueError("Setup изменил рабочие или чужие файлы")

        def status(executable: Path, *, present: bool, identity: str | None = None) -> None:
            if setup_command(executable, ["--status-json"], output, "status-" + str(len(report["steps"])), environment) != {"installed": present, "package_sha256": identity}:
                raise ValueError("Status настоящего Setup не соответствует установке")
            preserved()

        status(setup, present=False)
        initial_webview = webview_runtime_version()
        report["webview2_before"] = initial_webview
        report["offline_webview_install_required_by_initial_detection"] = not supported_runtime_version(initial_webview)
        report["installer_executed"] = True
        first = setup_command(setup, [], output, "install", environment)
        manifest = load_owned_bundle(installed)
        registration_bytes = (installed / "PackageContents.xml").read_bytes()
        identity = manifest_identity(manifest)
        if (digest(installed / "distribution-manifest.json") != build["distribution_manifest_sha256"]
                or manifest.source.manifest_sha256 != build["source_manifest_sha256"]
                or first is None or first.get("action") != "install" or first.get("package_sha256") != identity
                or first.get("backup") is not None or digest(ordinary_file(saved)) != build["sha256"]):
            raise ValueError("Установленный bundle/сохранённый Setup отличается от production build")
        verify_shell(roaming, saved, installed, manifest.layout.version, present=True)
        report["steps"].append("production_setup_install_exact_manifest_and_shell")
        preserved()
        status(saved, present=True, identity=identity)
        report["host_status"] = executable_json(installed / "Contents/Windows/GreenAtlasDesktop.exe", "--status-json")
        if (report["host_status"].get("schema") != "green-atlas.desktop-status/1"
                or report["host_status"].get("webview2_available") is not True
                or report["host_status"].get("layout_verified") is not True
                or not supported_runtime_version(report["host_status"].get("webview2_version"))):
            raise ValueError("Installed host не обнаружил WebView2/layout")
        report["webview2_after"] = webview_runtime_version()
        report.update(runtime_native_dll_paths(installed))
        if report["runtime_native_dll_paths_below_max_path"] is not True:
            raise ValueError("Синтетический CI профиль создаёт путь DLL длиной не менее 260 символов")
        report["installed_api"] = installed_api(installed, profile, output)
        database = local / "GreenAtlas/Workspace/projects.sqlite3"
        expected_sentinels[database] = digest(database)
        for name, arguments, action in (("same-payload-update", [], "install"),):
            receipt = setup_command(saved, arguments, output, name, environment)
            if (receipt is None or receipt.get("action") != action or receipt.get("package_sha256") != identity
                    or receipt.get("backup") is not None or receipt.get("cleanup_pending")):
                raise ValueError("Setup не подтвердил транзакцию: " + name)
            if manifest_identity(load_owned_bundle(installed)) != identity:
                raise ValueError("Транзакция изменила состав bundle: " + name)
            verify_shell(roaming, saved, installed, manifest.layout.version, present=True)
            report["steps"].append(name + "-transaction")
            status(saved, present=True, identity=identity)
        if any(path.name.startswith(("GreenAtlas.backup-", "GreenAtlas.failed-", "GreenAtlas.uninstalled-", "GreenAtlas.staging-")) for path in plugins.iterdir()):
            raise ValueError("Успешное обновление оставило старый программный комплект")
        downgrade = subprocess.run([str(saved), "--rollback", "--quiet"], env=environment, cwd=output,
                                   capture_output=True, timeout=120, check=False)
        (output / "rollback-unavailable.stdout.log").write_bytes(downgrade.stdout)
        (output / "rollback-unavailable.stderr.log").write_bytes(downgrade.stderr)
        rejection = json.loads(downgrade.stderr)
        if (downgrade.returncode != 1 or downgrade.stdout or rejection.get("ok") is not False
                or "Предыдущая сборка не сохранена" not in rejection.get("error", "")):
            raise ValueError("Setup не объяснил отсутствие исторической сборки")
        report["steps"].append("committed_update_removes_old_programs_and_explains_unavailable_downgrade")
        preserved()
        removed = setup_command(saved, ["--uninstall"], output, "uninstall", environment)
        if removed is None or removed.get("action") != "uninstall" or removed.get("installed") is not None or removed.get("package_sha256") != identity or installed.exists():
            raise ValueError("Setup не подтвердил удаление")
        if manifest_identity(load_owned_bundle(Path(removed["backup"]))) != identity:
            raise ValueError("Удаление не сохранило проверенный программный backup")
        verify_shell(roaming, saved, installed, manifest.layout.version, present=False)
        report["steps"].append("uninstall_shell_removed_program_backup_retained")
        status(saved, present=False)
        setup_command(saved, ["--purge-app-backups"], output, "purge-app-backups", environment)
        if any(path.name.startswith(("GreenAtlas.backup-", "GreenAtlas.uninstalled-", "GreenAtlas.failed-", "GreenAtlas.staging-")) for path in plugins.iterdir()):
            raise ValueError("Явная очистка оставила программные backups")
        preserved()
        if digest(saved) != build["sha256"]:
            raise ValueError("Очистка удалила текущий обслуживающий Setup")
        report["steps"].append("purge_owned_backups_preserves_workspace_database_and_saved_setup")
        # Reproduce a previous Setup that left the AutoCAD bundle without its
        # distribution manifest. The replacement must preserve that directory.
        installed.mkdir()
        (installed / "PackageContents.xml").write_bytes(registration_bytes)
        old_bridge = installed / "Contents/Windows/GreenAtlasBridge.arx"
        old_bridge.parent.mkdir(parents=True)
        old_bridge.write_bytes(b"incomplete-old-bridge")
        old_note = installed / "user-note.txt"
        old_note.write_bytes(b"preserve-old-install")
        repaired = setup_command(setup, [], output, "repair-incomplete", environment)
        if repaired is None or not repaired.get("backup") or repaired.get("package_sha256") != identity:
            raise ValueError("Setup не восстановил неполный прежний bundle")
        incomplete_backup = Path(repaired["backup"])
        if (not incomplete_backup.name.startswith("GreenAtlas.incomplete-")
                or (incomplete_backup / "user-note.txt").read_bytes() != b"preserve-old-install"
                or manifest_identity(load_owned_bundle(installed)) != identity):
            raise ValueError("Setup не сохранил неполный прежний bundle без потери файлов")
        report["steps"].append("repair_incomplete_previous_bundle_without_deletion")
        repaired_remove = setup_command(saved, ["--uninstall"], output, "uninstall-repaired", environment)
        if repaired_remove is None or installed.exists():
            raise ValueError("Setup не удалил восстановленную установку")
        setup_command(saved, ["--purge-app-backups"], output, "purge-repaired", environment)
        if (incomplete_backup / "user-note.txt").read_bytes() != b"preserve-old-install":
            raise ValueError("Очистка удалила сохранённый неполный bundle")
        preserved()
        report.update(passed=True, setup_sha256=build["sha256"], source_manifest_sha256=build["source_manifest_sha256"],
                      distribution_manifest_sha256=build["distribution_manifest_sha256"], workspace_preserved=True,
                      api_created_database_preserved=True, rollback_scope="no historical payload after committed update",
                      frozen_crash_recovery_tested=False)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        report["error"] = str(error)
    with (output / "install-ci-receipt.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"receipt": str(output / "install-ci-receipt.json"), "passed": report["passed"], "product_qualified": False}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
