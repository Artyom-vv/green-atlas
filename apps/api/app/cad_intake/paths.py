import os
from pathlib import Path

from app.cad_import.cache import file_sha256
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.contracts import (
    CadDirectory,
    CadDirectoryEntry,
    CadFingerprint,
    CadRoot,
    relative_path,
)

DIRECTORY_SCAN_LIMIT = 500
DRAWING_EXTENSIONS = {".dxf", ".dwg"}


class CadDiscovery:
    def __init__(self, config: CadIntakeConfig) -> None:
        self.config = config

    def roots(self) -> list[CadRoot]:
        return [CadRoot(id=root.id, label=root.label) for root in self.config.roots]

    def resolve(self, root_id: str, path: str, *, drawing: bool = False) -> Path:
        root = self.config.root(root_id)
        safe = relative_path(path)
        try:
            resolved = (root.path / safe).resolve(strict=True)
            if not resolved.is_relative_to(root.path):
                raise ValueError("Путь выходит за пределы разрешённого каталога")
            if drawing and (
                not resolved.is_file()
                or resolved.suffix.lower() not in DRAWING_EXTENSIONS
            ):
                raise ValueError("Выберите чертёж DWG или DXF")
            return resolved
        except OSError as error:
            raise ValueError("Путь недоступен в разрешённом каталоге") from error

    def directory(self, root_id: str, path: str = ".") -> CadDirectory:
        folder = self.resolve(root_id, path)
        if not folder.is_dir():
            raise ValueError("Выберите каталог комплекта")
        root = self.config.root(root_id)
        entries: list[CadDirectoryEntry] = []
        truncated = False
        with os.scandir(folder) as iterator:
            for index, item in enumerate(iterator):
                if index >= DIRECTORY_SCAN_LIMIT:
                    truncated = True
                    break
                # Do not list links, including Windows directory junctions.
                resolved = Path(item.path).resolve()
                if item.is_symlink() or not resolved.is_relative_to(root.path):
                    continue
                if item.is_dir(follow_symlinks=False):
                    entries.append(
                        CadDirectoryEntry(
                            path=resolved.relative_to(root.path).as_posix(),
                            name=item.name,
                            kind="directory",
                        )
                    )
                elif (
                    item.is_file(follow_symlinks=False)
                    and resolved.suffix.lower() in DRAWING_EXTENSIONS
                ):
                    entries.append(
                        CadDirectoryEntry(
                            path=resolved.relative_to(root.path).as_posix(),
                            name=item.name,
                            kind="drawing",
                            bytes=item.stat().st_size,
                        )
                    )
        entries.sort(
            key=lambda entry: (entry.kind != "directory", entry.name.casefold())
        )
        return CadDirectory(
            root_id=root_id,
            path=relative_path(path),
            entries=entries,
            truncated=truncated,
        )

    def fingerprint(self, root_id: str, path: str) -> CadFingerprint:
        source = self.resolve(root_id, path, drawing=True)
        before = source.stat()
        source_budget = (
            self.config.policy.max_output_bytes
            if source.suffix.lower() == ".dxf"
            else self.config.policy.max_source_bytes
        )
        if before.st_size > source_budget:
            raise ValueError("Чертёж превышает бюджет исходного CAD-файла")
        digest = file_sha256(source)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Чертёж изменился во время проверки")
        return CadFingerprint(
            root_id=root_id,
            path=relative_path(path),
            sha256=digest,
            bytes=after.st_size,
        )
