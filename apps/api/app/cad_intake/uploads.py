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
from app.dxf_import.limits import MAX_CAD_SNAPSHOT_BYTES

MAX_CAD_DRAWINGS = 64
MAX_CAD_UPLOAD_FILES = MAX_CAD_DRAWINGS * 2
MAX_CAD_UPLOAD_TOTAL_BYTES = 1024 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024
SNAPSHOT_SUFFIX = ".dxf.green-atlas.snapshot.json"


def _filename(upload: UploadFile) -> str:
    value = (upload.filename or "").replace("\\", "/")
    path = PurePosixPath(value)
    if (
        not value
        or len(path.parts) != 1
        or path.name in {"", ".", ".."}
        or not (
            path.suffix.casefold() == ".dxf"
            or path.name.casefold().endswith(SNAPSHOT_SUFFIX)
        )
        or "\x00" in value
    ):
        raise ValueError(
            "Комплект может содержать только DXF и их AutoCAD snapshot "
            "без вложенных путей"
        )
    return path.name


async def store_uploaded_package(
    config: CadIntakeConfig, uploads: list[UploadFile]
) -> CadUploadPackage:
    if not uploads or len(uploads) > MAX_CAD_UPLOAD_FILES:
        raise ValueError(
            f"Выберите до {MAX_CAD_DRAWINGS} DXF вместе с AutoCAD snapshot"
        )
    names = [_filename(upload) for upload in uploads]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Имена файлов в комплекте должны быть уникальны")
    drawing_names = [name for name in names if name.casefold().endswith(".dxf")]
    snapshot_names = [
        name for name in names if name.casefold().endswith(SNAPSHOT_SUFFIX)
    ]
    if not drawing_names or len(drawing_names) > MAX_CAD_DRAWINGS:
        raise ValueError(f"Выберите от 1 до {MAX_CAD_DRAWINGS} DXF")
    expected_snapshots = {
        f"{name}{SNAPSHOT_SUFFIX[len('.dxf') :]}".casefold() for name in drawing_names
    }
    actual_snapshots = {name.casefold() for name in snapshot_names}
    if actual_snapshots != expected_snapshots:
        missing = sorted(expected_snapshots - actual_snapshots)
        orphaned = sorted(actual_snapshots - expected_snapshots)
        details = [
            *(f"нет {name}" for name in missing),
            *(f"без DXF: {name}" for name in orphaned),
        ]
        raise ValueError(
            "Для каждого DXF нужен точный AutoCAD snapshot: " + "; ".join(details)
        )

    package_id = f"upload-{uuid4().hex}"
    upload_root = config.storage / "uploads"
    upload_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=upload_root))
    destination = upload_root / package_id
    entries: list[CadFingerprint] = []
    snapshot_entries: list[CadFingerprint] = []
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
                    limit = (
                        MAX_CAD_SNAPSHOT_BYTES
                        if name.casefold().endswith(SNAPSHOT_SUFFIX)
                        else PREPARE_POLICY.max_source_bytes
                    )
                    if size > limit:
                        raise ValueError(
                            f"Файл «{name}» должен быть не больше "
                            f"{limit // 1024 // 1024} МБ"
                        )
                    if total > MAX_CAD_UPLOAD_TOTAL_BYTES:
                        raise ValueError("Комплект DXF должен быть не больше 1 ГБ")
                    digest.update(chunk)
                    stream.write(chunk)
            if size == 0:
                raise ValueError(f"DXF «{name}» пуст")
            fingerprint = CadFingerprint(
                root_id=package_id,
                path=name,
                sha256=digest.hexdigest(),
                bytes=size,
            )
            (
                snapshot_entries
                if name.casefold().endswith(SNAPSHOT_SUFFIX)
                else entries
            ).append(fingerprint)

        package = CadUploadPackage(
            root_id=package_id,
            entries=entries,
            total_bytes=total,
            snapshots=snapshot_entries,
        )
        (staging / "upload.json").write_text(
            package.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(destination)
        return package
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
