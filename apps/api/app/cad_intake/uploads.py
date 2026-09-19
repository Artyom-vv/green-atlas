"""Bounded immutable upload storage for DXF packages entering CAD intake."""

from __future__ import annotations

import shutil
import tempfile
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import uuid4

from fastapi import UploadFile

from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.contracts import CadFingerprint, CadUploadPackage
from app.cad_intake.prepare_policy import PREPARE_POLICY

MAX_CAD_UPLOAD_FILES = 64
MAX_CAD_UPLOAD_TOTAL_BYTES = 1024 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


def _filename(upload: UploadFile) -> str:
    value = (upload.filename or "").replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or len(path.parts) != 1
        or path.name in {"", ".", ".."}
        or path.suffix.casefold() != ".dxf"
        or "\x00" in value
    ):
        raise ValueError("Комплект может содержать только DXF без вложенных путей")
    return path.name


async def store_uploaded_package(
    config: CadIntakeConfig, uploads: list[UploadFile]
) -> CadUploadPackage:
    if not uploads or len(uploads) > MAX_CAD_UPLOAD_FILES:
        raise ValueError(
            f"Выберите от 1 до {MAX_CAD_UPLOAD_FILES} самостоятельных DXF"
        )
    names = [_filename(upload) for upload in uploads]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Имена DXF в комплекте должны быть уникальны")

    package_id = f"upload-{uuid4().hex}"
    upload_root = config.storage / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=upload_root))
    destination = upload_root / package_id
    entries: list[CadFingerprint] = []
    total = 0
    try:
        for upload, name in zip(uploads, names, strict=True):
            digest = sha256()
            size = 0
            target = staging / name
            with target.open("xb") as stream:
                while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
                    size += len(chunk)
                    total += len(chunk)
                    if size > PREPARE_POLICY.max_source_bytes:
                        raise ValueError(
                            "Один DXF должен быть не больше "
                            f"{PREPARE_POLICY.max_source_bytes // 1024 // 1024} МБ"
                        )
                    if total > MAX_CAD_UPLOAD_TOTAL_BYTES:
                        raise ValueError("Комплект DXF должен быть не больше 1 ГБ")
                    digest.update(chunk)
                    stream.write(chunk)
            if size == 0:
                raise ValueError(f"DXF «{name}» пуст")
            entries.append(
                CadFingerprint(
                    root_id=package_id,
                    path=name,
                    sha256=digest.hexdigest(),
                    bytes=size,
                )
            )

        package = CadUploadPackage(
            root_id=package_id, entries=entries, total_bytes=total
        )
        (staging / "upload.json").write_text(
            package.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(destination)
        return package
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
