"""Windows-политика файлов с подставной WinAPI и отдельными native smoke-проверками."""

from __future__ import annotations

import ctypes
import io
import json
import os
import stat
import sys
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.native_query.transport import windows_file_api as winapi
from app.native_query.transport import windows_job_files as files
from app.native_query.transport.process_contracts import (
    CadPackageFile,
    NativeInputPackage,
    NativeQueryProcessConfig,
)


class _FakeRaw(io.FileIO):
    def __init__(self, path: Path, writable: bool, owner: _FakeApi, handle: int):
        self.owner, self.handle = owner, handle
        super().__init__(path, "r+" if writable else "r")

    def close(self) -> None:
        already_closed = self.closed
        try:
            super().close()
        finally:
            if not already_closed:
                self.owner.close(self.handle)


class _FakeApi:
    """Файловые ID синтетические: POSIX inode не выдаётся за Win32 identity."""

    def __init__(self) -> None:
        self.handles: dict[int, Path] = {}
        self.identifiers: dict[Path, bytes] = {}
        self.overrides: dict[Path, dict[str, Any]] = {}
        self.opened: list[tuple[Path, dict[str, bool]]] = []
        self.closed: list[int] = []
        self.next_handle = 100
        self.fail_close: set[Path] = set()
        self.fail_transfer = False

    def open(
        self,
        path: Path,
        *,
        directory: bool = False,
        writable: bool = False,
        create: bool = False,
        metadata: bool = False,
    ) -> int:
        path = Path(path)
        if create:
            with path.open("xb"):
                pass
        path.lstat()
        handle = self.next_handle
        self.next_handle += 1
        self.handles[handle] = path
        self.opened.append(
            (
                path,
                {
                    "directory": directory,
                    "writable": writable,
                    "create": create,
                    "metadata": metadata,
                },
            )
        )
        self.identifiers.setdefault(path, len(self.identifiers).to_bytes(16, "little"))
        return handle

    def information(self, handle: int) -> winapi.FileInformation:
        path = self.handles[handle]
        info = path.lstat()
        attributes = 0
        if stat.S_ISDIR(info.st_mode):
            attributes |= winapi.FILE_ATTRIBUTE_DIRECTORY
        if stat.S_ISLNK(info.st_mode):
            attributes |= winapi.FILE_ATTRIBUTE_REPARSE_POINT
        value = winapi.FileInformation(
            123,
            self.identifiers[path],
            info.st_nlink,
            info.st_size,
            attributes,
            info.st_mtime_ns,
            stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode),
        )
        return replace(value, **self.overrides.get(path, {}))

    def close(self, handle: int) -> None:
        path = self.handles.pop(handle)
        self.closed.append(handle)
        if path in self.fail_close:
            self.fail_close.remove(path)
            raise OSError("injected CloseHandle failure")

    def to_stream(self, handle: int, *, writable: bool) -> io.FileIO:
        if self.fail_transfer:
            self.close(handle)
            raise OSError("injected stream transfer failure")
        try:
            return _FakeRaw(self.handles[handle], writable, self, handle)
        except BaseException:
            self.close(handle)
            raise


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> _FakeApi:
    value = _FakeApi()
    monkeypatch.setattr(files, "_file_api", lambda: value)
    yield value
    assert not value.handles


def _write(path: Path, data: bytes = b"drawing") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _package(root: Path, *names: str) -> NativeInputPackage:
    return NativeInputPackage(
        root,
        names[0],
        tuple(
            CadPackageFile(name, sha256(b"drawing").hexdigest(), 7) for name in names
        ),
    )


def _worker(root: Path, **changes: object) -> NativeQueryProcessConfig:
    _write(root / "GreenAtlasQuery.crx", b"owned-worker")
    info: dict[str, object] = {
        "schema": files.WORKER_SCHEMA,
        "target": "windows-x86_64",
        "binary": "GreenAtlasQuery.crx",
        "plugin_version": "49",
        "sha256": sha256(b"owned-worker").hexdigest(),
        "face_protocols": ["face/3"],
        "topology_shapes": ["curve"],
    }
    info.update(changes)
    _write(root / "worker.json", json.dumps(info).encode())
    return NativeQueryProcessConfig(
        core_executable=root / "accoreconsole.exe",
        worker_bundle=root,
        job_root=root.parent / "jobs",
        worker_binary_sha256=sha256(b"owned-worker").hexdigest(),
        plugin_version="49",
        bootstrap_template=root / "worker.scr",
        face_protocols=("face/3",),
        topology_shapes=("curve",),
    )


