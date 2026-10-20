"""Файлы Windows Core-задания: пути, удержанные HANDLE, SHA и независимые копии.

Этот адаптер не изменяет Mac POSIX-политику. Чтение источника допускает hardlinks;
запись и постановка задания требуют отдельного файла с единственной ссылкой.
"""

from __future__ import annotations

import io
import json
import os
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from app.native_query.transport.process_contracts import (
    NativeInputPackage,
    NativeQueryProcessConfig,
    relative_path,
    valid_sha,
)
from app.native_query.transport.windows_file_api import (
    FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_REPARSE_POINT,
    FileApi,
    FileInformation,
    native_file_api,
)

WORKER_BINARY = "GreenAtlasQuery.crx"
WORKER_METADATA = "worker.json"
WORKER_SCHEMA = "green-atlas.query-worker/1"
_file_api = native_file_api
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"
}


def _component(value: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or value[-1] in " ."
        or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in value)
        or value.split(".", 1)[0].rstrip(" ").upper() in _RESERVED
    ):
        raise ValueError("Expected an unambiguous Windows file name")


def _relative(value: str) -> tuple[str, ...]:
    relative_path(value)
    parts = PurePosixPath(value).parts
    for part in parts:
        _component(part)
    return parts


def _absolute(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or str(path).startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("Expected an absolute path outside the device namespace")
    if path.drive.startswith("\\\\"):
        for part in path.drive[2:].split("\\"):
            _component(part)
    for part in path.parts[1:]:
        _component(part)
    return path


def _name_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _case_collision(parent: Path, name: str) -> None:
    wanted = _name_key(name)
    with os.scandir(parent) as entries:
        count = sum(_name_key(entry.name) == wanted for entry in entries)
    if count > 1:
        raise ValueError("Case-colliding Windows file names")


def _validate_information(info: FileInformation, *, directory: bool) -> None:
    if (
        not info.disk
        or info.attributes & FILE_ATTRIBUTE_REPARSE_POINT
        or bool(info.attributes & FILE_ATTRIBUTE_DIRECTORY) != directory
        or (not directory and info.links < 1)
    ):
        raise ValueError("Expected a regular path without reparse points")


def _directory_chain(path: Path, api: FileApi, *, create: bool = False) -> ExitStack:
    path = _absolute(path)
    guards = ExitStack()
    try:
        for directory in (*reversed(path.parents), path):
            if directory.parent != directory:
                _case_collision(directory.parent, directory.name)
            try:
                handle = api.open(directory, directory=True)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    directory.mkdir()
                except FileExistsError:
                    pass
                handle = api.open(directory, directory=True)
            guards.callback(api.close, handle)
            _validate_information(api.information(handle), directory=True)
        return guards
    except BaseException:
        guards.close()
        raise


def validate_directory(path: Path) -> None:
    """Проверяет каждый компонент, не скрывая ссылки вызовом resolve()."""
    with _directory_chain(path, _file_api()):
        pass


def prepare_directory(path: Path) -> None:
    """Создаёт недостающую цепочку, удерживая проверенные родительские каталоги."""
    with _directory_chain(path, _file_api(), create=True):
        pass


class _GuardedReader(io.BufferedReader):
    def __init__(
        self, raw: io.FileIO, guards: ExitStack, api: FileApi, handle: int
    ) -> None:
        self._guards, self.api, self.handle = guards, api, handle
        super().__init__(raw)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._guards.close()


class _GuardedRandom(io.BufferedRandom):
    def __init__(
        self, raw: io.FileIO, guards: ExitStack, api: FileApi, handle: int
    ) -> None:
        self._guards, self.api, self.handle = guards, api, handle
        super().__init__(raw)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._guards.close()


_Stream = _GuardedReader | _GuardedRandom


def _open(path: Path, *, writable: bool = False, create: bool = False) -> _Stream:
    path, api = _absolute(path), _file_api()
    guards = _directory_chain(path.parent, api, create=create)
    try:
        _case_collision(path.parent, path.name)
        handle = api.open(path, writable=writable, create=create)
        try:
            info = api.information(handle)
            _validate_information(info, directory=False)
            if writable and info.links != 1:
                raise ValueError("Expected an independent regular file")
        except BaseException:
            api.close(handle)
            raise
        # to_stream принимает HANDLE даже при ошибке; двойного CloseHandle нет.
        raw = api.to_stream(handle, writable=writable)
        try:
            stream_type = _GuardedRandom if writable else _GuardedReader
            return stream_type(raw, guards, api, handle)
        except BaseException:
            raw.close()
            raise
    except BaseException:
        guards.close()
        raise


def open_regular(path: Path, *, writable: bool = False) -> BinaryIO:
    return cast(BinaryIO, _open(path, writable=writable))


@contextmanager
def _metadata(path: Path) -> Iterator[FileInformation]:
    path, api = _absolute(path), _file_api()
    with _directory_chain(path.parent, api) as guards:
        _case_collision(path.parent, path.name)
        handle = api.open(path, metadata=True)
        guards.callback(api.close, handle)
        info = api.information(handle)
        _validate_information(info, directory=False)
        yield info


def package_file(root: Path, relative: str) -> Path:
    path = _absolute(root).joinpath(*_relative(relative))
    # Краткая проверка типа/reparse не мешает writer и переименованию .tmp.
    # Последующее чтение заново проверяет весь путь и удерживает строгий HANDLE.
    with _metadata(path):
        return path


def _fingerprint(info: FileInformation) -> tuple[int, int, int, int, int]:
    return (
        info.volume_serial,
        int.from_bytes(info.file_id, "little"),
        info.last_write,
        info.change_time,
        info.size,
    )


def file_fingerprint(path: Path) -> tuple[int, int, int, int, int]:
    """Stable file ID and change time for a validated Windows file."""
    with _metadata(path) as info:
        return _fingerprint(info)


def stream_fingerprint(stream: BinaryIO) -> tuple[int, int, int, int, int]:
    """Read the same identity from an already validated open file."""
    import msvcrt

    info = _file_api().information(msvcrt.get_osfhandle(stream.fileno()))
    _validate_information(info, directory=False)
    return _fingerprint(info)


def checked_path(root: Path, relative: str) -> Path:
    """Validate an existing path or the existing prefix of a destination path."""
    root = _absolute(root)
    path = root.joinpath(*_relative(relative))
    api = _file_api()
    parent = Path(path.anchor)
    with _directory_chain(parent, api), ExitStack() as guards:
        for index, part in enumerate(path.parts[1:]):
            _component(part)
            _case_collision(parent, part)
            with os.scandir(parent) as entries:
                present = any(
                    _name_key(entry.name) == _name_key(part) for entry in entries
                )
            if not present:
                return path
            selected = parent / part
            directory = selected.is_dir()
            if not directory and index != len(path.parts) - 2:
                raise ValueError("Expected a directory in the validated path")
            handle = api.open(selected, directory=directory, metadata=not directory)
            guards.callback(api.close, handle)
            _validate_information(api.information(handle), directory=directory)
            parent = selected
    return path


def _unchanged(before: FileInformation, after: FileInformation, size: int) -> None:
    if before != after or after.size != size:
        raise ValueError("File identity or content changed while reading")


def file_identity(
    path: Path, check: Callable[[], None] = lambda: None, limit: int = 256 * 1024**2
) -> tuple[int, str]:
    digest, size = sha256(), 0
    with _open(path) as stream:
        before = stream.api.information(stream.handle)
        if before.size > limit:
            raise ValueError("File exceeds its declared byte budget")
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError("File grew beyond its declared byte budget")
            digest.update(chunk)
            check()
        _unchanged(before, stream.api.information(stream.handle), size)
    return size, digest.hexdigest()


def _catalogue_paths(paths: Iterator[str]) -> None:
    # Коллизии каталогов A/x и a/y тоже неоднозначны, хотя полные имена различны.
    components: dict[tuple[str, ...], str] = {}
    for relative in paths:
        prefix: tuple[str, ...] = ()
        for part in _relative(relative):
            prefix += (_name_key(part),)
            previous = components.setdefault(prefix, part)
            if previous != part:
                raise ValueError("Case-colliding package directory components")


def verify_package(
    package: NativeInputPackage, root: Path, check: Callable[[], None] = lambda: None
) -> None:
    _catalogue_paths(item.path for item in package.files)
    validate_directory(root)
    for item in package.files:
        check()
        if file_identity(package_file(root, item.path), check, item.size_bytes) != (
            item.size_bytes,
            item.sha256,
        ):
            raise ValueError(f"Package file changed: {item.path}")


def copy_independent(
    source: Path, destination: Path, check: Callable[[], None], limit: int
) -> None:
    with (
        _open(source) as reader,
        _open(destination, writable=True, create=True) as writer,
    ):
        before = reader.api.information(reader.handle)
        if before.size > limit:
            raise ValueError("File exceeds its declared byte budget")
        size = 0
        while chunk := reader.read(1024 * 1024):
            check()
            size += len(chunk)
            if size > limit:
                raise ValueError("Staged file grew beyond its declared byte budget")
            writer.write(chunk)
        writer.flush()
        _unchanged(before, reader.api.information(reader.handle), size)
        staged = writer.api.information(writer.handle)
        if (
            staged.links != 1
            or staged.size != size
            or staged.object_id == before.object_id
        ):
            raise ValueError("Worker staging did not produce an independent copy")


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Worker metadata contains a duplicate field")
        result[key] = value
    return result


def verify_worker(bundle: Path, config: NativeQueryProcessConfig) -> None:
    metadata = package_file(bundle, WORKER_METADATA)
    with _open(metadata) as stream:
        before = stream.api.information(stream.handle)
        if before.size > 16384:
            raise ValueError("Worker metadata exceeds limit")
        raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError("Worker metadata exceeds limit")
        _unchanged(before, stream.api.information(stream.handle), len(raw))
    info = json.loads(raw, object_pairs_hook=_unique_fields)
    required = {"schema", "target", "binary", "plugin_version", "sha256"}
    if (
        not isinstance(info, dict)
        or not required.issubset(info)
        or set(info) - required - {"face_protocols", "topology_shapes"}
        or info.get("schema") != WORKER_SCHEMA
        or info.get("target") != "windows-x86_64"
        or info.get("binary") != WORKER_BINARY
        or info.get("plugin_version") != config.plugin_version
        or info.get("sha256") != config.worker_binary_sha256
    ):
        raise ValueError("Expected the curated Windows query worker")
    valid_sha(info["sha256"])
    for key, expected in (
        ("face_protocols", config.face_protocols),
        ("topology_shapes", config.topology_shapes),
    ):
        values = info.get(key, [])
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)
            or tuple(values) != expected
        ):
            raise ValueError("Windows query worker capabilities do not match")
    if (
        file_identity(package_file(bundle, WORKER_BINARY))[1]
        != config.worker_binary_sha256
    ):
        raise ValueError("Curated worker binary SHA mismatch")


