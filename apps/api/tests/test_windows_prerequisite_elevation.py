"""Граница UAC WebView2: lock, SHA, подпись, фиксированная команда и освобождение."""

from __future__ import annotations

import ctypes
import hashlib
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from app.distribution import windows_prerequisite_elevation as elevation


class FakeApi:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.sha256 = hashlib.sha256(b"synthetic fixture").hexdigest()
        self.failure: dict[str, Exception] = {}
        self.code = 3010
        self.parents: tuple[int, ...] = ()

    def event(self, name: str, *values: object) -> None:
        self.events.append((name, *values))
        if name in self.failure:
            raise self.failure[name]

    def open_locked(self, path: Path) -> int:
        self.event("open", path)
        return 10

    def lock_parents(self, path: Path) -> tuple[int, ...]:
        self.event("parents", path.parent)
        return self.parents

    def hash_locked(self, handle: int) -> str:
        self.event("hash", handle)
        return self.sha256

    def verify_microsoft(self, handle: int, path: Path) -> None:
        self.event("trust", handle, path)

    def execute(self, path: Path) -> int:
        self.event("execute", path)
        return 20

    def wait_exit(self, handle: int, timeout_seconds: int) -> int:
        self.event("wait", handle, timeout_seconds)
        return self.code

    def close(self, handle: int) -> None:
        self.event("close", handle)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[FakeApi, Path]:
    api = FakeApi()
    path = tmp_path / elevation._INSTALLER
    path.write_bytes(b"synthetic fixture")
    monkeypatch.setattr(elevation.sys, "platform", "win32")
    monkeypatch.setattr(elevation, "_native_api", lambda: api)

    @contextmanager
    def dll_search(*, external: bool) -> Any:
        assert external
        api.event("dll-enter")
        try:
            yield
        finally:
            api.event("dll-exit")

    monkeypatch.setattr(elevation, "child_dll_search", dll_search)
    return api, path


def run(fake: tuple[FakeApi, Path], **kwargs: Any) -> int:
    api, path = fake
    return elevation.run_elevated_webview_installer(path, expected_sha256=api.sha256, **kwargs)


def test_lock_hash_trust_launch_wait_close_exact_order(fake: tuple[FakeApi, Path]) -> None:
    api, path = fake
    assert run(fake, timeout_seconds=17) == 3010
    assert api.events == [("parents", path.parent), ("open", path), ("hash", 10), ("trust", 10, path), ("dll-enter",),
                          ("execute", path), ("dll-exit",), ("wait", 20, 17), ("close", 20), ("close", 10)]


def test_wrong_hash_cannot_request_uac(fake: tuple[FakeApi, Path]) -> None:
    api, path = fake
    with pytest.raises(RuntimeError, match="SHA256"):
        elevation.run_elevated_webview_installer(path, expected_sha256="f" * 64)
    assert api.events == [("parents", path.parent), ("open", path), ("hash", 10), ("close", 10)]


def test_parents_remain_pinned_until_wait_finishes_and_close_in_reverse(fake: tuple[FakeApi, Path]) -> None:
    native, _ = fake
    native.parents = (30, 31)
    assert run(fake) == 3010
    assert native.events[-5:] == [("wait", 20, 600), ("close", 20), ("close", 10), ("close", 31), ("close", 30)]


def test_unpinnable_parent_prevents_all_file_checks_and_uac(fake: tuple[FakeApi, Path]) -> None:
    native, path = fake
    native.failure["parents"] = RuntimeError("parent is reparse or mutated")
    with pytest.raises(RuntimeError, match="parent is reparse"):
        run(fake)
    assert native.events == [("parents", path.parent)]


def test_untrusted_publisher_cannot_request_uac(fake: tuple[FakeApi, Path]) -> None:
    api, _ = fake
    api.failure["trust"] = RuntimeError("another publisher")
    with pytest.raises(RuntimeError, match="another publisher"):
        run(fake)
    assert not any(row[0] == "execute" for row in api.events)
    assert api.events[-1] == ("close", 10)