@pytest.mark.parametrize(
    "relative",
    [
        "../source.dwg",
        "/source.dwg",
        "a//source.dwg",
        "a/./source.dwg",
        "a/../source.dwg",
        "a\\source.dwg",
        "source.dwg:stream",
        "CON.dwg",
        "CON .dwg",
        "aux.DWG",
        "COM1.dxf",
        "lpt9.dwg",
        "COM¹.dwg",
        "LPT²/x.dwg",
        "CONIN$.dwg",
        "trailing./source.dwg",
        "space /source.dwg",
        "bad?.dwg",
        "bad*.dwg",
        'bad".dwg',
        "bad<.dwg",
        "bad|.dwg",
        "bad\x00.dwg",
        "",
    ],
)
def test_rejects_noncanonical_windows_paths(
    api: _FakeApi, tmp_path: Path, relative: str
):
    with pytest.raises(ValueError):
        files.package_file(tmp_path, relative)
    assert not api.opened


def test_source_hash_and_binary_stream_compatibility(api: _FakeApi, tmp_path: Path):
    source = _write(tmp_path / "снимок" / "source.dwg")
    assert files.file_identity(source) == (7, sha256(b"drawing").hexdigest())
    with files.open_regular(source, writable=True) as stream:
        assert stream.fileno() >= 0
        stream.seek(0)
        stream.write(b"D")
        stream.flush()
        stream.seek(0)
        assert stream.read() == b"Drawing"
        stream.truncate(4)
        assert any(path == tmp_path for path in api.handles.values())
    assert source.read_bytes() == b"Draw"


@pytest.mark.parametrize("where", ["leaf", "parent", "ancestor"])
def test_rejects_reparse_at_every_component(api: _FakeApi, tmp_path: Path, where: str):
    source = _write(tmp_path / "package" / "xref" / "source.dwg")
    bad = {"leaf": source, "parent": source.parent, "ancestor": tmp_path}[where]
    base = api.overrides.setdefault(bad, {})
    base["attributes"] = winapi.FILE_ATTRIBUTE_REPARSE_POINT | (
        0 if bad == source else winapi.FILE_ATTRIBUTE_DIRECTORY
    )
    with pytest.raises(ValueError, match="reparse"):
        files.file_identity(source)
    with pytest.raises(ValueError, match="reparse"):
        files.package_file(source.parent, source.name)


def test_real_symlink_is_not_followed_by_adapter(api: _FakeApi, tmp_path: Path):
    source = _write(tmp_path / "source.dwg")
    alias = tmp_path / "alias.dwg"
    try:
        alias.symlink_to(source)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows account cannot create file symlinks")
        raise
    with pytest.raises(ValueError):
        files.open_regular(alias)


def test_directory_and_non_disk_file_are_rejected(api: _FakeApi, tmp_path: Path):
    with pytest.raises(ValueError):
        files.open_regular(tmp_path)
    source = _write(tmp_path / "source.dwg")
    api.overrides[source] = {"disk": False}
    with pytest.raises(ValueError):
        files.file_identity(source)


def test_readonly_hardlinks_are_allowed_but_writable_rejected(
    api: _FakeApi, tmp_path: Path
):
    source = _write(tmp_path / "source.dwg")
    api.overrides[source] = {"links": 2}
    assert files.file_identity(source)[0] == 7
    with pytest.raises(ValueError, match="independent"):
        files.open_regular(source, writable=True)
    target = tmp_path / "job" / "copy.dwg"
    files.copy_independent(source, target, lambda: None, 7)
    assert target.read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    "field,value",
    [
        ("file_id", b"different-file!!"),
        ("volume_serial", 456),
        ("links", 2),
        ("size", 8),
        ("last_write", 999),
        ("attributes", winapi.FILE_ATTRIBUTE_REPARSE_POINT),
    ],
)
def test_hash_rejects_changed_native_identity(
    api: _FakeApi, tmp_path: Path, field: str, value: object
):
    source = _write(tmp_path / "source.dwg")

    def changed() -> None:
        api.overrides[source] = {field: value}

    with pytest.raises(ValueError, match="changed"):
        files.file_identity(source, changed)


