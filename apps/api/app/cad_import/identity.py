"""Fingerprint the configured native converter and its adjacent runtime."""

import os
import subprocess
from pathlib import Path

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError, ConverterIdentity

VERSION_TIMEOUT_SECONDS = 10


def converter_identity(executable: Path) -> ConverterIdentity:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        response = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            timeout=VERSION_TIMEOUT_SECONDS,
            creationflags=flags,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.SubprocessError as error:
        raise CadConversionError(
            f"Не удалось определить версию CAD-конвертера: {error}"
        ) from error
    lines = response.stdout.strip().splitlines()
    if not lines or "dwg2dxf" not in lines[0].lower():
        raise CadConversionError("Ожидался исполняемый файл LibreDWG dwg2dxf")
    return ConverterIdentity(
        name="libredwg",
        version=lines[0],
        executable_sha256=file_sha256(executable),
        runtime_sha256={
            p.name: file_sha256(p) for p in sorted(executable.parent.glob("*.dll"))
        },
    )