def stage_worker(bundle: Path, destination: Path, check: Callable[[], None]) -> None:
    api, total, count = _file_api(), 0, 0
    with _directory_chain(bundle, api):
        pending = [bundle]
        while pending:
            directory = pending.pop()
            with _directory_chain(directory, api):
                children = []
                with os.scandir(directory) as entries:
                    for entry in entries:
                        check()
                        count += 1
                        if count > 1024:
                            raise ValueError("Worker bundle has too many files")
                        children.append(directory / entry.name)
                names = [_name_key(child.name) for child in children]
                if len(set(names)) != len(names):
                    raise ValueError("Case-colliding worker file names")
                for child in children:
                    check()
                    relative = child.relative_to(bundle).as_posix()
                    _relative(relative)
                    # BACKUP_SEMANTICS позволяет проверить каталог без перехода по ссылке.
                    handle = api.open(child, metadata=True)
                    try:
                        info = api.information(handle)
                        is_directory = bool(info.attributes & FILE_ATTRIBUTE_DIRECTORY)
                        _validate_information(info, directory=is_directory)
                    finally:
                        api.close(handle)
                    if is_directory:
                        pending.append(child)
                        continue
                    total += info.size
                    if total > 256 * 1024**2:
                        raise ValueError("Worker bundle exceeds staging budget")
                    target = destination.joinpath(*_relative(relative))
                    copy_independent(child, target, check, info.size)
                    if file_identity(child, check) != file_identity(target, check):
                        raise ValueError("Worker bundle changed during staging")