def test_hash_limits_and_cancellation_close_all_guards(api: _FakeApi, tmp_path: Path):
    source = _write(tmp_path / "source.dwg")
    with pytest.raises(ValueError, match="budget"):
        files.file_identity(source, limit=6)

    def cancelled() -> None:
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        files.file_identity(source, cancelled)


def test_verify_package_rejects_directory_case_aliases_before_io(
    api: _FakeApi, tmp_path: Path
):
    package = _package(tmp_path, "XREF/a.dwg", "xref/b.dwg")
    with pytest.raises(ValueError, match="Case-colliding"):
        files.verify_package(package, tmp_path)
    assert not api.opened


def test_verify_package_and_source_sha(api: _FakeApi, tmp_path: Path):
    source = _write(tmp_path / "source.dwg")
    package = _package(tmp_path, "source.dwg")
    files.verify_package(package, tmp_path)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Package file changed"):
        files.verify_package(package, tmp_path)


def test_package_file_uses_metadata_without_blocking_writer(
    api: _FakeApi, tmp_path: Path
):
    source = _write(tmp_path / "native.json.tmp")
    assert files.package_file(tmp_path, source.name) == source
    assert [options for path, options in api.opened if path == source] == [
        {"directory": False, "writable": False, "create": False, "metadata": True}
    ]


def test_independent_copy_never_overwrites_source_or_destination(
    api: _FakeApi, tmp_path: Path
):
    source = _write(tmp_path / "source.dwg")
    target = _write(tmp_path / "existing.dwg", b"existing")
    for destination in (source, target):
        with pytest.raises(FileExistsError):
            files.copy_independent(source, destination, lambda: None, 7)
    assert source.read_bytes() == b"drawing"
    assert target.read_bytes() == b"existing"


@pytest.mark.parametrize("field", ["links", "file_id"])
def test_copy_rejects_nonindependent_destination(
    api: _FakeApi, tmp_path: Path, field: str
):
    source = _write(tmp_path / "source.dwg")
    target = tmp_path / "copy.dwg"
    api.identifiers[source] = b"s" * 16
    if field == "file_id":
        api.identifiers[target] = b"s" * 16
    else:
        api.overrides[target] = {"links": 2}
    with pytest.raises(ValueError, match="independent"):
        files.copy_independent(source, target, lambda: None, 7)


def test_copy_rejects_source_change_and_budget(api: _FakeApi, tmp_path: Path):
    source = _write(tmp_path / "source.dwg")
    with pytest.raises(ValueError, match="budget"):
        files.copy_independent(source, tmp_path / "small.dwg", lambda: None, 6)

    def changed() -> None:
        api.overrides[source] = {"links": 2}

    with pytest.raises(ValueError, match="changed"):
        files.copy_independent(source, tmp_path / "changed.dwg", changed, 7)


def test_directory_guards_close_despite_child_close_failure(
    api: _FakeApi, tmp_path: Path
):
    source = _write(tmp_path / "source.dwg")
    api.fail_close.add(source)
    api.fail_close.add(source.parent)
    with (
        pytest.raises(OSError, match="CloseHandle"),
        files.open_regular(source) as stream,
    ):
        assert stream.read() == b"drawing"
    assert not api.handles


def test_transfer_failure_does_not_double_close(api: _FakeApi, tmp_path: Path):
    source = _write(tmp_path / "source.dwg")
    api.fail_transfer = True
    with pytest.raises(OSError, match="transfer"):
        files.open_regular(source)
    assert len(api.closed) == len(set(api.closed))


