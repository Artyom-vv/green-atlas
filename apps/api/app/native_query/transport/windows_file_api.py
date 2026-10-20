"""Узкая Win32-граница файлов Core; импорт на Mac не загружает системные DLL.

Идентичность берётся из открытого HANDLE, включая 128-битный FileIdInfo для ReFS.
Документация: https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-createfilew
https://learn.microsoft.com/windows/win32/api/fileapi/ns-fileapi-by_handle_file_information
https://learn.microsoft.com/windows/win32/api/winbase/ns-winbase-file_id_info
"""

from __future__ import annotations

import ctypes
import io
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, cast

FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
FILE_SHARE_DELETE = 4
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
CREATE_NEW = 1
FILE_TYPE_DISK = 1
FILE_ID_INFO_CLASS = 18
FILE_BASIC_INFO_CLASS = 0


@dataclass(frozen=True)
class FileInformation:
    volume_serial: int
    file_id: bytes
    links: int
    size: int
    attributes: int
    last_write: int
    disk: bool = True
    change_time: int = 0

    @property
    def object_id(self) -> tuple[int, bytes]:
        return self.volume_serial, self.file_id


class FileApi(Protocol):
    def open(
        self,
        path: Path,
        *,
        directory: bool = False,
        writable: bool = False,
        create: bool = False,
        metadata: bool = False,
    ) -> int: ...

    def information(self, handle: int) -> FileInformation: ...

    def close(self, handle: int) -> None: ...

    def to_stream(self, handle: int, *, writable: bool) -> io.FileIO:
        """Принимает владение HANDLE, включая ошибку преобразования в поток."""
        ...


# Win32 DWORD всегда 32-битный, независимо от размера C long на хосте тестов.
class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _ByHandleInformation(ctypes.Structure):
    _fields_ = [
        ("attributes", ctypes.c_uint32),
        ("created", _FileTime),
        ("accessed", _FileTime),
        ("written", _FileTime),
        ("volume", ctypes.c_uint32),
        ("size_high", ctypes.c_uint32),
        ("size_low", ctypes.c_uint32),
        ("links", ctypes.c_uint32),
        ("index_high", ctypes.c_uint32),
        ("index_low", ctypes.c_uint32),
    ]


class _FileIdInformation(ctypes.Structure):
    _fields_ = [("volume", ctypes.c_uint64), ("identifier", ctypes.c_ubyte * 16)]


class _FileBasicInformation(ctypes.Structure):
    _fields_ = [
        ("created", ctypes.c_int64),
        ("accessed", ctypes.c_int64),
        ("written", ctypes.c_int64),
        ("changed", ctypes.c_int64),
        ("attributes", ctypes.c_uint32),
    ]


class WindowsFileApi:
    _kernel: Any

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Win32 file handles require Windows")
        # Загрузка DLL и msvcrt происходит только при первом Windows-вызове.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel = kernel
        kernel.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.GetFileInformationByHandle.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_ByHandleInformation),
        ]
        kernel.GetFileInformationByHandle.restype = ctypes.c_int32
        kernel.GetFileInformationByHandleEx.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        kernel.GetFileInformationByHandleEx.restype = ctypes.c_int32
        kernel.GetFileType.argtypes = [ctypes.c_void_p]
        kernel.GetFileType.restype = ctypes.c_uint32
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int32

    def open(
        self,
        path: Path,
        *,
        directory: bool = False,
        writable: bool = False,
        create: bool = False,
        metadata: bool = False,
    ) -> int:
        access = 0 if directory or metadata else GENERIC_READ
        if writable:
            access |= GENERIC_WRITE
        if directory:
            sharing = FILE_SHARE_READ | FILE_SHARE_WRITE
        elif metadata:
            # Наблюдение .tmp не должно мешать собственной атомарной публикации.
            sharing = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        else:
            sharing = 0 if writable else FILE_SHARE_READ
        handle = self._kernel.CreateFileW(
            _extended_path(path),
            access,
            sharing,
            None,
            CREATE_NEW if create else OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == ctypes.c_void_p(-1).value:
            raise _windows_error()
        return int(handle)

    def information(self, handle: int) -> FileInformation:
        basic, identity, timestamps = (
            _ByHandleInformation(),
            _FileIdInformation(),
            _FileBasicInformation(),
        )
        if not self._kernel.GetFileInformationByHandle(handle, ctypes.byref(basic)):
            raise _windows_error()
        if not self._kernel.GetFileInformationByHandleEx(
            handle, FILE_ID_INFO_CLASS, ctypes.byref(identity), ctypes.sizeof(identity)
        ):
            # Не подменять Windows identity значением POSIX st_ino или усечённым ReFS ID.
            raise _windows_error()
        if not self._kernel.GetFileInformationByHandleEx(
            handle,
            FILE_BASIC_INFO_CLASS,
            ctypes.byref(timestamps),
            ctypes.sizeof(timestamps),
        ):
            raise _windows_error()
        return FileInformation(
            volume_serial=identity.volume,
            file_id=bytes(identity.identifier),
            links=basic.links,
            size=(basic.size_high << 32) | basic.size_low,
            attributes=basic.attributes,
            last_write=(basic.written.high << 32) | basic.written.low,
            disk=self._kernel.GetFileType(handle) == FILE_TYPE_DISK,
            change_time=timestamps.changed,
        )

    def close(self, handle: int) -> None:
        if not self._kernel.CloseHandle(handle):
            raise _windows_error()

    def to_stream(self, handle: int, *, writable: bool) -> io.FileIO:
        try:
            import msvcrt

            windows_os: Any = os
            windows_msvcrt: Any = msvcrt
            flags = (
                (os.O_RDWR if writable else os.O_RDONLY)
                | windows_os.O_BINARY
                | windows_os.O_NOINHERIT
            )
            fd = windows_msvcrt.open_osfhandle(handle, flags)
        except BaseException:
            self.close(handle)
            raise
        try:
            return io.FileIO(fd, "r+" if writable else "r", closefd=True)
        except BaseException:
            os.close(fd)
            raise


def _extended_path(path: Path) -> str:
    # Пользовательский device namespace отклоняется до этой внутренней конверсии.
    value = str(path)
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _windows_error() -> OSError:
    windows_ctypes: Any = ctypes
    return cast(OSError, windows_ctypes.WinError(windows_ctypes.get_last_error()))


@lru_cache(maxsize=1)
def native_file_api() -> FileApi:
    return WindowsFileApi()
