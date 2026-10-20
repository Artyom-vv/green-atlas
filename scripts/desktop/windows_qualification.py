"""Проверки Windows payload и явный изолированный frozen smoke без CAD и user profile.

Квитанция перечисляет выполненные проверки, но не создаёт qualification manifest
и не подтверждает GUI AutoCAD, семантику захвата или установленный продукт.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps/api"))


class OwnedChild(Protocol):
    def poll(self) -> int | None: ...
    def read_available(self, maxbytes: int) -> bytes: ...
    def active_processes(self) -> int: ...
    def wait(self, timeout: float | None = None) -> int | None: ...
    def close(self) -> None: ...


def clean_environment(profile: Path, inherited: Mapping[str, str]) -> dict[str, str]:
    """Оставить только Windows system PATH и отдельные каталоги синтетического профиля."""
    system_value = next((value for key, value in inherited.items() if key.upper()=="SYSTEMROOT"), None)
    if system_value is None:
        raise ValueError("Не найден SystemRoot")
    system = Path(system_value)
    if not system.is_absolute():
        raise ValueError("SystemRoot должен быть абсолютным")
    env = {key.upper(): value for key, value in inherited.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS", "OS",
    }}
    env.update({
        "SYSTEMROOT": str(system), "PATH": str(system / "System32"),
        "HOME": str(profile), "USERPROFILE": str(profile),
        "APPDATA": str(profile / "Roaming"), "LOCALAPPDATA": str(profile / "Local"),
        "TEMP": str(profile / "Temp"), "TMP": str(profile / "Temp"),
        "TMPDIR": str(profile / "Temp"), "GREEN_ATLAS_LAYER_MODEL_PROVIDER": "names",
    })
    return env


def _ready_failure_diagnostic(output: bytes, exit_code: int | None, *, truncated: bool) -> str:
    """Сохранить ошибку запуска без приватной READY-строки и похожих на токен значений."""
    lines = output.decode("utf-8", errors="replace").splitlines()
    safe = [line for line in lines if "session_token" not in line.lower()
            and "green-atlas-desktop" not in line]
    text = re.sub(r"[A-Za-z0-9_-]{43,}", "[скрыто]", "\n".join(safe))
    return (f"Frozen API: код завершения {exit_code!r}.\n"
            + ("Сохранён ограниченный хвост вывода.\n" if truncated else "") + text + "\n")


def ready_record(child: OwnedChild, timeout: float = 120, *,
                 on_failure: Callable[[str], None] | None = None) -> dict[str, str | int]:
    """Дочитать ограниченный ready канал, не записывая токен и сырой вывод в отчёт."""
    deadline = time.monotonic() + timeout
    pending = bytearray()
    diagnostic = bytearray()
    truncated = False
    total = 0

    def record(chunk: bytes) -> None:
        nonlocal total, truncated
        total += len(chunk)
        diagnostic.extend(chunk)
        if len(diagnostic) > 65536:
            del diagnostic[:-65536]
            truncated = True

    try:
        while time.monotonic() < deadline:
            chunk = child.read_available(16384)
            record(chunk)
            pending.extend(chunk)
            if total > 1024 * 1024 or len(pending) > 65536:
                raise ValueError("Ready канал превысил предел")
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending = bytearray(rest)
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(value, dict) or value.get("service") != "green-atlas-desktop":
                    continue
                origin, secret = value.get("origin"), value.get("session_token")
                if (set(value) != {"service", "protocol", "origin", "session_token"}
                    or type(value["protocol"]) is not int or value["protocol"] != 1
                    or not isinstance(origin, str)
                    or re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{3,4}", origin) is None
                    or not 1024 <= int(origin.rsplit(":", 1)[1]) <= 65535
                    or not isinstance(secret, str)
                    or re.fullmatch(r"[A-Za-z0-9_-]{43,128}", secret) is None):
                    raise ValueError("Некорректный приватный ready контракт")
                return value
            if child.poll() is not None:
                raise RuntimeError("Frozen API завершился до готовности")
            time.sleep(0.01)
        raise TimeoutError("Frozen API не объявил готовность")
    except BaseException as error:
        if on_failure is not None:
            try:
                # После выхода лидера pipe может содержать ещё не прочитанный traceback.
                while total < 1024 * 1024:
                    chunk = child.read_available(min(16384, 1024 * 1024 - total))
                    if not chunk:
                        break
                    record(chunk)
                on_failure(_ready_failure_diagnostic(bytes(diagnostic), child.poll(), truncated=truncated))
            except (OSError, RuntimeError, ValueError) as diagnostic_error:
                error.add_note(f"Не удалось сохранить диагностику запуска: {diagnostic_error!r}")
        raise


def wait_clean_exit(
    child: OwnedChild, *, timeout: float = 20, label: str = "Frozen API"
) -> dict[str, int | float]:
    """Подтвердить код 0 и пустой Job за общий срок, без terminate или close.

    Сигнал лидера не означает, что Job уже освободил всех потомков. Пока ждём
    оба условия, ограниченно читаем stdout, чтобы полный pipe не мешал выходу.
    """
    started = time.monotonic()
    deadline = started + timeout
    pending = bytearray()
    total = 0
    first_active: int | None = None
    code: int | None = None
    active = 0
    try:
        while True:
            chunk = child.read_available(16384)
            total += len(chunk)
            pending.extend(chunk)
            if len(pending) > 65536:
                del pending[:-65536]
            if total > 1024 * 1024:
                raise RuntimeError("Канал завершения превысил предел")
            code = child.poll()
            active = child.active_processes()
            if code is not None:
                if first_active is None:
                    first_active = active
                if code != 0:
                    raise RuntimeError(f"Код завершения {code}; процессов в Job: {active}")
                if active == 0:
                    return {
                        "exit_code": 0,
                        "job_active_final": 0,
                        "job_active_after_leader": first_active,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "output_bytes": total,
                    }
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Общий срок {timeout:g} с: код лидера {code!r}, процессов в Job: {active}"
                )
            time.sleep(min(0.01, remaining))
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        diagnostic = _ready_failure_diagnostic(bytes(pending), code, truncated=total > 65536)
        failure = TimeoutError if isinstance(error, TimeoutError) else RuntimeError
        raise failure(f"{label}: {error}.\n{diagnostic[-4096:]}") from error


def executable_json(executable: Path, switch: str) -> dict[str, Any]:
    """Запустить только фиксированный диагностический switch установленного host."""
    from app.shared.windows_process import launch_owned

    if switch not in {"--self-test", "--build-info", "--status-json"}:
        raise ValueError("Неизвестная диагностическая команда")
    child = launch_owned([str(executable), switch], cwd=executable.parent)
    output = bytearray()
    try:
        deadline = time.monotonic() + 30
        while True:
            output.extend(child.read_available(16384))
            if len(output) > 65536:
                raise ValueError("Host diagnostic превысил предел")
            if child.poll() is not None:
                if child.active_processes():
                    raise RuntimeError("Host diagnostic оставил дочерний процесс")
                while chunk := child.read_available(16384):
                    output.extend(chunk)
                    if len(output) > 65536:
                        raise ValueError("Host diagnostic превысил предел")
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Host diagnostic не завершился")
            time.sleep(0.01)
        if child.poll() != 0:
            raise RuntimeError("Host diagnostic вернул ошибку: " + switch)
        value = json.loads(output)
        if not isinstance(value, dict):
            raise TypeError("Host diagnostic не вернул JSON объект")
        return value
    finally:
        child.close()


def dll_preflight(package: Path) -> dict[str, object]:
    """Сверить все PE imports с поставленным реестром; DLL и CAD не загружаются."""
    from app.distribution.contracts import relative_path
    from app.distribution.objectarx_imports import (
        LIBRARIES,
        SdkCatalog,
        validate_native_evidence,
    )
    from app.distribution.windows_dependencies import pe_imports

    path = package / "Contents/Windows/dependency-inventory.json"
    report = json.loads(path.read_bytes())
    if (not isinstance(report, dict) or report.get("schema") != "green-atlas.windows-dependencies/1"
        or report.get("resolution") not in {"build-file-inventory", "objectarx-sdk-import-contract"}
        or report.get("native_execution_qualified") is not False):
        raise ValueError("Неверный контракт DLL inventory")
    catalog = None
    if report["resolution"] == "objectarx-sdk-import-contract":
        catalog = SdkCatalog(
            dict(LIBRARIES),
            {pin.module: name for name, pin in LIBRARIES.items() if pin.module},
        )
        if report.get("objectarx_sdk") != catalog.report():
            raise ValueError("ObjectARX SDK inventory не совпадает с закреплённым контрактом")
        validate_native_evidence(package / "Contents/Windows", catalog)
    rows = report.get("binaries")
    if not isinstance(rows, list) or len(rows) > 20000:
        raise ValueError("Некорректный список PE")
    binaries: set[str] = set()
    external: set[str] = set()
    for row in rows:
        relative = relative_path(row["binary"])
        if relative in binaries:
            raise ValueError("Повторён PE в DLL inventory")
        binaries.add(relative)
        imports = row["imports"]
        if not isinstance(imports, list):
            raise TypeError("Ожидается список imports")
        declared = {entry["dll"] for entry in imports}
        if len(declared) != len(imports) or declared != set(pe_imports(package / relative)):
            raise ValueError("PE imports отличаются от DLL inventory")
        for entry in imports:
            provider = entry["provider"]
            if provider == "payload":
                dependency = package / relative_path(entry["path"])
                if not dependency.is_file() or dependency.name.lower() != entry["dll"]:
                    raise ValueError("Payload DLL не найдена")
            elif provider == "autocad2027":
                if relative not in {
                    "Contents/Windows/GreenAtlasBridge.arx",
                    "Contents/Windows/Worker/GreenAtlasQuery.crx",
                }:
                    raise ValueError("AutoCAD import разрешён только двум native модулям")
                if catalog is not None:
                    target = "core" if relative.endswith("GreenAtlasQuery.crx") else "gui"
                    if entry != catalog.module_evidence(entry["dll"], target=target):
                        raise ValueError("ObjectARX DLL import не совпадает с закреплённым SDK")
                external.add(entry["dll"])
            elif provider != "windows":
                raise ValueError("Неизвестный provider DLL")
    actual = {file.relative_to(package).as_posix() for file in (package / "Contents/Windows").rglob("*")
              if file.is_file() and file.suffix.lower() in {".exe", ".dll", ".arx", ".crx", ".pyd"}
              and "Prerequisites" not in file.relative_to(package).parts}
    if actual != binaries:
        raise ValueError("DLL inventory не покрывает собственные PE")
    return {"binaries_checked": len(binaries), "autocad_external_dlls": sorted(external),
            "resolution": "file-inventory-only", "native_execution_qualified": False}


def smoke_http(origin: str, secret: str, route: str, *, method: str = "GET",
               authenticated: bool = True) -> tuple[int, Any]:
    headers = {"x-green-atlas-local-session": secret} if authenticated else {}
    data = None
    if method == "POST":
        data = b"{}"
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(origin + route, data=data, headers=headers, method=method)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req: urllib.request.Request, fp: Any, code: int,
                             msg: str, headers: Any, newurl: str) -> None:
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("Smoke HTTP ответ превысил предел")
            return response.status, json.loads(body)
    except urllib.error.HTTPError as error:
        return error.code, None


def frozen_smoke(package: Path, work_root: Path) -> dict[str, object]:
    """Перенести payload в checkout .runtime и создать только новый синтетический профиль."""
    from app.distribution.preflight import check_package
    from app.distribution.windows_install import load_owned_bundle, plain_directory
    from app.shared.windows_process import launch_owned

    expected = REPO / ".runtime"
    work_root = Path(os.path.abspath(work_root))
    if not work_root.is_relative_to(expected):
        raise ValueError("Smoke разрешён только в .runtime текущего checkout")
    plain_directory(work_root, create=True)
    work = work_root / ("smoke-" + uuid.uuid4().hex)
    work.mkdir()
    if shutil.disk_usage(work).free < sum(p.stat().st_size for p in package.rglob("*") if p.is_file()) + 256 * 1024**2:
        raise ValueError("Недостаточно места для перенесённого payload")
    relocated = work / "Перенесённый комплект" / "GreenAtlas.bundle"
    shutil.copytree(package, relocated)
    relocated_manifest = load_owned_bundle(relocated)
    profile = work / "Синтетический профиль"
    for name in ("Roaming", "Local", "Temp", "Transfers"):
        (profile / name).mkdir(parents=True)
    windows = relocated / "Contents/Windows"
    environment = clean_environment(profile, os.environ)
    child = launch_owned([
        str(windows / "Runtime/GreenAtlasRuntime.exe"), "--data-dir", str(profile / "Workspace"),
        "--web-dir", str(windows / "Web"), "--ticket-root", str(profile / "Transfers"),
        "--cad-worker", str(windows / "Worker"),
    ], cwd=windows, environment=environment)
    try:
        ready = ready_record(child)
        origin, secret = str(ready["origin"]), str(ready["session_token"])
        if smoke_http(origin, secret, "/api/projects", authenticated=False)[0] != 403:
            raise ValueError("API открыт без приватного токена")
        if smoke_http(origin, secret, "/_desktop/health") != (200, {"service": "green-atlas-desktop", "protocol": 1}):
            raise ValueError("Health не соответствует runtime")
        if smoke_http(origin, secret, "/api/projects") != (200, []):
            raise ValueError("Синтетический профиль не пуст")
        if smoke_http(origin, secret, "/_desktop/shutdown", method="POST") != (202, {"state": "stopping"}):
            raise ValueError("Нет native shutdown квитанции")
        wait_clean_exit(child, timeout=15, label="Перенесённый frozen API")
        check_package(relocated, relocated_manifest)
        return {"executed": True, "passed": True, "relocated": True, "clean_environment": True,
                "synthetic_profile": str(profile), "checks": ["unauthenticated_403", "authenticated_health",
                "empty_projects", "native_shutdown_202", "exit_0_owned_tree_empty"], "cad_executed": False}
    finally:
        child.close()


def main() -> int:
    from app.distribution.preflight import check_package, manifest_identity
    from app.distribution.windows_install import load_owned_bundle, plain_directory

    parser = argparse.ArgumentParser(description="Проверка Windows candidate без запуска CAD")
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-frozen-smoke", action="store_true")
    parser.add_argument("--work-root", type=Path, default=REPO / ".runtime/windows-qualification")
    args = parser.parse_args()
    package = Path(os.path.abspath(args.package_root))
    args.output = Path(os.path.abspath(args.output))
    if args.output.is_relative_to(package):
        parser.error("Квитанция не должна изменять проверяемый payload")
    try:
        # Сам package тоже может оказаться ссылкой на физический каталог отчёта.
        # Проверяем его до любых mkdir; resolve дополнительно учитывает 8.3 aliases.
        plain_directory(package)
        if args.output.resolve(strict=False).is_relative_to(package.resolve(strict=True)):
            parser.error("Квитанция не должна изменять проверяемый payload")
        # На новой машине .runtime/qa ещё отсутствует. Создаём только проверенную
        # цепочку вне payload; ссылки/reparse points остаются запрещены.
        plain_directory(args.output.parent, create=True)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.output.exists():
        parser.error("Квитанция уже существует; выберите новый путь")
    report: dict[str, Any] = {"schema": "green-atlas.windows-checks/1", "qualification": "not_claimed",
                              "cad_executed": False, "windows_executed": os.name == "nt"}
    try:
        manifest = load_owned_bundle(package)
        report["package_manifest_sha256"] = manifest_identity(manifest)
        report["preflight"] = check_package(package, manifest).model_dump(mode="json", by_alias=True)
        report["dll_preflight"] = dll_preflight(package)
        if os.name != "nt":
            raise RuntimeError("Host и frozen проверки выполняются только на Windows")
        host = package / "Contents/Windows/GreenAtlasDesktop.exe"
        report["host"] = {switch.removeprefix("--"): executable_json(host, switch)
                          for switch in ("--self-test", "--build-info", "--status-json")}
        tests, info, status = (report["host"][key] for key in ("self-test", "build-info", "status-json"))
        if (tests.get("schema") != "green-atlas.desktop-self-test/1"
            or tests.get("passed") is not True or tests.get("real_windows") is not True
            or tests.get("webview_launched") is not False):
            raise ValueError("Host self-test не пройден")
        if (info.get("schema") != "green-atlas.desktop-build/1"
            or info.get("component") != "windows_webview2_host"
            or info.get("version") != manifest.layout.version
            or info.get("architecture") != "x86_64" or info.get("layout_verified") is not True):
            raise ValueError("Host build-info не соответствует комплекту")
        from app.distribution.windows_prerequisites import supported_runtime_version

        if (status.get("schema") != "green-atlas.desktop-status/1"
            or status.get("layout_verified") is not True or status.get("webview2_available") is not True
            or not supported_runtime_version(status.get("webview2_version", ""))):
            raise ValueError("Совместимый WebView2 Runtime не обнаружен")
        report["frozen_smoke"] = frozen_smoke(package, args.work_root) if args.run_frozen_smoke else {"executed": False}
        # Повторная проверка подтверждает отсутствие изменений исходного payload.
        check_package(package, manifest)
        report["passed"] = True
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        report["passed"] = False
        report["error"] = str(error)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"receipt": str(args.output), "passed": report["passed"], "qualification": "not_claimed"}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
