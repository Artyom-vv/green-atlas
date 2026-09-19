"""Read an explicitly selected AutoCAD snapshot without escaping its CAD root."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from app.cad_bridge import CadSnapshot
from app.cad_import.cache import file_sha256
from app.cad_intake.prepare_contracts import CadSnapshotSelection
from app.dxf_import.limits import MAX_CAD_SNAPSHOT_BYTES


@dataclass(frozen=True)
class CadSnapshotSource:
    path: Path
    content: bytes
    sha256: str


def _snapshot_path(root: Path, relative: str) -> Path:
    root = root.resolve(strict=True)
    unresolved = root / relative
    if unresolved.is_symlink():
        raise ValueError("CAD snapshot не должен быть символической ссылкой")
    try:
        path = unresolved.resolve(strict=True)
    except OSError as error:
        raise ValueError("CAD snapshot недоступен в разрешённом каталоге") from error
    if (
        not path.is_relative_to(root)
        or not path.is_file()
        or path.suffix.lower() != ".json"
    ):
        raise ValueError("Выберите JSON snapshot внутри CAD-комплекта")
    return path


def adjacent_cad_snapshot_path(drawing_path: str) -> str:
    """Stable bridge output name; no directory scan or heuristic matching."""

    return f"{drawing_path}.green-atlas.snapshot.json"


def discover_adjacent_cad_snapshot(
    root: Path, drawing_path: str
) -> CadSnapshotSelection | None:
    relative = adjacent_cad_snapshot_path(drawing_path)
    unresolved = root / relative
    if not unresolved.exists():
        return None
    path = _snapshot_path(root, relative)
    before = path.stat()
    if not 0 < before.st_size <= MAX_CAD_SNAPSHOT_BYTES:
        raise ValueError(
            f"CAD snapshot должен быть не больше {MAX_CAD_SNAPSHOT_BYTES // 1024 // 1024} МБ"
        )
    digest = file_sha256(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("CAD snapshot изменился во время проверки")
    return CadSnapshotSelection(path=relative, sha256=digest)


def verify_cad_snapshot_fingerprint(
    root: Path, selection: CadSnapshotSelection
) -> str:
    """Recheck immutable sidecar evidence without loading its payload again."""

    path = _snapshot_path(root, selection.path)
    before = path.stat()
    if not 0 < before.st_size <= MAX_CAD_SNAPSHOT_BYTES:
        raise ValueError(
            f"CAD snapshot должен быть не больше {MAX_CAD_SNAPSHOT_BYTES // 1024 // 1024} МБ"
        )
    digest = file_sha256(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("CAD snapshot изменился во время проверки")
    if digest != selection.sha256:
        raise ValueError("CAD snapshot изменился после выбора")
    return digest


def resolve_cad_snapshot(
    root: Path, selection: CadSnapshotSelection
) -> CadSnapshotSource:
    path = _snapshot_path(root, selection.path)
    before = path.stat()
    with path.open("rb") as stream:
        content = stream.read(MAX_CAD_SNAPSHOT_BYTES + 1)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("CAD snapshot изменился во время чтения")
    if not content or len(content) > MAX_CAD_SNAPSHOT_BYTES:
        raise ValueError(
            f"CAD snapshot должен быть не больше {MAX_CAD_SNAPSHOT_BYTES // 1024 // 1024} МБ"
        )
    digest = sha256(content).hexdigest()
    if digest != selection.sha256:
        raise ValueError("CAD snapshot изменился после выбора")
    return CadSnapshotSource(path=path, content=content, sha256=digest)


def verify_cad_snapshot_dependencies(
    root: Path, snapshot: CadSnapshot
) -> dict[str, str]:
    """Bind every native XREF geometry contribution to package-local bytes."""

    canonical_root = root.resolve(strict=True)
    verified: dict[str, str] = {}
    for dependency in snapshot.dependencies or []:
        unresolved = canonical_root / dependency.path
        if unresolved.is_symlink():
            raise ValueError("XREF из CAD snapshot не должен быть символической ссылкой")
        try:
            path = unresolved.resolve(strict=True)
        except OSError as error:
            raise ValueError("XREF из CAD snapshot отсутствует в комплекте") from error
        if (
            not path.is_relative_to(canonical_root)
            or not path.is_file()
            or path.suffix.lower() != ".dxf"
        ):
            raise ValueError("XREF из CAD snapshot находится вне DXF-комплекта")
        before = path.stat()
        digest = file_sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("XREF изменился во время проверки CAD snapshot")
        if before.st_size != dependency.bytes or digest != dependency.sha256:
            raise ValueError("XREF изменился после создания CAD snapshot")
        verified[dependency.path] = digest
    return verified
