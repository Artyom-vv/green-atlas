"""Ленивая Win32-граница для атомарного владения дочерним деревом.

Размеры скалярных типов фиксированы по Windows ABI и проверяются также на Mac.
HANDLE_LIST ограничивает наследование, JOB_LIST привязывает job при CreateProcessW.
"""

from __future__ import annotations

import ctypes
import math
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NoReturn

from app.shared.windows_process import ProcessHandles

DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
WORD = ctypes.c_uint16
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t
LONGLONG = ctypes.c_int64
ULONGLONG = ctypes.c_uint64
LPVOID = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p

HANDLE_LIST = 0x00020002
JOB_LIST = 0x0002000D
KILL_ON_JOB_CLOSE = 0x00002000
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_NO_WINDOW = 0x08000000
_PIPE_EOF = {109, 232, 233}
_MAX_READ = 1024 * 1024
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", DWORD), ("descriptor", LPVOID), ("inherit", BOOL)]


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("reserved", LPCWSTR),
        ("desktop", LPCWSTR),
        ("title", LPCWSTR),
        ("x", DWORD),
        ("y", DWORD),
        ("x_size", DWORD),
        ("y_size", DWORD),
        ("x_chars", DWORD),
        ("y_chars", DWORD),
        ("fill", DWORD),
        ("flags", DWORD),
        ("show", WORD),
        ("reserved_size", WORD),
        ("reserved_bytes", LPVOID),
        ("stdin", HANDLE),
        ("stdout", HANDLE),
        ("stderr", HANDLE),
    ]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", LPVOID)]


class ProcessInformation(ctypes.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", DWORD), ("tid", DWORD)]


class BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("process_time", LONGLONG),
        ("job_time", LONGLONG),
        ("flags", DWORD),
        ("minimum_working_set", SIZE_T),
        ("maximum_working_set", SIZE_T),
        ("active_process_limit", DWORD),
        ("affinity", ULONG_PTR),
        ("priority", DWORD),
        ("scheduling", DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operations", ULONGLONG),
        ("write_operations", ULONGLONG),
        ("other_operations", ULONGLONG),
        ("read_bytes", ULONGLONG),
        ("write_bytes", ULONGLONG),
        ("other_bytes", ULONGLONG),
    ]


class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimitInformation),
        ("io", IoCounters),
        ("process_memory_limit", SIZE_T),
        ("job_memory_limit", SIZE_T),
        ("peak_process_memory", SIZE_T),
        ("peak_job_memory", SIZE_T),
    ]


class AccountingInformation(ctypes.Structure):
    _fields_ = [
        ("user_time", LONGLONG),
        ("kernel_time", LONGLONG),
        ("period_user_time", LONGLONG),
        ("period_kernel_time", LONGLONG),
        ("page_faults", DWORD),
        ("total_processes", DWORD),
        ("active_processes", DWORD),
        ("terminated_processes", DWORD),
    ]


def _last_error() -> int:
    return int(getattr(ctypes, "get_last_error", lambda: 0)())


def _error(message: str) -> OSError:
    code = _last_error()
    return OSError(code, message)


