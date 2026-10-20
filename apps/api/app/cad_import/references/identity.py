"""Отпечаток настроенного CAD-конвертера и его библиотек для воспроизводимости прежних
результатов.
"""

import os
import subprocess
from pathlib import Path

from app.cad_import.archive.cache import file_sha256
from app.cad_import.contracts import CadConversionError, ConverterIdentity
from app.shared.child_dll_search import child_dll_search

VERSION_TIMEOUT_SECONDS = 10


def converter_identity(executable: Path) -> ConverterIdentity:
    flags = getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0
    child = None
    try:
        with child_dll_search(external=True):
            child = subprocess.Popen(
                [str(executable), "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
                encoding="utf-8",
                errors="replace",
            )
        stdout, stderr = child.communicate(timeout=VERSION_TIMEOUT_SECONDS)
        if child.returncode:
            raise subprocess.CalledProcessError(
                child.returncode, child.args, stdout, stderr
            )
    except subprocess.SubprocessError as error:
        raise CadConversionError(
            f"Не удалось определить версию CAD-конвертера: {error}"
        ) from error
    finally:
        # Завершение после ошибки восстановления DLL-поиска происходит вне lock.
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate()
    lines = stdout.strip().splitlines()
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
