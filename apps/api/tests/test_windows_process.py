"""Проверки владения Windows-процессом без запуска CAD и реального Win32 на Mac."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from app.shared import windows_process as owned
from app.shared import windows_process_native as win


class FakeNative:
    def __init__(self, fail: str = "") -> None:
        self.events: list[tuple[Any, ...]] = []
        self.fail = fail
        self.exit_code: int | None = None
        self.active = 1
        self.closed: list[int] = []
        self.output = b"stdout"

    def event(self, name: str, *args: Any) -> None:
        self.events.append((name, *args))
        if self.fail == name:
            raise OSError(name)

    def create_job(self) -> int:
        self.event("job")
        return 11

    def create_pipe(self) -> tuple[int, int]:
        self.event("pipe")
        return 12, 13

    def open_null_stdin(self) -> int:
        self.event("stdin")
        return 14

    def create_suspended(
        self, command: Sequence[str], cwd: Path,
        environment: Mapping[str, str] | None,
        stdin_handle: int, stdout_handle: int, job_handle: int,
    ) -> owned.ProcessHandles:
        self.event("create", tuple(command), cwd, environment,
                   stdin_handle, stdout_handle, job_handle)
        return owned.ProcessHandles(123, 15, 16)

    def is_in_job(self, process: int, job: int) -> bool:
        self.event("membership", process, job)
        return self.fail != "not-member"

    def resume(self, thread: int) -> None:
        self.event("resume", thread)

    def poll(self, process: int) -> int | None:
        self.event("poll", process)
        return self.exit_code

    def wait(self, process: int, timeout: float) -> int | None:
        self.event("wait", process, timeout)
        return self.exit_code

    def read_available(self, pipe: int, maxbytes: int) -> bytes:
        self.event("read", pipe, maxbytes)
        result, self.output = self.output[:maxbytes], self.output[maxbytes:]
        return result

    def active_processes(self, job: int) -> int:
        self.event("active", job)
        return self.active

    def terminate_job(self, job: int) -> None:
        self.event("terminate", job)
        self.active = 0
        if self.exit_code is None:
            self.exit_code = 1

    def close_handle(self, handle: int) -> None:
        self.closed.append(handle)
        self.event("close", handle)


def launch(native: FakeNative) -> owned.OwnedWindowsProcess:
    return owned.launch_owned(["worker.exe", "a b"], cwd=Path("work"), native=native)


def test_creation_is_owned_before_resume_and_all_handles_close_once() -> None:
    native = FakeNative()
    child = launch(native)
    assert child.pid == 123
    assert not child.closed
    assert [event[0] for event in native.events] == [
        "job", "pipe", "stdin", "create", "membership", "close", "close",
        "resume", "close",
    ]
    assert native.events[3][-3:] == (14, 13, 11)
    assert native.closed == [13, 14, 16]
    child.close()
    assert child.closed
    assert sorted(native.closed) == [11, 12, 13, 14, 15, 16]
    events = list(native.events)
    child.close()
    child.terminate_all()
    assert native.events == events
    assert child.returncode == 1
    assert child.poll() == 1
    assert child.read_available(10) == b""
    with pytest.raises(RuntimeError, match="закрыт"):
        child.active_processes()


@pytest.mark.parametrize(
    ("failure", "allocated", "has_process"),
    [
        ("job", [], False),
        ("pipe", [11], False),
        ("stdin", [11, 12, 13], False),
        ("create", [11, 12, 13, 14], False),
        ("membership", [11, 12, 13, 14, 15, 16], True),
        ("not-member", [11, 12, 13, 14, 15, 16], True),
        ("resume", [11, 12, 13, 14, 15, 16], True),
    ],
)
def test_failed_launch_cleans_only_its_handles(
    failure: str, allocated: list[int], has_process: bool
) -> None:
    native = FakeNative(failure)
    with pytest.raises((OSError, RuntimeError)):
        launch(native)
    assert sorted(native.closed) == allocated
    assert any(event[0] == "terminate" for event in native.events) is has_process
    if failure != "resume":
        assert not any(event[0] == "resume" for event in native.events)


def test_negative_job_membership_is_os_launch_error() -> None:
    with pytest.raises(OSError, match="не принадлежит"):
        launch(FakeNative("not-member"))


@pytest.mark.parametrize("stage", ["reset", "restore"])
def test_dll_search_failure_does_not_leak_suspended_child(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    @contextmanager
    def guard(*, external: bool) -> Iterator[None]:
        assert external
        if stage == "reset":
            raise OSError("reset")
        yield
        raise OSError("restore")

    monkeypatch.setattr(owned, "child_dll_search", guard)
    native = FakeNative()
    with pytest.raises(OSError, match=stage):
        launch(native)
    assert sorted(native.closed) == ([11, 12, 13, 14] if stage == "reset" else
                                     [11, 12, 13, 14, 15, 16])
    assert not any(event[0] == "resume" for event in native.events)
    assert any(event[0] == "terminate" for event in native.events) is (stage == "restore")


def test_leader_exit_does_not_hide_child_and_poll_caches_code() -> None:
    native = FakeNative()
    child = launch(native)
    native.exit_code = 259
    assert child.poll() == 259
    assert child.returncode == 259
    assert child.active_processes() == 1
    polls = len([event for event in native.events if event[0] == "poll"])
    assert child.poll() == 259
    assert len([event for event in native.events if event[0] == "poll"]) == polls
    child.terminate_all()
    child.terminate_all()
    assert child.active_processes() == 0
    assert len([event for event in native.events if event[0] == "terminate"]) == 1
    child.close()


def test_wait_timeout_and_bounded_reads() -> None:
    native = FakeNative()
    with launch(native) as child:
        assert child.wait(0) is None
        assert child.read_available(2) == b"st"
        assert child.read_available(2) == b"do"
        assert child.read_available(100) == b"ut"
        assert child.read_available(100) == b""
        for invalid in (0, -1, True):
            with pytest.raises(ValueError):
                child.read_available(invalid)
        for invalid_timeout in (-1, float("nan"), float("inf"), True):
            with pytest.raises(ValueError):
                child.wait(invalid_timeout)


@pytest.mark.parametrize("failure", ["active", "terminate", "wait", "close"])
def test_close_attempts_all_handles_even_when_cleanup_fails(failure: str) -> None:
    native = FakeNative()
    child = launch(native)
    native.fail = failure
    with pytest.raises(OSError, match=failure):
        child.close()
    assert native.closed[-3:] == [12, 15, 11]
    previous = list(native.events)
    child.close()
    assert native.events == previous


@pytest.mark.parametrize("leader_alive", [False, True])
def test_cleanup_timeout_still_closes_all_handles(
    monkeypatch: pytest.MonkeyPatch, leader_alive: bool
) -> None:
    class HangingChild(FakeNative):
        def terminate_job(self, job: int) -> None:
            self.event("terminate", job)

    native = HangingChild()
    child = launch(native)
    monkeypatch.setattr(owned, "_CLOSE_TIMEOUT", 0)
    native.exit_code = None if leader_alive else 0
    with pytest.raises(TimeoutError):
        child.close()
    assert child.closed
    assert native.closed[-3:] == [12, 15, 11]
    assert child.poll() == native.exit_code


def test_failed_cancellation_can_be_retried() -> None:
    native = FakeNative()
    with launch(native) as child:
        native.fail = "terminate"
        with pytest.raises(OSError):
            child.terminate_all()
        native.fail = ""
        child.terminate_all()
        assert child.active_processes() == 0


@pytest.mark.parametrize("consumed", [13, 14, 16])
def test_launch_never_retries_consumed_close_handle(consumed: int) -> None:
    class ConsumingClose(FakeNative):
        def close_handle(self, handle: int) -> None:
            super().close_handle(handle)
            if handle == consumed:
                raise OSError("ошибка после освобождения handle")

    native = ConsumingClose()
    with pytest.raises(OSError, match="после освобождения"):
        launch(native)
    assert sorted(native.closed) == [11, 12, 13, 14, 15, 16]
    assert native.closed.count(consumed) == 1


def test_failed_launch_keeps_cleanup_errors_as_notes() -> None:
    class FailingCleanup(FakeNative):
        def close_handle(self, handle: int) -> None:
            super().close_handle(handle)
            raise OSError("cleanup")

    native = FailingCleanup("create")
    with pytest.raises(OSError, match="create") as raised:
        launch(native)
    assert len(raised.value.__notes__) == 4
    assert sorted(native.closed) == [11, 12, 13, 14]


@pytest.mark.parametrize(
    ("command", "environment"),
    [([], None), ([""], None), (["bad\0.exe"], None),
     (["worker"], {"PATH": "x", "Path": "y"}),
     (["worker"], {"A=B": "value"}), (["worker"], {"A": "x\0y"})],
)
def test_invalid_launch_arguments_allocate_nothing(
    command: list[str], environment: dict[str, str] | None
) -> None:
    native = FakeNative()
    with pytest.raises(ValueError):
        owned.launch_owned(command, cwd=Path("work"), environment=environment,
                           native=native)
    assert native.events == []


def test_module_import_does_not_load_windows_dll_on_mac() -> None:
    if os.name == "nt":
        pytest.skip("Проверка отсутствия WinDLL относится к Mac")
    with pytest.raises(OSError, match="только в Windows"):
        owned.launch_owned(["worker"], cwd=Path("work"))


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="Контракт Windows x64")
def test_windows_x64_abi_uses_fixed_width_types_on_all_hosts() -> None:
    assert ctypes.sizeof(win.DWORD) == ctypes.sizeof(win.BOOL) == 4
    assert ctypes.sizeof(win.SecurityAttributes) == 24
    assert ctypes.sizeof(win.StartupInfo) == 104
    assert ctypes.sizeof(win.StartupInfoEx) == 112
    assert ctypes.sizeof(win.ProcessInformation) == 24
    assert ctypes.sizeof(win.BasicLimitInformation) == 64
    assert ctypes.sizeof(win.IoCounters) == 48
    assert ctypes.sizeof(win.ExtendedLimitInformation) == 144
    assert ctypes.sizeof(win.AccountingInformation) == 48
    assert win.StartupInfo.stdin.offset == 80
    assert win.BasicLimitInformation.flags.offset == 16
    assert win.AccountingInformation.active_processes.offset == 40


class NativeFunction:
    def __init__(self, callback: Any = None) -> None:
        self.callback = callback
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        return True if self.callback is None else self.callback(*args)


class FakeKernel:
    def __init__(self) -> None:
        self.functions: dict[str, NativeFunction] = {}
        self.updates: dict[int, tuple[int, ...]] = {}
        self.calls: list[str] = []
        self.payload = b""
        self.available = 0
        self.exit_code = 259
        self.wait_state = 0
        self.startup: tuple[Any, ...] = ()
        self.environment = ""
        self.failure = ""
        self.closed: list[int] = []
        self.limit_flags = 0

    def __getattr__(self, name: str) -> NativeFunction:
        if name not in self.functions:
            self.functions[name] = NativeFunction(
                lambda *args: self.call(name, *args)
            )
        return self.functions[name]

    def call(self, name: str, *args: Any) -> Any:
        self.calls.append(name)
        if name == self.failure:
            return False
        if name == "CreateJobObjectW":
            return 101
        if name == "SetInformationJobObject":
            limits = ctypes.cast(args[2], ctypes.POINTER(win.ExtendedLimitInformation))
            self.limit_flags = limits.contents.basic.flags
        elif name == "CreatePipe":
            ctypes.cast(args[0], ctypes.POINTER(win.HANDLE))[0] = 102
            ctypes.cast(args[1], ctypes.POINTER(win.HANDLE))[0] = 103
        elif name == "CreateFileW":
            return 104
        elif name == "CloseHandle":
            self.closed.append(args[0])
        elif name == "InitializeProcThreadAttributeList":
            ctypes.cast(args[3], ctypes.POINTER(win.SIZE_T))[0] = 128
            return args[0] is not None
        elif name == "UpdateProcThreadAttribute":
            values = ctypes.cast(args[3], ctypes.POINTER(win.HANDLE))
            self.updates[args[2]] = tuple(values[i] for i in range(args[4] // 8))
        elif name == "CreateProcessW":
            startup = ctypes.cast(args[8], ctypes.POINTER(win.StartupInfoEx)).contents
            self.startup = (args[0], args[1].value, args[4], args[5],
                            startup.startup.cb, startup.startup.flags,
                            startup.startup.stdin, startup.startup.stdout,
                            startup.startup.stderr)
            if args[6]:
                env_values = ctypes.cast(args[6], ctypes.POINTER(ctypes.c_wchar))
                index = 0
                while not (env_values[index] == "\0" and env_values[index + 1] == "\0"):
                    self.environment += env_values[index]
                    index += 1
            info = ctypes.cast(args[9], ctypes.POINTER(win.ProcessInformation)).contents
            info.process, info.thread, info.pid = 105, 106, 123
        elif name == "ResumeThread":
            return 1
        elif name == "WaitForSingleObject":
            return self.wait_state
        elif name == "GetExitCodeProcess":
            ctypes.cast(args[1], ctypes.POINTER(win.DWORD))[0] = self.exit_code
        elif name == "PeekNamedPipe":
            ctypes.cast(args[4], ctypes.POINTER(win.DWORD))[0] = self.available
        elif name == "ReadFile":
            size = min(args[2], len(self.payload))
            ctypes.memmove(args[1], self.payload, size)
            ctypes.cast(args[3], ctypes.POINTER(win.DWORD))[0] = size
        return True


def native_kernel(monkeypatch: pytest.MonkeyPatch) -> tuple[win.Win32Native, FakeKernel]:
    monkeypatch.setattr(win, "_last_error", lambda: 122)
    kernel = FakeKernel()
    return win.Win32Native(kernel=kernel), kernel


def test_atomic_attributes_and_only_own_handles_are_inherited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native, kernel = native_kernel(monkeypatch)
    assert native.create_job() == 101
    assert kernel.limit_flags == win.KILL_ON_JOB_CLOSE
    assert native.create_pipe() == (102, 103)
    assert native.open_null_stdin() == 104
    command = [r"C:\Program Files\AutoCAD\accoreconsole.exe", "a b", 'c"d']
    handles = native.create_suspended(command, Path("work"),
                                      {"z": "последний", "A": "первый"},
                                      104, 103, 101)
    assert handles == owned.ProcessHandles(123, 105, 106)
    assert kernel.updates == {0x00020002: (104, 103), 0x0002000D: (101,)}
    flags = (win.CREATE_SUSPENDED | win.CREATE_UNICODE_ENVIRONMENT
             | win.EXTENDED_STARTUPINFO_PRESENT | win.CREATE_NO_WINDOW)
    assert kernel.startup == (command[0], subprocess.list2cmdline(command), True,
                              flags, 112, 0x100, 104, 103, 103)
    assert kernel.environment == "A=первый\0z=последний"
    assert kernel.calls.index("SetInformationJobObject") < kernel.calls.index("CreateProcessW")
    assert kernel.calls[-1] == "DeleteProcThreadAttributeList"
    assert "AssignProcessToJobObject" not in kernel.calls
    assert "ResumeThread" not in kernel.calls


@pytest.mark.parametrize("failure", ["UpdateProcThreadAttribute", "CreateProcessW"])
def test_required_attributes_fail_closed_without_creation_fallback(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    native, kernel = native_kernel(monkeypatch)
    kernel.failure = failure
    with pytest.raises(OSError):
        native.create_suspended(["worker.exe"], Path("work"), None, 104, 103, 101)
    assert kernel.calls[-1] == "DeleteProcThreadAttributeList"
    assert kernel.calls.count("CreateProcessW") == (failure == "CreateProcessW")


def test_native_partial_handle_allocations_are_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native, kernel = native_kernel(monkeypatch)
    kernel.failure = "SetInformationJobObject"
    with pytest.raises(OSError):
        native.create_job()
    assert kernel.closed == [101]
    kernel.failure = "SetHandleInformation"
    with pytest.raises(OSError):
        native.create_pipe()
    assert kernel.closed == [101, 102, 103]


def test_native_partial_cleanup_retains_original_error_and_attempts_each_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native, kernel = native_kernel(monkeypatch)
    kernel.failure = "SetHandleInformation"
    consumed: list[int] = []

    def close_consumed(handle: int) -> bool:
        consumed.append(handle)
        return False

    kernel.CloseHandle.callback = close_consumed
    with pytest.raises(OSError, match="запретить наследование") as raised:
        native.create_pipe()
    assert consumed == [102, 103]
    assert len(raised.value.__notes__) == 2


def test_native_wait_requires_signal_even_for_still_active_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native, kernel = native_kernel(monkeypatch)
    kernel.wait_state = 258
    assert native.poll(105) is None
    assert "GetExitCodeProcess" not in kernel.calls
    kernel.wait_state = 0
    assert native.poll(105) == 259


@pytest.mark.parametrize("count", [0, 2, 0xFFFFFFFF])
def test_unexpected_suspend_count_is_os_launch_error(
    monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    native, kernel = native_kernel(monkeypatch)
    kernel.ResumeThread.callback = lambda handle: count
    with pytest.raises(OSError):
        native.resume(106)


def test_native_pipe_read_is_bounded_and_never_reads_empty_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native, kernel = native_kernel(monkeypatch)
    assert native.read_available(102, 10) == b""
    assert "ReadFile" not in kernel.calls
    kernel.available = 1000
    kernel.payload = b"abcdef"
    assert native.read_available(102, 3) == b"abc"
    kernel.available = 2
    assert native.read_available(102, 100) == b"ab"


@pytest.mark.parametrize("error", [109, 232, 233])
def test_closed_pipe_is_eof(monkeypatch: pytest.MonkeyPatch, error: int) -> None:
    native, kernel = native_kernel(monkeypatch)
    kernel.failure = "PeekNamedPipe"
    monkeypatch.setattr(win, "_last_error", lambda: error)
    assert native.read_available(102, 100) == b""


@pytest.mark.skipif(os.name != "nt", reason="Реальный Job Object требует Windows")
def test_windows_smoke_leader_exit_retains_owned_child(tmp_path: Path) -> None:
    code = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(p.pid,flush=True)"
    )
    with owned.launch_owned([sys.executable, "-c", code], cwd=tmp_path) as child:
        assert child.wait(10) == 0
        assert child.active_processes() >= 1
        assert child.read_available(1024).strip().isdigit()
        child.terminate_all()
        deadline = time.monotonic() + 5
        while child.active_processes() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child.active_processes() == 0


@pytest.mark.skipif(os.name != "nt", reason="Реальный Job Object требует Windows")
def test_windows_smoke_cancel_closes_live_tree(tmp_path: Path) -> None:
    code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print('ready',flush=True); time.sleep(60)"
    )
    child = owned.launch_owned(
        [sys.executable, "-c", code], cwd=tmp_path,
    )
    try:
        deadline = time.monotonic() + 10
        output = bytearray()
        while b"ready" not in output and time.monotonic() < deadline:
            output.extend(child.read_available(1024))
            time.sleep(0.01)
        assert b"ready" in output
        assert child.active_processes() >= 2
        assert child.wait(0.01) is None
        child.terminate_all()
        assert child.wait(5) == 1
        deadline = time.monotonic() + 5
        while child.active_processes() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child.active_processes() == 0
    finally:
        child.close()
    child.close()
