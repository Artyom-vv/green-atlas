"""Файлы Core-задания: проверка SHA, независимые копии и доверенный Mac worker.

Этот владелец сохраняет POSIX NOFOLLOW/stat/inode правила. Windows-дескриптор
установки не заменяет проверенный файловый адаптер исполнения.

package_file принимает только канонический относительный путь, уже проверенный
NativeInputPackage, или фиксированное имя файла задания. Проверка произвольного
пользовательского пути принадлежит вызывающей границе.
"""

from __future__ import annotations

import os
import plistlib
import stat
import sys
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from app.native_query.transport.process_contracts import (
    NativeInputPackage,
    NativeQueryProcessConfig,
)

WORKER_ID = "ru.green-atlas.native-query-worker"
WORKER_BINARY = "Contents/MacOS/GreenAtlasBridge"


def package_file(root: Path, relative: str) -> Path:
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        return windows_job_files.package_file(root, relative)
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Symlink is not a package file: {relative}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Not a regular file: {relative}")
    return path


def checked_path(root: Path, relative: str) -> Path:
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        return windows_job_files.checked_path(root, relative)
    return root / relative


def open_regular(path: Path, *, writable: bool = False) -> BinaryIO:
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        return windows_job_files.open_regular(path, writable=writable)
    if sys.platform == "win32":
        raise OSError("Windows файл должен открываться через нативный адаптер")
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or (writable and info.st_nlink != 1):
        os.close(fd)
        raise ValueError("Expected an independent regular file")
    return os.fdopen(fd, "r+b" if writable else "rb")


def file_identity(
    path: Path, check: Callable[[], None] = lambda: None, limit: int = 256 * 1024**2
) -> tuple[int, str]:
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        return windows_job_files.file_identity(path, check, limit)
    digest, size = sha256(), 0
    with open_regular(path) as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            raise ValueError("File exceeds its declared byte budget")
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError("File grew beyond its declared byte budget")
            digest.update(chunk)
            check()
    return size, digest.hexdigest()


def verify_package(
    package: NativeInputPackage, root: Path, check: Callable[[], None] = lambda: None
) -> None:
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        windows_job_files.verify_package(package, root, check)
        return
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
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        windows_job_files.copy_independent(source, destination, check, limit)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open_regular(source) as reader, destination.open("xb") as writer:
        size = 0
        while chunk := reader.read(1024 * 1024):
            check()
            size += len(chunk)
            if size > limit:
                raise ValueError("Staged file grew beyond its declared byte budget")
            writer.write(chunk)
    # Побайтовая копия создаёт независимый inode, даже если у исходника есть ссылки.
    if destination.stat().st_nlink != 1 or os.path.samestat(
        source.stat(), destination.stat()
    ):
        raise ValueError("Worker staging did not produce an independent copy")


def verify_worker(bundle: Path, config: NativeQueryProcessConfig) -> None:
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        windows_job_files.verify_worker(bundle, config)
        return
    info_path = package_file(bundle, "Contents/Info.plist")
    if info_path.stat().st_size > 64 * 1024:
        raise ValueError("Worker metadata exceeds limit")
    with open_regular(info_path) as stream:
        info = plistlib.load(stream)
    if (
        not isinstance(info, dict)
        or info.get("CFBundleIdentifier") != WORKER_ID
        or info.get("CFBundleExecutable") != "GreenAtlasBridge"
        or info.get("CFBundleShortVersionString") != config.plugin_version
        or tuple(info.get("GAFaceProtocols", ())) != config.face_protocols
        or tuple(info.get("GATopologyShapes", ())) != config.topology_shapes
    ):
        raise ValueError("Expected the curated query worker, not the GUI plugin")
    if (
        file_identity(package_file(bundle, WORKER_BINARY))[1]
        != config.worker_binary_sha256
    ):
        raise ValueError("Curated worker binary SHA mismatch")


def stage_worker(bundle: Path, destination: Path, check: Callable[[], None]) -> None:
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        windows_job_files.stage_worker(bundle, destination, check)
        return
    total = 0
    for index, source in enumerate(bundle.rglob("*")):
        check()
        if index >= 1024 or source.is_symlink():
            raise ValueError("Worker bundle has too many files or a symlink")
        if source.is_dir():
            continue
        source = package_file(bundle, source.relative_to(bundle).as_posix())
        total += source.stat().st_size
        if total > 256 * 1024**2:
            raise ValueError("Worker bundle exceeds staging budget")
        target = destination / source.relative_to(bundle)
        copy_independent(source, target, check, source.stat().st_size)
        target.chmod(stat.S_IMODE(source.stat().st_mode) & 0o777)
        if file_identity(source, check) != file_identity(target, check):
            raise ValueError("Worker bundle changed during staging")


def _windows_platform() -> bool:
    return os.name == "nt"


def validate_directory(path: Path) -> None:
    """Проверить исходный путь до разрешения ссылок и подготовки задания."""
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        windows_job_files.validate_directory(path)
        return
    if not path.is_dir() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("Job directory is unavailable or behind a symbolic link")


def worker_destination(job: Path) -> Path:
    return job / ("QueryWorker" if _windows_platform() else "QueryWorker.dbx")


def prepare_job_root(path: Path) -> None:
    """Создать отдельный корень задания без перехода через Windows reparse."""
    if _windows_platform():
        from app.native_query.transport import windows_job_files

        windows_job_files.prepare_directory(path)
        return
    path.mkdir(parents=True, exist_ok=True)
