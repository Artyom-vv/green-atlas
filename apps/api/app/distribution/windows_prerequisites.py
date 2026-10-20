"""Обнаружение Evergreen и запуск только включённого offline установщика Microsoft."""

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.distribution.contracts import Contract, Sha256
from app.shared.child_dll_search import child_dll_search

WEBVIEW_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
MINIMUM_WEBVIEW = (152, 0, 4191, 53)
WEBVIEW_MACHINE_INSTALL_EXISTS = 0x80040828


def supported_runtime_version(value: object) -> bool:
    valid = valid_runtime_version(value)
    return valid is not None and tuple(int(part) for part in valid.split(".")) >= MINIMUM_WEBVIEW


def valid_runtime_version(value: object) -> str | None:
    if not isinstance(value, str) or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", value) is None:
        return None
    return value if any(int(part) for part in value.split(".")) else None


def webview_runtime_version() -> str | None:
    if os.name != "nt":
        return None
    import winreg

    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\" + WEBVIEW_CLIENT),
        (winreg.HKEY_CURRENT_USER, "Software\\Microsoft\\EdgeUpdate\\Clients\\" + WEBVIEW_CLIENT),
    ):
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
                value, kind = winreg.QueryValueEx(key, "pv")
            if kind == winreg.REG_SZ and (version := valid_runtime_version(value)):
                return version
        except FileNotFoundError:
            continue
    return None


class OfflineWebViewReceipt(Contract):
    format: Literal["green-atlas.offline-webview2/1"] = Field(alias="schema")
    file: Literal["MicrosoftEdgeWebView2RuntimeInstallerX64.exe"]
    sha256: Sha256
    publisher: Literal["Microsoft Corporation"]
    authenticode: Literal["Valid"]


def _offline_webview_payload(package: Path) -> tuple[Path, OfflineWebViewReceipt]:
    """Проверить обязательный offline payload без запуска внешнего установщика."""
    from app.distribution.preflight import _digest_file, _plain_file

    directory = "Contents/Windows/Prerequisites/"
    receipt = OfflineWebViewReceipt.model_validate_json(
        _plain_file(package, directory + "webview2-installer.json").read_bytes()
    )
    installer = _plain_file(package, directory + receipt.file)
    size, sha256 = _digest_file(installer)
    if size == 0 or sha256 != receipt.sha256:
        raise ValueError("Offline WebView2 installer не соответствует сборочной квитанции")
    return installer, receipt


def offline_webview_installer(package: Path) -> Path:
    """Проверить offline payload; публичный read-only контракт возвращает только EXE."""
    return _offline_webview_payload(package)[0]


def ensure_webview_runtime(
    package: Path, *, confirm_elevation: Callable[[], bool] | None = None
) -> str:
    current = webview_runtime_version()
    if current is not None and supported_runtime_version(current):
        return current
    if os.name != "nt":
        raise RuntimeError("WebView2 устанавливается только на Windows")
    installer, receipt = _offline_webview_payload(package)
    # Неповышенный setup запускает штатный per-user путь Microsoft, без скачивания bootstrapper.
    process = None
    try:
        with child_dll_search(external=True):
            process = subprocess.Popen([str(installer), "/silent", "/install"])
    except BaseException:
        if process is not None:
            process.kill()
            process.wait()
        raise
    try:
        code = process.wait(timeout=600)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait()
        raise RuntimeError("Превышено время offline установки WebView2") from error
    if code & 0xFFFFFFFF == WEBVIEW_MACHINE_INSTALL_EXISTS:
        # Per-user setup не обновляет уже установленный системный Runtime.
        # Параллельное штатное обновление могло завершиться за время ожидания.
        refreshed = webview_runtime_version()
        if refreshed is not None and supported_runtime_version(refreshed):
            return refreshed
        if confirm_elevation is None or not confirm_elevation():
            raise RuntimeError(
                "needs_machine_prerequisite_upgrade: для обновления системного "
                "Microsoft WebView2 нужно подтвердить запрос прав Windows"
            )
        from app.distribution.windows_prerequisite_elevation import (
            run_elevated_webview_installer,
        )

        code = run_elevated_webview_installer(
            installer, expected_sha256=receipt.sha256, timeout_seconds=600
        )
    if code not in (0, 3010):
        raise RuntimeError(f"WebView2 installer завершился с кодом {code}")
    version = webview_runtime_version()
    if version is None or not supported_runtime_version(version):
        raise RuntimeError("WebView2 не обнаружен после установки; перезапустите setup после перезагрузки")
    return version