def test_dll_restore_failure_closes_both_handles_without_wait(fake: tuple[FakeApi, Path]) -> None:
    api, _ = fake
    api.failure["dll-exit"] = OSError("restore failed")
    with pytest.raises(OSError, match="restore failed"):
        run(fake)
    assert api.events[-2:] == [("close", 20), ("close", 10)]
    assert not any(row[0] == "wait" for row in api.events)


def test_timeout_does_not_kill_external_elevated_process(fake: tuple[FakeApi, Path]) -> None:
    api, _ = fake
    api.failure["wait"] = RuntimeError("timeout, external installer continues")
    with pytest.raises(RuntimeError, match="continues"):
        run(fake)
    assert api.events[-2:] == [("close", 20), ("close", 10)]


def test_cleanup_attempt_once_and_original_error_preserved(fake: tuple[FakeApi, Path]) -> None:
    api, _ = fake
    api.failure["wait"] = RuntimeError("primary wait failure")
    api.failure["close"] = OSError("already consumed close")
    with pytest.raises(RuntimeError, match="primary wait failure") as caught:
        run(fake)
    assert api.events.count(("close", 20)) == api.events.count(("close", 10)) == 1
    assert len(caught.value.__notes__) == 2


def test_cleanup_error_not_hidden_after_success(fake: tuple[FakeApi, Path]) -> None:
    api, _ = fake
    api.failure["close"] = OSError("close failed")
    with pytest.raises(OSError, match="close failed"):
        run(fake)
    assert api.events[-2:] == [("close", 20), ("close", 10)]


@pytest.mark.parametrize("timeout", [0, -1, True, 0xFFFFFFFF])
def test_invalid_timeout_never_loads_native_api(fake: tuple[FakeApi, Path], timeout: int) -> None:
    api, _ = fake
    with pytest.raises(ValueError, match="timeout"):
        run(fake, timeout_seconds=timeout)
    assert api.events == []


def test_no_arbitrary_filename_or_relative_command(fake: tuple[FakeApi, Path]) -> None:
    api, path = fake
    for invalid in (path.with_name("arbitrary.exe"), Path(elevation._INSTALLER)):
        with pytest.raises(ValueError, match="путь"):
            elevation.run_elevated_webview_installer(invalid, expected_sha256=api.sha256)
    assert api.events == []


def test_invalid_sha_never_opens_file(fake: tuple[FakeApi, Path]) -> None:
    api, path = fake
    with pytest.raises(ValueError, match="SHA256"):
        elevation.run_elevated_webview_installer(path, expected_sha256="not-a-sha")
    assert api.events == []


def test_explicit_non_windows_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(elevation.sys, "platform", "darwin")
    with pytest.raises(OSError, match="только на Windows"):
        elevation.run_elevated_webview_installer(Path("/not/executed.exe"), expected_sha256="a" * 64)


def api() -> Any:
    return object.__new__(elevation._WindowsApi)


def test_win64_abi_uses_fixed_32bit_integer_fields() -> None:
    assert ctypes.sizeof(elevation._DWORD) == ctypes.sizeof(elevation._LONG) == ctypes.sizeof(elevation._BOOL) == 4
    assert ctypes.sizeof(elevation._Guid) == 16
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(elevation._WinTrustFileInfo) == 32
        assert ctypes.sizeof(elevation._WinTrustData) == 88
        assert ctypes.sizeof(elevation._ShellExecuteInfo) == 112
        assert elevation._ProviderCertificatePrefix.pCert.offset == 8


@pytest.mark.parametrize("attributes,file_type", [(0, 1), (0x400, 1), (0x10, 1), (0, 3)])
def test_native_open_holds_readonly_no_share_write_or_delete(tmp_path: Path, attributes: int, file_type: int) -> None:
    native, calls = api(), []

    def create(*values: object) -> int:
        calls.append(values)
        return 77

    def info(handle: int, kind: int, output: Any, size: int) -> int:
        assert (handle, kind, size) == (77, 9, 8)
        ctypes.cast(output, ctypes.POINTER(elevation._FileAttributeTagInfo)).contents.attributes = attributes
        return 1

    native._create_file, native._information = create, info
    native._file_type, native._close = lambda handle: file_type, lambda handle: calls.append(("closed", handle)) or 1
    path = tmp_path / elevation._INSTALLER
    if attributes or file_type != 1:
        with pytest.raises(RuntimeError, match="обычным файлом"):
            native.open_locked(path)
        assert calls[-1] == ("closed", 77)
    else:
        assert native.open_locked(path) == 77
    assert calls[0] == (str(path), 0x80000000, 1, None, 3, 0x00200000 | 0x08000000, None)


