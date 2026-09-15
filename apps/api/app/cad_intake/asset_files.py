"""Verify and stream the same descriptor; never materialize the DXF in memory."""

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from app.cad_intake.asset_contracts import CadAssetUnavailable
from app.cad_intake.asset_sources import CadAssetSource

ASSET_CHUNK_BYTES = 64 * 1024
BINARY_DXF_SIGNATURE = b"AutoCAD Binary DXF\r\n\x1a\x00"


def _identity(stream: BinaryIO) -> tuple[int, int, int, int]:
    value = os.fstat(stream.fileno())
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _open_verified(path: Path, digest: str, size: int) -> BinaryIO:
    stream = path.open("rb")
    try:
        before = _identity(stream)
        if (
            before[2] != size
            or hashlib.file_digest(stream, "sha256").hexdigest() != digest
        ):
            raise CadAssetUnavailable()
        if _identity(stream) != before:
            raise CadAssetUnavailable()
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def open_asset(source: CadAssetSource) -> BinaryIO:
    assert source.drawing.source_sha256 is not None
    assert source.drawing.normalized_sha256 is not None
    if source.original != source.asset:
        with _open_verified(
            source.original, source.drawing.source_sha256, source.drawing.source_bytes
        ):
            pass
    stream = _open_verified(
        source.asset, source.drawing.normalized_sha256, source.asset_bytes
    )
    try:
        if stream.read(len(BINARY_DXF_SIGNATURE)) == BINARY_DXF_SIGNATURE:
            raise CadAssetUnavailable()
        stream.seek(0)
        return stream
    except BaseException:
        stream.close()
        raise


def asset_chunks(stream: BinaryIO) -> Iterator[bytes]:
    try:
        while chunk := stream.read(ASSET_CHUNK_BYTES):
            yield chunk
    finally:
        stream.close()