@pytest.mark.parametrize(
    "changes",
    [
        {"target": "macos-arm64"},
        {"binary": "GreenAtlasBridge.dll"},
        {"schema": "green-atlas.gui-plugin/1"},
        {"plugin_version": "48"},
        {"sha256": "0" * 64},
        {"face_protocols": ["face/2"]},
        {"topology_shapes": ["region"]},
        {"face_protocols": ["face/3", "face/3"]},
        {"unexpected": True},
    ],
)
def test_verify_worker_identity_and_capabilities(
    api: _FakeApi, tmp_path: Path, changes: dict
):
    config = _worker(tmp_path / "worker", **changes)
    with pytest.raises(ValueError):
        files.verify_worker(config.worker_bundle, config)


def test_worker_metadata_duplicates_limit_and_binary_hash(
    api: _FakeApi, tmp_path: Path
):
    config = _worker(tmp_path / "worker")
    metadata = config.worker_bundle / "worker.json"
    metadata.write_bytes(b'{"schema":"a","schema":"b"}')
    with pytest.raises(ValueError, match="duplicate"):
        files.verify_worker(config.worker_bundle, config)
    metadata.write_bytes(b" " * 16385)
    with pytest.raises(ValueError, match="limit"):
        files.verify_worker(config.worker_bundle, config)
    config = _worker(config.worker_bundle)
    (config.worker_bundle / files.WORKER_BINARY).write_bytes(b"bad-worker")
    with pytest.raises(ValueError, match="SHA mismatch"):
        files.verify_worker(config.worker_bundle, config)


def test_stage_worker_preserves_owned_payload_and_metadata(
    api: _FakeApi, tmp_path: Path
):
    config = _worker(tmp_path / "worker")
    _write(config.worker_bundle / "support" / "owned.dll", b"support")
    files.verify_worker(config.worker_bundle, config)
    target = tmp_path / "job" / "QueryWorker"
    files.stage_worker(config.worker_bundle, target, lambda: None)
    files.verify_worker(target, config)
    assert (target / "support" / "owned.dll").read_bytes() == b"support"
    assert (target / files.WORKER_BINARY).read_bytes() == b"owned-worker"


def test_stage_worker_rejects_junction_directory_before_traversal(
    api: _FakeApi, tmp_path: Path
):
    root = tmp_path / "worker"
    directory = root / "support"
    _write(directory / "owned.dll")
    api.overrides[directory] = {
        "attributes": winapi.FILE_ATTRIBUTE_DIRECTORY
        | winapi.FILE_ATTRIBUTE_REPARSE_POINT
    }
    with pytest.raises(ValueError, match="reparse"):
        files.stage_worker(root, tmp_path / "job", lambda: None)
    assert not any(path.name == "owned.dll" for path, _ in api.opened)


def test_stage_worker_file_and_count_budgets(api: _FakeApi, tmp_path: Path):
    root = tmp_path / "worker"
    source = _write(root / "owned.dll")
    api.overrides[source] = {"size": 256 * 1024**2 + 1}
    with pytest.raises(ValueError, match="budget"):
        files.stage_worker(root, tmp_path / "job", lambda: None)
    api.overrides.clear()
    source.unlink()
    for index in range(1025):
        (root / f"{index}.empty").mkdir()
    with pytest.raises(ValueError, match="too many"):
        files.stage_worker(root, tmp_path / "job", lambda: None)


def test_case_collision_in_real_directory_is_rejected(
    api: _FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _write(tmp_path / "source.dwg")
    original = files.os.scandir

    class Entries:
        def __enter__(self):
            return iter(
                [SimpleNamespace(name="source.dwg"), SimpleNamespace(name="SOURCE.DWG")]
            )

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        files.os,
        "scandir",
        lambda path: Entries() if path == tmp_path else original(path),
    )
    with pytest.raises(ValueError, match="Case-colliding"):
        files.package_file(tmp_path, source.name)


def test_validate_directory_does_not_resolve_reparse_parents(
    api: _FakeApi, tmp_path: Path
):
    root = tmp_path / "package"
    root.mkdir()
    api.overrides[tmp_path] = {
        "attributes": winapi.FILE_ATTRIBUTE_DIRECTORY
        | winapi.FILE_ATTRIBUTE_REPARSE_POINT
    }
    with pytest.raises(ValueError, match="reparse"):
        files.validate_directory(root)