def test_native_hash_reads_and_rewinds_same_held_handle() -> None:
    native, data, cursor, seeks = api(), b"actual held handle bytes" * 1000, 0, []

    def seek(handle: int, offset: int, output: None, method: int) -> int:
        nonlocal cursor
        assert (handle, offset, output, method) == (77, 0, None, 0)
        seeks.append(handle)
        cursor = 0
        return 1

    def read(handle: int, buffer: Any, size: int, output: Any, overlapped: None) -> int:
        nonlocal cursor
        assert handle == 77 and overlapped is None
        chunk = data[cursor:cursor + size]
        ctypes.memmove(buffer, chunk, len(chunk))
        ctypes.cast(output, ctypes.POINTER(elevation._DWORD)).contents.value = len(chunk)
        cursor += len(chunk)
        return 1

    native._seek, native._read = seek, read
    assert native.hash_locked(77) == hashlib.sha256(data).hexdigest()
    assert seeks == [77, 77]


def test_native_parent_chain_locks_all_components_without_share_delete(tmp_path: Path) -> None:
    native, calls = api(), []
    next_handle = 100

    def create(*values: object) -> int:
        nonlocal next_handle
        calls.append(values)
        next_handle += 1
        return next_handle

    def info(handle: int, kind: int, output: Any, size: int) -> int:
        ctypes.cast(output, ctypes.POINTER(elevation._FileAttributeTagInfo)).contents.attributes = 0x10
        return 1

    native._create_file, native._information, native._file_type = create, info, lambda handle: 1
    path = tmp_path / "guarded" / elevation._INSTALLER
    handles = native.lock_parents(path)
    assert len(handles) == len(path.parents)
    assert [row[0] for row in calls] == [str(p) for p in reversed(path.parents)]
    assert all(row[1:] == (0x80, 3, None, 3, 0x02000000 | 0x00200000, None) for row in calls)


def test_native_failed_parent_pin_closes_already_open_components(tmp_path: Path) -> None:
    native, closed, opened = api(), [], []

    def open_path(path: Path, *, directory: bool) -> int:
        assert directory
        opened.append(path)
        if len(opened) == 3:
            raise RuntimeError("parent mutated into reparse")
        return len(opened) + 50

    native._open_path, native._close = open_path, lambda handle: closed.append(handle) or 1
    with pytest.raises(RuntimeError, match="parent mutated"):
        native.lock_parents(tmp_path / "one" / "two" / elevation._INSTALLER)
    assert closed == [52, 51]


def trust_fixture(*, result: int = 0, publisher: str = "Microsoft Corporation") -> tuple[Any, list[object]]:
    native, calls = api(), []
    certificate = elevation._ProviderCertificatePrefix(64, 999)

    def trust(window: None, action: Any, pointer: Any) -> int:
        data = ctypes.cast(pointer, ctypes.POINTER(elevation._WinTrustData)).contents
        calls.append(("state", data.dwStateAction))
        assert data.pFile.contents.hFile == 77 and data.pFile.contents.pcwszFilePath == str(Path("/synthetic/installer.exe"))
        assert data.dwUIChoice == 2 and data.dwUnionChoice == 1
        assert data.dwProvFlags == 0x1000 | 0x2000
        data.hWVTStateData = 123
        return result if data.dwStateAction == 1 else 0

    native._trust, native._provider, native._signer = trust, lambda state: 456, lambda *args: 789
    native._certificate = lambda *args: ctypes.pointer(certificate)

    def name(cert: int, kind: int, oid: bytes | None = None) -> str:
        calls.append(("name", cert, kind, oid))
        return publisher

    native._certificate_name = name
    return native, calls


def test_native_authenticode_uses_held_file_and_leaf_subject_organization() -> None:
    native, calls = trust_fixture()
    native.verify_microsoft(77, Path("/synthetic/installer.exe"))
    assert calls == [("state", 1), ("name", 999, 4, None), ("name", 999, 3, b"2.5.4.10"), ("state", 2)]


