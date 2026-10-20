"""Хранение файлов восстановления без потерь с проверкой хешей и устойчивой записью на
диск.
"""

from __future__ import annotations

import gzip
import os
from hashlib import sha256
from pathlib import Path
from typing import TypedDict

BACKUP_COPY_CHUNK_BYTES = 1024 * 1024
BACKUP_COMPRESSION_THRESHOLD_BYTES = 128 * 1024 * 1024
BACKUP_COMPRESSION_LEVEL = 1


class BackupFile(TypedDict):
    path: str
    original_path: str
    encoding: str
    bytes: int
    sha256: str
    stored_sha256: str


def sync_directory(path: Path) -> None:
    # Windows не предоставляет дескриптор каталога через os.open.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(BACKUP_COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_backup_file(
    source: Path, root: Path, relative: Path, *, compress: bool = True
) -> BackupFile:
    """Сжимает большой файл с ограниченным расходом памяти, сохраняя исходник."""
    compressed = (
        compress and source.stat().st_size >= BACKUP_COMPRESSION_THRESHOLD_BYTES
    )
    target = root / relative
    if compressed:
        target = target.with_name(target.name + ".gz")
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    size = 0
    if source != target:
        with source.open("rb") as reader, target.open("wb") as output:
            if compressed:
                # Детерминированный заголовок не публикует временные метки исходника.
                with gzip.GzipFile(
                    fileobj=output,
                    mode="wb",
                    filename="",
                    mtime=0,
                    compresslevel=BACKUP_COMPRESSION_LEVEL,
                ) as writer:
                    for chunk in iter(
                        lambda: reader.read(BACKUP_COPY_CHUNK_BYTES), b""
                    ):
                        writer.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
            else:
                for chunk in iter(lambda: reader.read(BACKUP_COPY_CHUNK_BYTES), b""):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        expected = digest.hexdigest()
    else:
        size = source.stat().st_size
        expected = file_digest(source)
    if compressed:
        # Проверяем распаковку и CRC до удаления временной SQLite-копии
        # или публикации точки восстановления.
        verified = sha256()
        with gzip.open(target, "rb") as reader:
            for chunk in iter(lambda: reader.read(BACKUP_COPY_CHUNK_BYTES), b""):
                verified.update(chunk)
        if verified.hexdigest() != expected:
            raise ValueError("Не удалось проверить сжатую резервную копию")
    return {
        "path": str(target.relative_to(root)),
        "original_path": str(relative),
        "encoding": "gzip" if compressed else "identity",
        "bytes": size,
        "sha256": expected,
        "stored_sha256": file_digest(target),
    }