def test_prepare_directory_creates_missing_nested_parents(
    api: _FakeApi, tmp_path: Path
):
    root = tmp_path / "new-profile" / "native" / "jobs"
    files.prepare_directory(root)
    assert root.is_dir()
    assert not api.handles
    for directory in (root.parent.parent, root.parent, root):
        assert any(path == directory for path, _options in api.opened)


def test_prepare_directory_rejects_reparse_before_creating_children(
    api: _FakeApi, tmp_path: Path
):
    ancestor = tmp_path / "profile"
    ancestor.mkdir()
    api.overrides[ancestor] = {
        "attributes": winapi.FILE_ATTRIBUTE_DIRECTORY
        | winapi.FILE_ATTRIBUTE_REPARSE_POINT
    }
    with pytest.raises(ValueError, match="reparse"):
        files.prepare_directory(ancestor / "new-native" / "jobs")
    assert not (ancestor / "new-native").exists()
    assert not api.handles


def test_native_struct_sizes_and_createfile_policy():
    assert ctypes.sizeof(winapi._ByHandleInformation) == 52
    assert ctypes.sizeof(winapi._FileIdInformation) == 24
    calls = []
    backend = object.__new__(winapi.WindowsFileApi)
    backend._kernel = SimpleNamespace(
        CreateFileW=lambda *args: calls.append(args) or 42
    )
    for options, desired, share, disposition in [
        ({}, winapi.GENERIC_READ, winapi.FILE_SHARE_READ, winapi.OPEN_EXISTING),
        ({"metadata": True}, 0, 7, winapi.OPEN_EXISTING),
        ({"directory": True}, 0, 3, winapi.OPEN_EXISTING),
        (
            {"writable": True},
            winapi.GENERIC_READ | winapi.GENERIC_WRITE,
            0,
            winapi.OPEN_EXISTING,
        ),
        (
            {"writable": True, "create": True},
            winapi.GENERIC_READ | winapi.GENERIC_WRITE,
            0,
            winapi.CREATE_NEW,
        ),
    ]:
        assert backend.open(Path("/synthetic/file"), **options) == 42
        assert calls[-1][1:3] == (desired, share)
        assert calls[-1][4] == disposition
        assert (
            calls[-1][5]
            == winapi.FILE_FLAG_OPEN_REPARSE_POINT | winapi.FILE_FLAG_BACKUP_SEMANTICS
        )
        assert calls[-1][3] is None


def test_native_information_combines_full_id_volume_links_and_size():
    backend = object.__new__(winapi.WindowsFileApi)

    def basic(handle: int, pointer: Any) -> int:
        assert handle == 42
        info = ctypes.cast(
            pointer, ctypes.POINTER(winapi._ByHandleInformation)
        ).contents
        info.attributes, info.links = 0x20, 2
        info.size_high, info.size_low = 3, 5
        info.written.high, info.written.low = 7, 11
        # Усечённый legacy ID отличается от полного ID и не используется вместо него.
        info.volume, info.index_high, info.index_low = 17, 19, 23
        return 1

    def identity(handle: int, kind: int, pointer: Any, size: int) -> int:
        assert handle == 42
        if kind == winapi.FILE_BASIC_INFO_CLASS:
            assert size == ctypes.sizeof(winapi._FileBasicInformation)
            timestamps = ctypes.cast(
                pointer, ctypes.POINTER(winapi._FileBasicInformation)
            ).contents
            timestamps.changed = 12345
            return 1
        assert (kind, size) == (winapi.FILE_ID_INFO_CLASS, 24)
        info = ctypes.cast(pointer, ctypes.POINTER(winapi._FileIdInformation)).contents
        info.volume = 0x100000013
        info.identifier[:] = bytes(range(16))
        return 1

    backend._kernel = SimpleNamespace(
        GetFileInformationByHandle=basic,
        GetFileInformationByHandleEx=identity,
        GetFileType=lambda handle: winapi.FILE_TYPE_DISK,
    )
    assert backend.information(42) == winapi.FileInformation(
        0x100000013,
        bytes(range(16)),
        2,
        (3 << 32) | 5,
        0x20,
        (7 << 32) | 11,
        change_time=12345,
    )