class Win32Native:
    """Нативные операции; kernel подменяется только в тестах структуры вызовов."""

    def __init__(self, *, kernel: Any | None = None) -> None:
        if kernel is None:
            if os.name != "nt":
                raise OSError("Запуск Windows-процесса доступен только в Windows")
            kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        self._kernel = kernel
        signatures: dict[str, tuple[tuple[Any, ...], Any]] = {
            "CreateJobObjectW": ((LPVOID, LPCWSTR), HANDLE),
            "SetInformationJobObject": ((HANDLE, ctypes.c_int, LPVOID, DWORD), BOOL),
            "QueryInformationJobObject": (
                (HANDLE, ctypes.c_int, LPVOID, DWORD, ctypes.POINTER(DWORD)), BOOL
            ),
            "TerminateJobObject": ((HANDLE, DWORD), BOOL),
            "IsProcessInJob": ((HANDLE, HANDLE, ctypes.POINTER(BOOL)), BOOL),
            "CreatePipe": (
                (ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE),
                 ctypes.POINTER(SecurityAttributes), DWORD), BOOL
            ),
            "SetHandleInformation": ((HANDLE, DWORD, DWORD), BOOL),
            "CreateFileW": (
                (LPCWSTR, DWORD, DWORD, ctypes.POINTER(SecurityAttributes),
                 DWORD, DWORD, HANDLE), HANDLE
            ),
            "InitializeProcThreadAttributeList": (
                (LPVOID, DWORD, DWORD, ctypes.POINTER(SIZE_T)), BOOL
            ),
            "UpdateProcThreadAttribute": (
                (LPVOID, DWORD, ULONG_PTR, LPVOID, SIZE_T, LPVOID,
                 ctypes.POINTER(SIZE_T)), BOOL
            ),
            "DeleteProcThreadAttributeList": ((LPVOID,), None),
            "CreateProcessW": (
                (LPCWSTR, ctypes.c_wchar_p, LPVOID, LPVOID, BOOL, DWORD, LPVOID,
                 LPCWSTR, ctypes.POINTER(StartupInfoEx),
                 ctypes.POINTER(ProcessInformation)), BOOL
            ),
            "ResumeThread": ((HANDLE,), DWORD),
            "WaitForSingleObject": ((HANDLE, DWORD), DWORD),
            "GetExitCodeProcess": ((HANDLE, ctypes.POINTER(DWORD)), BOOL),
            "PeekNamedPipe": (
                (HANDLE, LPVOID, DWORD, ctypes.POINTER(DWORD),
                 ctypes.POINTER(DWORD), ctypes.POINTER(DWORD)), BOOL
            ),
            "ReadFile": (
                (HANDLE, LPVOID, DWORD, ctypes.POINTER(DWORD), LPVOID), BOOL
            ),
            "CloseHandle": ((HANDLE,), BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(kernel, name)
            function.argtypes = arguments
            function.restype = result

    def close_handle(self, handle: int) -> None:
        if not self._kernel.CloseHandle(handle):
            raise _error("Не удалось закрыть Windows handle")

    def _close_on_error(self, error: OSError, *handles: int) -> NoReturn:
        for handle in handles:
            try:
                self.close_handle(handle)
            except BaseException as cleanup_error:
                error.add_note(f"Ошибка закрытия Windows handle: {cleanup_error!r}")
        raise error

    def create_job(self) -> int:
        job = self._kernel.CreateJobObjectW(None, None)
        if not job:
            raise _error("Не удалось создать Job Object")
        limits = ExtendedLimitInformation()
        limits.basic.flags = KILL_ON_JOB_CLOSE
        if not self._kernel.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = _error("Не удалось включить kill-on-close для Job Object")
            self._close_on_error(error, int(job))
        return int(job)

    def create_pipe(self) -> tuple[int, int]:
        read, write = HANDLE(), HANDLE()
        security = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
        if not self._kernel.CreatePipe(
            ctypes.byref(read), ctypes.byref(write), ctypes.byref(security), 0
        ):
            raise _error("Не удалось создать stdout pipe")
        if not self._kernel.SetHandleInformation(read, 1, 0):
            error = _error("Не удалось запретить наследование читающего pipe")
            self._close_on_error(error, int(read.value or 0), int(write.value or 0))
        return int(read.value or 0), int(write.value or 0)

    def open_null_stdin(self) -> int:
        security = SecurityAttributes(ctypes.sizeof(SecurityAttributes), None, True)
        handle = self._kernel.CreateFileW(
            "NUL", 0x80000000, 3, ctypes.byref(security), 3, 0x80, None
        )
        if handle is None or handle == _INVALID_HANDLE:
            raise _error("Не удалось открыть NUL для stdin")
        return int(handle)

    def create_suspended(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str] | None,
        stdin_handle: int,
        stdout_handle: int,
        job_handle: int,
    ) -> ProcessHandles:
        size = SIZE_T()
        initialized = self._kernel.InitializeProcThreadAttributeList(
            None, 2, 0, ctypes.byref(size)
        )
        if initialized or _last_error() != 122 or not size.value:
            raise _error("Не удалось определить размер STARTUPINFOEX")
        storage = ctypes.create_string_buffer(size.value)
        attributes = ctypes.cast(storage, LPVOID)
        if not self._kernel.InitializeProcThreadAttributeList(
            attributes, 2, 0, ctypes.byref(size)
        ):
            raise _error("Не удалось создать список STARTUPINFOEX")
        try:
            inherited = (HANDLE * 2)(stdin_handle, stdout_handle)
            jobs = (HANDLE * 1)(job_handle)
            for kind, values in ((HANDLE_LIST, inherited), (JOB_LIST, jobs)):
                if not self._kernel.UpdateProcThreadAttribute(
                    attributes, 0, kind, ctypes.cast(values, LPVOID),
                    ctypes.sizeof(values), None, None
                ):
                    raise _error("Не удалось установить обязательный атрибут процесса")
            startup = StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.flags = 0x100
            startup.startup.stdin = stdin_handle
            startup.startup.stdout = stdout_handle
            startup.startup.stderr = stdout_handle
            startup.attributes = attributes
            info = ProcessInformation()
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
            env_buffer = None
            if environment is not None:
                block = "\0".join(
                    f"{key}={environment[key]}" for key in sorted(environment, key=str.upper)
                ) + "\0\0"
                env_buffer = ctypes.create_unicode_buffer(block)
            flags = (
                CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
                | EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW
            )
            if not self._kernel.CreateProcessW(
                command[0], command_line, None, None, True, flags,
                ctypes.cast(env_buffer, LPVOID) if env_buffer is not None else None,
                str(cwd), ctypes.byref(startup), ctypes.byref(info)
            ):
                raise _error("Не удалось создать принадлежащий job процесс")
            return ProcessHandles(info.pid, int(info.process or 0), int(info.thread or 0))
        finally:
            self._kernel.DeleteProcThreadAttributeList(attributes)

    def is_in_job(self, process: int, job: int) -> bool:
        belongs = BOOL()
        if not self._kernel.IsProcessInJob(process, job, ctypes.byref(belongs)):
            raise _error("Не удалось проверить принадлежность процесса job")
        return bool(belongs.value)

    def resume(self, thread: int) -> None:
        count = self._kernel.ResumeThread(thread)
        if count == 0xFFFFFFFF:
            raise _error("Не удалось продолжить основной поток процесса")
        if count != 1:
            raise OSError("Неожиданное состояние приостановленного потока")

    def poll(self, process: int) -> int | None:
        return self.wait(process, 0.0)

    def wait(self, process: int, timeout: float) -> int | None:
        milliseconds = min(math.ceil(timeout * 1000), 0xFFFFFFFE)
        state = self._kernel.WaitForSingleObject(process, milliseconds)
        if state == 258:
            return None
        if state != 0:
            raise _error("Не удалось дождаться Windows-процесса")
        code = DWORD()
        if not self._kernel.GetExitCodeProcess(process, ctypes.byref(code)):
            raise _error("Не удалось получить код Windows-процесса")
        # Код 259 допустим после сигнала process handle и не означает STILL_ACTIVE.
        return int(code.value)

    def read_available(self, pipe: int, maxbytes: int) -> bytes:
        available = DWORD()
        if not self._kernel.PeekNamedPipe(
            pipe, None, 0, None, ctypes.byref(available), None
        ):
            if _last_error() in _PIPE_EOF:
                return b""
            raise _error("Не удалось прочитать состояние stdout pipe")
        amount = min(maxbytes, available.value, _MAX_READ)
        if not amount:
            return b""
        buffer = ctypes.create_string_buffer(amount)
        read = DWORD()
        if not self._kernel.ReadFile(pipe, buffer, amount, ctypes.byref(read), None):
            if _last_error() in _PIPE_EOF:
                return b""
            raise _error("Не удалось прочитать stdout pipe")
        return buffer.raw[:read.value]

    def active_processes(self, job: int) -> int:
        accounting = AccountingInformation()
        if not self._kernel.QueryInformationJobObject(
            job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
        ):
            raise _error("Не удалось проверить дерево Windows-процесса")
        return int(accounting.active_processes)

    def terminate_job(self, job: int) -> None:
        if not self._kernel.TerminateJobObject(job, 1):
            raise _error("Не удалось завершить собственный Job Object")
