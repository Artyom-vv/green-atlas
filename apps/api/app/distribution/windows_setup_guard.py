"""Отказ от замены bundle, пока текущий пользователь использует его компоненты.

Toolhelp видит процессы других сессий того же SID. Модуль никого не завершает
и не обращается к PowerShell, WMI или именам пользовательских чертежей.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Iterable
from typing import Protocol

APPLICATIONS = frozenset(
    {"acad.exe", "accoreconsole.exe", "greenatlasdesktop.exe", "greenatlasruntime.exe"}
)


class ProcessOwners(Protocol):
    def current_sid(self) -> bytes: ...
    def processes(self) -> Iterable[tuple[int, str]]: ...
    def owner_sid(self, pid: int) -> bytes: ...


def current_user_sid() -> bytes:
    """Прочитать SID текущего процесса без имени пользователя и изменения профиля."""
    return _WindowsOwners().current_sid()


def assert_applications_closed(owners: ProcessOwners | None = None) -> None:
    """Проверить владельца каждого подходящего процесса, включая другие сессии."""
    api = owners if owners is not None else _WindowsOwners()
    current = api.current_sid()
    if not current:
        raise OSError("Не удалось определить пользователя установки")
    active: set[str] = set()
    for pid, name in api.processes():
        if name.casefold() not in APPLICATIONS:
            continue
        try:
            sid = api.owner_sid(pid)
        except ProcessLookupError:
            # Процесс мог закончиться после получения snapshot.
            continue
        except OSError as error:
            raise OSError(
                "Не удалось проверить владельца " + name + ". Закройте приложение и повторите установку."
            ) from error
        if not sid:
            raise OSError("Не удалось определить владельца " + name)
        if sid == current:
            active.add(name.casefold())
    if active:
        raise RuntimeError(
            "Перед установкой закройте приложения во всех своих сеансах: "
            + ", ".join(sorted(active))
        )


class _WindowsOwners:
    def __init__(self) -> None:
        if os.name != "nt" or ctypes.sizeof(ctypes.c_wchar) != 2:
            raise OSError("Проверка процессов установки предназначена для Windows")
        loader = getattr(ctypes, "WinDLL")
        self.kernel = loader("kernel32", use_last_error=True)
        self.security = loader("advapi32", use_last_error=True)
        handle = ctypes.c_void_p
        dword = ctypes.c_uint32
        boolean = ctypes.c_int32

        class Entry(ctypes.Structure):
            _fields_ = [
                ("dwSize", dword), ("cntUsage", dword), ("th32ProcessID", dword),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", dword),
                ("cntThreads", dword), ("th32ParentProcessID", dword),
                ("pcPriClassBase", ctypes.c_int32), ("dwFlags", dword),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        class TokenUser(ctypes.Structure):
            _fields_ = [("Sid", handle), ("Attributes", dword)]

        self.entry_type, self.token_type = Entry, TokenUser
        definitions: tuple[tuple[object, str, list[object], object], ...] = (
            (self.kernel, "CreateToolhelp32Snapshot", [dword, dword], handle),
            (self.kernel, "Process32FirstW", [handle, ctypes.POINTER(Entry)], boolean),
            (self.kernel, "Process32NextW", [handle, ctypes.POINTER(Entry)], boolean),
            (self.kernel, "GetCurrentProcess", [], handle),
            (self.kernel, "OpenProcess", [dword, boolean, dword], handle),
            (self.kernel, "CloseHandle", [handle], boolean),
            (self.security, "OpenProcessToken", [handle, dword, ctypes.POINTER(handle)], boolean),
            (self.security, "GetTokenInformation", [handle, dword, handle, dword, ctypes.POINTER(dword)], boolean),
            (self.security, "IsValidSid", [handle], boolean),
            (self.security, "GetLengthSid", [handle], dword),
        )
        for library, name, arguments, result in definitions:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def _error(*, allow_exited: bool = False) -> OSError:
        code = getattr(ctypes, "get_last_error")()
        if code == 87 and allow_exited:
            return ProcessLookupError(code, "Процесс уже завершился")
        return OSError(code, "Не удалось прочитать список процессов или SID")

    def current_sid(self) -> bytes:
        return self._sid(self.kernel.GetCurrentProcess())

    def processes(self) -> Iterable[tuple[int, str]]:
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise self._error()
        try:
            entry = self.entry_type()
            entry.dwSize = ctypes.sizeof(entry)
            if not self.kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
                if getattr(ctypes, "get_last_error")() == 18:
                    return
                raise self._error()
            while True:
                yield int(entry.th32ProcessID), str(entry.szExeFile)
                if not self.kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                    if getattr(ctypes, "get_last_error")() != 18:
                        raise self._error()
                    return
        finally:
            self.kernel.CloseHandle(snapshot)

    def owner_sid(self, pid: int) -> bytes:
        process = self.kernel.OpenProcess(0x1000, False, pid)
        if not process:
            raise self._error(allow_exited=True)
        try:
            return self._sid(process)
        finally:
            self.kernel.CloseHandle(process)

    def _sid(self, process: int) -> bytes:
        token = ctypes.c_void_p()
        if not self.security.OpenProcessToken(process, 8, ctypes.byref(token)):
            raise self._error()
        try:
            size = ctypes.c_uint32()
            self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not 0 < size.value <= 1024 * 1024:
                raise OSError("Некорректный размер TOKEN_USER")
            data = ctypes.create_string_buffer(size.value)
            if not self.security.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
                raise self._error()
            info = ctypes.cast(data, ctypes.POINTER(self.token_type)).contents
            if not self.security.IsValidSid(info.Sid):
                raise OSError("Некорректный SID процесса")
            length = int(self.security.GetLengthSid(info.Sid))
            if not 0 < length <= 68:
                raise OSError("Некорректная длина SID процесса")
            return ctypes.string_at(info.Sid, length)
        finally:
            self.kernel.CloseHandle(token)
