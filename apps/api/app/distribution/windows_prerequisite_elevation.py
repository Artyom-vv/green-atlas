"""Повышение прав только проверенного offline установщика WebView2.

Green Atlas не повышает собственный Setup. Удержанный файл проверяется по SHA
и системной Authenticode policy; запускаются только фиксированные параметры.
Повышенный Microsoft updater не принадлежит JobObject обычного пользователя:
тайм-аут прекращает ожидание, но не убивает этот внешний системный процесс.
"""

from __future__ import annotations

import ctypes
import hashlib
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Protocol

from app.shared.child_dll_search import child_dll_search

_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int32
_LONG = ctypes.c_int32
_HANDLE = ctypes.c_void_p
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_INSTALLER = "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
_PARAMETERS = "/silent /install"
_MICROSOFT = "Microsoft Corporation"
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 1
_OPEN_EXISTING = 3
_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WAIT_TIMEOUT = 0x102
_WAIT_FAILED = 0xFFFFFFFF
_ERROR_CANCELLED = 1223


class _Guid(ctypes.Structure):
    _fields_ = [("data1", _DWORD), ("data2", ctypes.c_uint16), ("data3", ctypes.c_uint16),
                ("data4", ctypes.c_ubyte * 8)]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [("attributes", _DWORD), ("reparse_tag", _DWORD)]


class _WinTrustFileInfo(ctypes.Structure):
    _fields_ = [("cbStruct", _DWORD), ("pcwszFilePath", ctypes.c_wchar_p), ("hFile", _HANDLE),
                ("pgKnownSubject", ctypes.POINTER(_Guid))]


class _WinTrustData(ctypes.Structure):
    _fields_ = [("cbStruct", _DWORD), ("pPolicyCallbackData", ctypes.c_void_p), ("pSIPClientData", ctypes.c_void_p),
                ("dwUIChoice", _DWORD), ("fdwRevocationChecks", _DWORD), ("dwUnionChoice", _DWORD),
                ("pFile", ctypes.POINTER(_WinTrustFileInfo)), ("dwStateAction", _DWORD), ("hWVTStateData", _HANDLE),
                ("pwszURLReference", ctypes.c_wchar_p), ("dwProvFlags", _DWORD), ("dwUIContext", _DWORD),
                ("pSignatureSettings", ctypes.c_void_p)]


class _ProviderCertificatePrefix(ctypes.Structure):
    # Нужны только первые два поля CRYPT_PROVIDER_CERT, саму цепочку ведёт WinTrust.
    _fields_ = [("cbStruct", _DWORD), ("pCert", ctypes.c_void_p)]


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [("cbSize", _DWORD), ("fMask", _DWORD), ("hwnd", _HANDLE), ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p), ("lpDirectory", ctypes.c_wchar_p),
                ("nShow", ctypes.c_int32), ("hInstApp", _HANDLE), ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p), ("hkeyClass", _HANDLE), ("dwHotKey", _DWORD),
                ("hIconOrMonitor", _HANDLE), ("hProcess", _HANDLE)]


class _NativeApi(Protocol):
    def lock_parents(self, path: Path) -> tuple[int, ...]: ...
    def open_locked(self, path: Path) -> int: ...
    def hash_locked(self, handle: int) -> str: ...
    def verify_microsoft(self, handle: int, path: Path) -> None: ...
    def execute(self, path: Path) -> int: ...
    def wait_exit(self, handle: int, timeout_seconds: int) -> int: ...
    def close(self, handle: int) -> None: ...


def _bind(library: Any, name: str, arguments: tuple[Any, ...], result: Any) -> Any:
    function = getattr(library, name)
    function.argtypes, function.restype = arguments, result
    return function


def _last_error() -> OSError:
    return getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())


