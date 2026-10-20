"""Владение деревом внешнего процесса Windows через отдельный Job Object.

Создание приостановленного процесса атомарно включает его в job. Чтение канала
ограничено доступными байтами; завершение лидера не означает завершения дерева.
Win32 загружается лениво. Подмена native нужна для проверки границы на Mac,
но не подтверждает работу AutoCAD или квалификацию Windows-комплекта.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import Protocol

from app.shared.child_dll_search import child_dll_search

_WAIT_SLICE = 0.05
_CLOSE_TIMEOUT = 2.0


@dataclass(frozen=True)
class ProcessHandles:
    """Единственные process/thread handles, полученные при создании процесса."""

    pid: int
    process: int
    thread: int


class WindowsNative(Protocol):
    """Минимальная граница Win32, без поиска и завершения чужих процессов."""

    def create_job(self) -> int: ...
    def create_pipe(self) -> tuple[int, int]: ...
    def open_null_stdin(self) -> int: ...
    def create_suspended(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str] | None,
        stdin_handle: int,
        stdout_handle: int,
        job_handle: int,
    ) -> ProcessHandles: ...
    def is_in_job(self, process: int, job: int) -> bool: ...
    def resume(self, thread: int) -> None: ...
    def poll(self, process: int) -> int | None: ...
    def wait(self, process: int, timeout: float) -> int | None: ...
    def read_available(self, pipe: int, maxbytes: int) -> bytes: ...
    def active_processes(self, job: int) -> int: ...
    def terminate_job(self, job: int) -> None: ...
    def close_handle(self, handle: int) -> None: ...


class OwnedWindowsProcess:
    """Процесс и потомки принадлежат одному закрываемому job с самого создания.

    wait ожидает лидера, active_processes проверяет всё дерево. close завершает
    оставшихся потомков, ожидает освобождения и закрывает все собственные handles.
    После ошибки очистки все handles всё равно получают попытку закрытия.
    """

    def __init__(
        self, native: WindowsNative, handles: ProcessHandles, job: int, stdout: int
    ) -> None:
        self.pid = handles.pid
        self._native = native
        self._process = handles.process
        self._job = job
        self._stdout = stdout
        self._returncode: int | None = None
        self._terminated = False
        self._closed = False
        self._lock = RLock()

    @property
    def returncode(self) -> int | None:
        """Последний подтверждённый код лидера, без обращения к закрытому handle."""
        return self._returncode

    @property
    def closed(self) -> bool:
        """Владение handles уже передано очистке; повторная остановка не нужна."""
        return self._closed

    def poll(self) -> int | None:
        with self._lock:
            if self._returncode is None and not self._closed:
                self._returncode = self._native.poll(self._process)
            return self._returncode

    def read_available(self, maxbytes: int) -> bytes:
        if isinstance(maxbytes, bool) or not isinstance(maxbytes, int) or maxbytes < 1:
            raise ValueError("Размер чтения должен быть положительным целым числом")
        with self._lock:
            if self._closed:
                return b""
            return self._native.read_available(self._stdout, maxbytes)

    def active_processes(self) -> int:
        with self._lock:
            if self._closed:
                raise RuntimeError("Job Object уже закрыт")
            return self._native.active_processes(self._job)

    def terminate_all(self) -> None:
        """Отменяет только собственный job; повторная успешная отмена безопасна."""
        with self._lock:
            if not self._closed and not self._terminated:
                self._native.terminate_job(self._job)
                self._terminated = True

    def wait(self, timeout: float | None = None) -> int | None:
        if timeout is not None and (
            isinstance(timeout, bool) or not math.isfinite(timeout) or timeout < 0
        ):
            raise ValueError("Время ожидания должно быть конечным и неотрицательным")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                if self._returncode is not None or self._closed:
                    return self._returncode
                remaining = (
                    _WAIT_SLICE if deadline is None else deadline - time.monotonic()
                )
                self._returncode = self._native.wait(
                    self._process, max(0.0, min(_WAIT_SLICE, remaining))
                )
                if self._returncode is not None:
                    return self._returncode
            if deadline is not None and time.monotonic() >= deadline:
                return None

    def close(self) -> None:
        """Очищает дерево и все handles; повторный вызов ничего не запускает."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            errors: list[BaseException] = []
            try:
                if self._native.active_processes(self._job):
                    self._native.terminate_job(self._job)
                    self._terminated = True
            except BaseException as error:
                errors.append(error)
            deadline = time.monotonic() + _CLOSE_TIMEOUT
            try:
                self._returncode = self._native.wait(self._process, _CLOSE_TIMEOUT)
                if self._returncode is None:
                    raise TimeoutError("Лидер Windows-процесса не завершился")
                while self._native.active_processes(self._job):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Дерево Windows-процесса не завершилось")
                    time.sleep(0.01)
            except BaseException as error:
                errors.append(error)
            # Последний job handle также служит аварийной гарантией kill-on-close.
            for handle in (self._stdout, self._process, self._job):
                try:
                    self._native.close_handle(handle)
                except BaseException as error:
                    errors.append(error)
            if errors:
                for cleanup_error in errors[1:]:
                    errors[0].add_note(
                        f"Дополнительная ошибка очистки Windows-процесса: {cleanup_error!r}"
                    )
                raise errors[0]

    def __enter__(self) -> OwnedWindowsProcess:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def launch_owned(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
    native: WindowsNative | None = None,
) -> OwnedWindowsProcess:
    """Создаёт внешний процесс без окна, с собственным stdout/stderr и NUL stdin.

    Наследуются только два собственных handles. JOB_LIST обязателен: старые ОС
    или запрет вложенного job дают ошибку до запуска, без незащищённого fallback.
    Переданный environment полностью заменяет окружение, None наследует текущее.
    """
    if isinstance(command, str):
        raise ValueError("Команда должна содержать отдельные аргументы")
    command = tuple(command)
    if not command or not command[0] or any(
        not isinstance(part, str) or "\0" in part for part in command
    ):
        raise ValueError("Команда Windows-процесса некорректна")
    if "\0" in str(cwd):
        raise ValueError("Рабочий каталог Windows-процесса некорректен")
    if environment is not None:
        environment = dict(environment)
        names: set[str] = set()
        for key, value in environment.items():
            if (
                not isinstance(key, str)
                or not isinstance(value, str)
                or not key
                or "=" in key
                or "\0" in key
                or "\0" in value
                or key.casefold() in names
            ):
                raise ValueError("Окружение Windows-процесса некорректно")
            names.add(key.casefold())
    if native is None:
        from app.shared.windows_process_native import Win32Native

        native = Win32Native()
    allocated: list[int] = []
    job: int | None = None
    handles: ProcessHandles | None = None
    try:
        job = native.create_job()
        allocated.append(job)
        read, write = native.create_pipe()
        allocated.extend((read, write))
        stdin = native.open_null_stdin()
        allocated.append(stdin)
        # Контекст DLL-поиска охватывает только границу создания процесса.
        with child_dll_search(external=True):
            handles = native.create_suspended(command, cwd, environment, stdin, write, job)
            allocated.extend((handles.process, handles.thread))
        if not native.is_in_job(handles.process, job):
            raise OSError("Созданный процесс не принадлежит собственному job")
        for handle in (write, stdin):
            # После попытки CloseHandle числовое значение больше не используем:
            # ошибка адаптера могла возникнуть уже после освобождения handle.
            allocated.remove(handle)
            native.close_handle(handle)
        native.resume(handles.thread)
        allocated.remove(handles.thread)
        native.close_handle(handles.thread)
        return OwnedWindowsProcess(native, handles, job, read)
    except BaseException as original:
        errors: list[BaseException] = []
        if handles is not None and job is not None:
            try:
                native.terminate_job(job)
            except BaseException as error:
                # Закрытие последнего job handle остаётся аварийным завершением.
                errors.append(error)
            try:
                native.wait(handles.process, _CLOSE_TIMEOUT)
            except BaseException as error:
                errors.append(error)
        # Пытаемся закрыть каждый handle даже при ошибке одного CloseHandle.
        for handle in reversed(allocated):
            try:
                native.close_handle(handle)
            except BaseException as error:
                errors.append(error)
        for cleanup_error in errors:
            original.add_note(f"Ошибка очистки Windows-процесса: {cleanup_error!r}")
        raise