@pytest.mark.parametrize(
    "operation", ["open", "basic", "identity", "timestamps", "close"]
)
def test_native_errors_fail_closed_without_portable_fallback(
    monkeypatch: pytest.MonkeyPatch, operation: str
):
    backend = object.__new__(winapi.WindowsFileApi)
    backend._kernel = SimpleNamespace(
        CreateFileW=lambda *args: ctypes.c_void_p(-1).value,
        GetFileInformationByHandle=lambda *args: int(operation != "basic"),
        GetFileInformationByHandleEx=lambda *args: 0,
        CloseHandle=lambda handle: 0,
    )
    monkeypatch.setattr(winapi, "_windows_error", lambda: OSError("native error"))
    with pytest.raises(OSError, match="native error"):
        if operation == "open":
            backend.open(Path("/synthetic/file"))
        elif operation == "close":
            backend.close(42)
        else:
            backend.information(42)


def test_native_stream_transfer_has_single_owner_and_binary_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    source = _write(tmp_path / "source.dwg", b"a\r\nb\x1a")
    descriptor = os.open(source, os.O_RDONLY)
    native = object.__new__(winapi.WindowsFileApi)
    closed = []
    native._kernel = SimpleNamespace(
        CloseHandle=lambda handle: closed.append(handle) or 1
    )
    msvcrt = ModuleType("msvcrt")
    calls = []
    monkeypatch.setattr(
        msvcrt,
        "open_osfhandle",
        lambda *args: calls.append(args) or descriptor,
        raising=False,
    )
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(os, "O_BINARY", 0x8000, raising=False)
    monkeypatch.setattr(os, "O_NOINHERIT", 0x80, raising=False)
    with native.to_stream(42, writable=False) as stream:
        assert stream.read() == b"a\r\nb\x1a"
    assert calls == [(42, os.O_RDONLY | 0x8000 | 0x80)]
    assert closed == []
    with pytest.raises(OSError):
        os.read(descriptor, 1)


