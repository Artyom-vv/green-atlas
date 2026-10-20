"""Поиск DLL дочернего процесса: собственный frozen worker использует комплект,
внешняя программа — системные библиотеки Windows. Владение процессом и его
завершение остаются обязанностью вызывающего сценария.
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock

_spawn_lock = RLock()


@contextmanager
def child_dll_search(*, external: bool) -> Iterator[None]:
    """Ограничивает временный системный DLL-поиск созданием внешнего процесса.

    Подключённые frozen-запуски Windows используют один lock. Ожидание, чтение ответа
    и отмена происходят после выхода из контекста. Mac и обычный Python не
    меняют окружение и не обращаются к Windows API.
    """
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    with _spawn_lock:
        if not external:
            yield
            return
        previous = _get_directory()
        _set_directory(None)
        try:
            yield
        finally:
            # Восстанавливаем фактический путь, а не предполагаемый _MEIPASS:
            # вложенный внешний запуск тоже должен вернуть своё исходное состояние.
            _set_directory(previous)


def _get_directory() -> str | None:
    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    get_directory = kernel.GetDllDirectoryW
    get_directory.argtypes = (ctypes.c_uint, ctypes.c_wchar_p)
    get_directory.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    getattr(ctypes, "set_last_error")(0)
    length = get_directory(len(buffer), buffer)
    error = getattr(ctypes, "get_last_error")()
    if (length == 0 and error) or length >= len(buffer):
        raise OSError(error, "Не удалось прочитать путь DLL перед запуском")
    return buffer.value if length else None


def _set_directory(directory: str | None) -> None:
    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    set_directory = kernel.SetDllDirectoryW
    set_directory.argtypes = (ctypes.c_wchar_p,)
    set_directory.restype = ctypes.c_int
    if not set_directory(directory):
        raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