@pytest.mark.parametrize("result,publisher", [(-2146762496, "Microsoft Corporation"), (0, "Other Publisher")])
def test_native_trust_rejection_always_closes_state(result: int, publisher: str) -> None:
    native, calls = trust_fixture(result=result, publisher=publisher)
    with pytest.raises(RuntimeError, match="WinVerifyTrust|другим издателем"):
        native.verify_microsoft(77, Path("/synthetic/installer.exe"))
    assert calls[0] == ("state", 1) and calls[-1] == ("state", 2)


def test_native_subject_name_reads_requested_organization_oid() -> None:
    native, calls = api(), []
    text = "Microsoft Corporation"

    def name(cert: int, kind: int, flags: int, oid: Any, output: Any, length: int) -> int:
        calls.append((cert, kind, flags, ctypes.cast(oid, ctypes.c_char_p).value))
        if output is not None:
            output.value = text
        return len(text) + 1

    native._name = name
    assert native._certificate_name(999, 3, b"2.5.4.10") == text
    assert calls == [(999, 3, 0, b"2.5.4.10")] * 2


@pytest.mark.parametrize("cancel", [False, True])
def test_native_shell_only_runas_exact_exe_static_args_and_balanced_com(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cancel: bool) -> None:
    native, calls = api(), []
    path = tmp_path / elevation._INSTALLER
    native._co_initialize, native._co_uninitialize = lambda *args: 0, lambda: calls.append("com-close")

    def execute(pointer: Any) -> int:
        info = ctypes.cast(pointer, ctypes.POINTER(elevation._ShellExecuteInfo)).contents
        calls.append((info.fMask, info.lpVerb, info.lpFile, info.lpParameters, info.lpDirectory, info.nShow))
        if cancel:
            return 0
        info.hProcess = 88
        return 1

    native._execute = execute
    error = OSError("synthetic cancelled")
    error.winerror = 1223
    monkeypatch.setattr(elevation, "_last_error", lambda: error)
    if cancel:
        with pytest.raises(RuntimeError, match="отменено"):
            native.execute(path)
    else:
        assert native.execute(path) == 88
    assert calls == [(0x40 | 0x100, "runas", str(path), "/silent /install", str(path.parent), 1), "com-close"]


@pytest.mark.parametrize("result", [0, 0x102, 0xFFFFFFFF, 0x80])
def test_native_wait_bounded_and_unsigned_exit_code(monkeypatch: pytest.MonkeyPatch, result: int) -> None:
    native, calls = api(), []
    native._wait = lambda handle, timeout: calls.append((handle, timeout)) or result
    monkeypatch.setattr(elevation, "_last_error", lambda: OSError("wait failed"))

    def code(handle: int, output: Any) -> int:
        ctypes.cast(output, ctypes.POINTER(elevation._DWORD)).contents.value = 0x80040828
        return 1

    native._exit_code = code
    if result == 0:
        assert native.wait_exit(88, 17) == 0x80040828
    else:
        with pytest.raises((RuntimeError, OSError)):
            native.wait_exit(88, 17)
    assert calls == [(88, 17000)]


@pytest.mark.skipif(sys.platform != "win32", reason="Реальные lock/WinTrust доступны только на Windows")
def test_real_windows_lock_and_unsigned_rejection_without_execution(tmp_path: Path) -> None:
    native = elevation._WindowsApi()
    parent = tmp_path / "guarded"
    parent.mkdir()
    path = parent / elevation._INSTALLER
    data = b"not executable; never launched"
    path.write_bytes(data)
    parents = native.lock_parents(path)
    handle = native.open_locked(path)
    try:
        assert native.hash_locked(handle) == hashlib.sha256(data).hexdigest()
        with pytest.raises(OSError):
            path.write_bytes(b"replacement blocked")
        with pytest.raises(OSError):
            path.rename(path.with_name("moved.exe"))
        with pytest.raises(OSError):
            parent.rename(tmp_path / "replacement")
        with pytest.raises(RuntimeError, match="WinVerifyTrust"):
            native.verify_microsoft(handle, path)
    finally:
        native.close(handle)
        for guarded in reversed(parents):
            native.close(guarded)
    assert path.read_bytes() == data