def test_native_stream_conversion_failure_closes_owned_handle(
    monkeypatch: pytest.MonkeyPatch,
):
    native = object.__new__(winapi.WindowsFileApi)
    closed = []
    native._kernel = SimpleNamespace(
        CloseHandle=lambda handle: closed.append(handle) or 1
    )
    msvcrt = ModuleType("msvcrt")

    def fail(*args):
        raise OSError("conversion failed")

    monkeypatch.setattr(msvcrt, "open_osfhandle", fail, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(os, "O_BINARY", 0x8000, raising=False)
    monkeypatch.setattr(os, "O_NOINHERIT", 0x80, raising=False)
    with pytest.raises(OSError, match="conversion"):
        native.to_stream(42, writable=False)
    assert closed == [42]


def test_native_stream_missing_runtime_closes_owned_handle(
    monkeypatch: pytest.MonkeyPatch,
):
    native = object.__new__(winapi.WindowsFileApi)
    closed = []
    native._kernel = SimpleNamespace(
        CloseHandle=lambda handle: closed.append(handle) or 1
    )
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    with pytest.raises(ModuleNotFoundError):
        native.to_stream(42, writable=False)
    assert closed == [42]


def test_native_fileio_failure_closes_transferred_descriptor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    source = _write(tmp_path / "source.dwg")
    descriptor = os.open(source, os.O_RDONLY)
    native = object.__new__(winapi.WindowsFileApi)
    closed = []
    native._kernel = SimpleNamespace(
        CloseHandle=lambda handle: closed.append(handle) or 1
    )
    msvcrt = ModuleType("msvcrt")
    monkeypatch.setattr(
        msvcrt, "open_osfhandle", lambda *args: descriptor, raising=False
    )
    monkeypatch.setitem(sys.modules, "msvcrt", msvcrt)
    monkeypatch.setattr(os, "O_BINARY", 0x8000, raising=False)
    monkeypatch.setattr(os, "O_NOINHERIT", 0x80, raising=False)

    def fail(*args, **kwargs):
        raise OSError("FileIO failed")

    monkeypatch.setattr(winapi.io, "FileIO", fail)
    with pytest.raises(OSError, match="FileIO"):
        native.to_stream(42, writable=False)
    assert closed == []
    with pytest.raises(OSError):
        os.read(descriptor, 1)


def test_buffer_constructor_failure_closes_stream_and_guards(
    api: _FakeApi, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _write(tmp_path / "source.dwg")

    def fail(*args, **kwargs):
        raise OSError("buffer failed")

    monkeypatch.setattr(files, "_GuardedReader", fail)
    with pytest.raises(OSError, match="buffer"):
        files.open_regular(source)
    assert not api.handles


def test_import_does_not_load_windows_dll_on_mac():
    if sys.platform != "win32":
        with pytest.raises(OSError, match="require Windows"):
            winapi.WindowsFileApi()


@pytest.mark.skipif(
    sys.platform != "win32", reason="Требуется настоящая Windows Win32/NTFS"
)
def test_real_windows_handles_copy_sha_and_independence(tmp_path: Path):
    source = _write(tmp_path / "source.dwg")
    target = tmp_path / "job" / "copy.dwg"
    files.copy_independent(source, target, lambda: None, 7)
    assert files.file_identity(source) == files.file_identity(target)
    os.link(source, tmp_path / "hardlink.dwg")
    assert files.file_identity(source)[0] == 7
    with pytest.raises(ValueError, match="independent"):
        files.open_regular(source, writable=True)
    with files.open_regular(target, writable=True) as stream:
        assert stream.fileno() >= 0
        stream.truncate(3)
    assert target.read_bytes() == b"dra"
    assert source.read_bytes() == b"drawing"


@pytest.mark.skipif(
    sys.platform != "win32", reason="Требуется настоящая Windows Win32/NTFS"
)
def test_real_windows_rejects_reparse_leaf_and_parent(tmp_path: Path):
    source = _write(tmp_path / "source" / "drawing.dwg")
    alias = tmp_path / "alias.dwg"
    try:
        alias.symlink_to(source)
        directory_alias = tmp_path / "alias-directory"
        directory_alias.symlink_to(source.parent, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Нужен Developer Mode или право создания символических ссылок")
        raise
    with pytest.raises(ValueError, match="reparse"):
        files.file_identity(alias)
    with pytest.raises(ValueError, match="reparse"):
        files.file_identity(directory_alias / source.name)


@pytest.mark.skipif(
    sys.platform != "win32", reason="Требуется настоящая Windows Win32/NTFS"
)
def test_real_windows_handle_sharing_keeps_source_and_parent_stable(tmp_path: Path):
    source = _write(tmp_path / "package" / "source.dwg")
    with files.open_regular(source) as stream:
        assert stream.read() == b"drawing"
        with pytest.raises(OSError):
            source.write_bytes(b"changed")
        with pytest.raises(OSError):
            source.rename(source.with_name("renamed.dwg"))
        with pytest.raises(OSError):
            source.parent.rename(source.parent.with_name("renamed-package"))
    source.parent.rename(source.parent.with_name("renamed-package"))


@pytest.mark.skipif(
    sys.platform != "win32", reason="Требуется настоящая Windows Win32/NTFS"
)
def test_real_windows_metadata_can_probe_file_while_writer_is_active(tmp_path: Path):
    source = _write(tmp_path / "native.json.tmp")
    with source.open("wb") as writer:
        writer.write(b"partial")
        writer.flush()
        assert files.package_file(tmp_path, source.name) == source
        with pytest.raises(OSError):
            files.file_identity(source)


@pytest.mark.skipif(
    sys.platform != "win32", reason="Требуется настоящая Windows Win32/NTFS"
)
def test_real_windows_metadata_allows_atomic_output_publication(tmp_path: Path):
    temporary = _write(tmp_path / "native.json.tmp", b"complete")
    final = tmp_path / "native.json"
    for _ in range(20):
        with files._metadata(temporary):
            temporary.replace(final)
            final.replace(temporary)
    assert temporary.read_bytes() == b"complete"