class _WindowsApi:
    """Win32 ABI загружается только после явной проверки платформы."""

    def __init__(self) -> None:
        loader = getattr(ctypes, "WinDLL")
        kernel, trust, crypto, shell, ole = [loader(name, use_last_error=True, winmode=0x800)
                                          for name in ("kernel32.dll", "wintrust.dll", "crypt32.dll", "shell32.dll", "ole32.dll")]
        self._create_file = _bind(kernel, "CreateFileW", (ctypes.c_wchar_p, _DWORD, _DWORD, ctypes.c_void_p, _DWORD, _DWORD, _HANDLE), _HANDLE)
        self._information = _bind(kernel, "GetFileInformationByHandleEx", (_HANDLE, ctypes.c_int32, ctypes.c_void_p, _DWORD), _BOOL)
        self._file_type = _bind(kernel, "GetFileType", (_HANDLE,), _DWORD)
        self._read = _bind(kernel, "ReadFile", (_HANDLE, ctypes.c_void_p, _DWORD, ctypes.POINTER(_DWORD), ctypes.c_void_p), _BOOL)
        self._seek = _bind(kernel, "SetFilePointerEx", (_HANDLE, ctypes.c_int64, ctypes.c_void_p, _DWORD), _BOOL)
        self._wait = _bind(kernel, "WaitForSingleObject", (_HANDLE, _DWORD), _DWORD)
        self._exit_code = _bind(kernel, "GetExitCodeProcess", (_HANDLE, ctypes.POINTER(_DWORD)), _BOOL)
        self._close = _bind(kernel, "CloseHandle", (_HANDLE,), _BOOL)
        self._trust = _bind(trust, "WinVerifyTrust", (_HANDLE, ctypes.POINTER(_Guid), ctypes.POINTER(_WinTrustData)), _LONG)
        self._provider = _bind(trust, "WTHelperProvDataFromStateData", (_HANDLE,), ctypes.c_void_p)
        self._signer = _bind(trust, "WTHelperGetProvSignerFromChain", (ctypes.c_void_p, _DWORD, _BOOL, _DWORD), ctypes.c_void_p)
        self._certificate = _bind(trust, "WTHelperGetProvCertFromChain", (ctypes.c_void_p, _DWORD), ctypes.POINTER(_ProviderCertificatePrefix))
        self._name = _bind(crypto, "CertGetNameStringW", (ctypes.c_void_p, _DWORD, _DWORD, ctypes.c_void_p, ctypes.c_wchar_p, _DWORD), _DWORD)
        self._execute = _bind(shell, "ShellExecuteExW", (ctypes.POINTER(_ShellExecuteInfo),), _BOOL)
        self._co_initialize = _bind(ole, "CoInitializeEx", (ctypes.c_void_p, _DWORD), _LONG)
        self._co_uninitialize = _bind(ole, "CoUninitialize", (), None)

    def _open_path(self, path: Path, *, directory: bool) -> int:
        handle = self._create_file(str(path), 0x80 if directory else _GENERIC_READ,
                                   3 if directory else _FILE_SHARE_READ, None, _OPEN_EXISTING,
                                   _FILE_FLAG_OPEN_REPARSE_POINT | (_FILE_FLAG_BACKUP_SEMANTICS if directory else _FILE_FLAG_SEQUENTIAL_SCAN), None)
        if handle in (None, _INVALID_HANDLE):
            raise _last_error()
        try:
            info = _FileAttributeTagInfo()
            if not self._information(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise _last_error()
            if (self._file_type(handle) != 1 or info.attributes & _FILE_ATTRIBUTE_REPARSE_POINT
                    or bool(info.attributes & _FILE_ATTRIBUTE_DIRECTORY) != directory):
                raise RuntimeError("WebView2 installer должен быть обычным файлом в каталогах без reparse point")
        except BaseException as error:
            try:
                self.close(handle)
            except Exception as failure:
                error.add_note("Ошибка закрытия WebView2 path handle: " + str(failure))
            raise
        return int(handle)

    def lock_parents(self, path: Path) -> tuple[int, ...]:
        handles: list[int] = []
        try:
            for directory in reversed(path.parents):
                handles.append(self._open_path(directory, directory=True))
        except BaseException as error:
            for handle in reversed(handles):
                try:
                    self.close(handle)
                except Exception as failure:
                    error.add_note("Ошибка закрытия родительского WebView2 handle: " + str(failure))
            raise
        return tuple(handles)

    def open_locked(self, path: Path) -> int:
        return self._open_path(path, directory=False)

    def hash_locked(self, handle: int) -> str:
        if not self._seek(handle, 0, None, 0):
            raise _last_error()
        digest = hashlib.sha256()
        buffer = ctypes.create_string_buffer(1024 * 1024)
        while True:
            count = _DWORD()
            if not self._read(handle, buffer, len(buffer), ctypes.byref(count), None):
                raise _last_error()
            if count.value > len(buffer):
                raise RuntimeError("ReadFile вернул недопустимую длину installer")
            if not count.value:
                break
            digest.update(buffer.raw[:count.value])
        if not self._seek(handle, 0, None, 0):
            raise _last_error()
        return digest.hexdigest()

    def _certificate_name(self, certificate: int, kind: int, oid: bytes | None = None) -> str:
        parameter = ctypes.cast(ctypes.c_char_p(oid), ctypes.c_void_p) if oid else None
        length = int(self._name(certificate, kind, 0, parameter, None, 0))
        if not 1 < length <= 4096:
            raise RuntimeError("WinTrust не вернул имя издателя WebView2")
        buffer = ctypes.create_unicode_buffer(length)
        if self._name(certificate, kind, 0, parameter, buffer, length) != length:
            raise RuntimeError("Не удалось прочитать издателя WebView2")
        return buffer.value

    def verify_microsoft(self, handle: int, path: Path) -> None:
        action = _Guid(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
        file = _WinTrustFileInfo(ctypes.sizeof(_WinTrustFileInfo), str(path), handle, None)
        data = _WinTrustData()
        data.cbStruct, data.dwUIChoice, data.dwUnionChoice, data.dwStateAction = ctypes.sizeof(data), 2, 1, 1
        data.pFile = ctypes.pointer(file)
        # Системная Authenticode policy с локальным cache, без сетевой загрузки и MD2/MD4.
        data.dwProvFlags = 0x1000 | 0x2000
        failure: BaseException | None = None
        try:
            status = int(self._trust(None, ctypes.byref(action), ctypes.byref(data)))
            if status:
                raise RuntimeError(f"Подпись WebView2 не прошла WinVerifyTrust: 0x{status & 0xFFFFFFFF:08X}")
            provider = self._provider(data.hWVTStateData)
            signer = self._signer(provider, 0, False, 0) if provider else None
            certificate = self._certificate(signer, 0) if signer else None
            if not certificate or certificate.contents.cbStruct < ctypes.sizeof(_ProviderCertificatePrefix) or not certificate.contents.pCert:
                raise RuntimeError("WinTrust не вернул сертификат подписанта WebView2")
            cert = int(certificate.contents.pCert)
            if self._certificate_name(cert, 4) != _MICROSOFT or self._certificate_name(cert, 3, b"2.5.4.10") != _MICROSOFT:
                raise RuntimeError("WebView2 installer подписан другим издателем, требуется Microsoft Corporation")
        except BaseException as error:
            failure = error
            raise
        finally:
            data.dwStateAction = 2
            status = int(self._trust(None, ctypes.byref(action), ctypes.byref(data)))
            if status:
                message = f"Не удалось закрыть WinTrust state: 0x{status & 0xFFFFFFFF:08X}"
                if failure is not None:
                    failure.add_note(message)
                else:
                    raise RuntimeError(message)

    def execute(self, path: Path) -> int:
        status = int(self._co_initialize(None, 2))
        if status not in (0, 1):
            raise OSError(f"Не удалось подготовить COM для WebView2 UAC: 0x{status & 0xFFFFFFFF:08X}")
        info = _ShellExecuteInfo()
        info.cbSize, info.fMask, info.lpVerb = ctypes.sizeof(info), 0x40 | 0x100, "runas"
        info.lpFile, info.lpParameters, info.lpDirectory, info.nShow = str(path), _PARAMETERS, str(path.parent), 1
        try:
            if not self._execute(ctypes.byref(info)):
                error = _last_error()
                if info.hProcess:
                    self.close(int(info.hProcess))
                if error.winerror == _ERROR_CANCELLED:
                    raise RuntimeError("Обновление WebView2 отменено в запросе прав администратора") from error
                raise error
            if not info.hProcess:
                raise RuntimeError("ShellExecuteExW не вернул handle повышенного WebView2 installer")
            return int(info.hProcess)
        finally:
            self._co_uninitialize()

    def wait_exit(self, handle: int, timeout_seconds: int) -> int:
        result = int(self._wait(handle, timeout_seconds * 1000))
        if result == _WAIT_TIMEOUT:
            raise RuntimeError("Истекло время ожидания WebView2; повышенный Microsoft installer может продолжать работу")
        if result == _WAIT_FAILED:
            raise _last_error()
        if result != 0:
            raise OSError("Неожиданный результат ожидания WebView2 installer")
        code = _DWORD()
        if not self._exit_code(handle, ctypes.byref(code)):
            raise _last_error()
        return int(code.value)

    def close(self, handle: int) -> None:
        if not self._close(handle):
            raise _last_error()


def _native_api() -> _NativeApi:
    return _WindowsApi()


def run_elevated_webview_installer(installer: Path, *, expected_sha256: str, timeout_seconds: int = 600) -> int:
    """Повысить только Microsoft WebView2; timeout ограничивает ожидание после UAC.

    Отмена UAC и тайм-аут не означают успешную установку. Возвращённый код и
    фактическую совместимость установленного runtime проверяет вызывающий Setup.
    """
    if sys.platform != "win32":
        raise OSError("Повышенный WebView2 installer поддерживается только на Windows")
    if not installer.is_absolute() or installer.name != _INSTALLER or str(installer).startswith(("\\\\", "\\\\?\\", "\\\\.\\")):
        raise ValueError("Нужен абсолютный локальный путь штатного WebView2 installer")
    if not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None:
        raise ValueError("Нужен точный SHA256 WebView2 installer")
    if type(timeout_seconds) is not int or not 0 < timeout_seconds <= 0xFFFFFFFE // 1000:
        raise ValueError("Нужен конечный положительный timeout WebView2")
    api, cleanup_errors = _native_api(), []

    def close(handle: int) -> None:
        try:
            api.close(handle)
        except Exception as error:
            cleanup_errors.append(error)

    try:
        with ExitStack() as handles:
            # Запрет DELETE удерживает каждый участок пути от подмены во время UAC.
            for parent in api.lock_parents(installer):
                handles.callback(close, parent)
            locked = api.open_locked(installer)
            handles.callback(close, locked)
            if api.hash_locked(locked) != expected_sha256.lower():
                raise RuntimeError("SHA256 WebView2 installer отличается от проверенного payload")
            api.verify_microsoft(locked, installer)
            with child_dll_search(external=True):
                process = api.execute(installer)
                handles.callback(close, process)
            code = api.wait_exit(process, timeout_seconds)
    except BaseException as error:
        for failure in cleanup_errors:
            error.add_note("Ошибка закрытия WebView2 handle: " + str(failure))
        raise
    if cleanup_errors:
        raise cleanup_errors[0]
    return code
