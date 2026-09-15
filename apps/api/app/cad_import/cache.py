"""Content-addressed conversion outputs; damaged or obsolete entries are misses."""

import hashlib
import os
from pathlib import Path

from app.cad_import.contracts import CadConversion, ConvertedDrawing, ConverterIdentity

CONVERSION_CACHE_REVISION = "cad-conversion-v2"


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cache_folder(root: Path, source_hash: str, identity: ConverterIdentity) -> Path:
    key = hashlib.sha256(
        f"{CONVERSION_CACHE_REVISION}:{source_hash}:{identity.model_dump_json()}".encode()
    ).hexdigest()
    return root / key


def read_cached(
    folder: Path, source: Path, digest: str, max_bytes: int
) -> ConvertedDrawing | None:
    manifest = folder / "conversion.json"
    try:
        evidence = CadConversion.model_validate_json(
            manifest.read_text(encoding="utf-8")
        )
        if len(evidence.output_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in evidence.output_sha256
        ):
            return None
        output = folder / f"{evidence.output_sha256}.dxf"
        if not 0 < output.stat().st_size == evidence.output_bytes <= max_bytes:
            return None
        if (
            evidence.source_sha256 != digest
            or file_sha256(output) != evidence.output_sha256
        ):
            return None
    except (OSError, ValueError):
        return None
    immutable_manifest = folder / f"{evidence.output_sha256}.conversion.json"
    if not immutable_manifest.exists():
        try:
            os.link(manifest, immutable_manifest)
        except FileExistsError:
            pass
    if (
        not immutable_manifest.is_file()
        or immutable_manifest.read_bytes() != manifest.read_bytes()
    ):
        return None
    return ConvertedDrawing(
        path=output,
        evidence_path=immutable_manifest,
        evidence=evidence.model_copy(update={"source_name": source.name}),
        cache_hit=True,
    )


def publish(folder: Path, work: Path, evidence: CadConversion) -> ConvertedDrawing:
    # Concurrent runs can have different CAD timestamps; manifests always
    # reference immutable output content, so runs cannot mix their bytes.
    target = folder / f"{evidence.output_sha256}.dxf"
    (work / "drawing.dxf").replace(target)
    (work / "converter.log").replace(folder / f"{evidence.output_sha256}.log")
    pending = work / "conversion.json"
    pending.write_text(evidence.model_dump_json(indent=2) + "\n", encoding="utf-8")
    immutable_manifest = folder / f"{evidence.output_sha256}.conversion.json"
    try:
        # A hard link publishes the complete file atomically, without replacing
        # evidence already referenced by another package or concurrent caller.
        os.link(pending, immutable_manifest)
    except FileExistsError:
        evidence = CadConversion.model_validate_json(
            immutable_manifest.read_text(encoding="utf-8")
        )
        pending.unlink()
        pending.write_bytes(immutable_manifest.read_bytes())
    manifest = folder / "conversion.json"
    pending.replace(manifest)
    return ConvertedDrawing(
        path=target, evidence_path=immutable_manifest, evidence=evidence
    )
